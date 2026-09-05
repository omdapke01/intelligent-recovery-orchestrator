"""Tests for Phase 7: Financial Safety Boundary (Policy Engine) and Immutable Audit Trail.

Verifies:
1. AI cannot override policy: AI recommendation with 0.99 confidence denied by policy.
2. Fail-closed on missing/corrupt policy data.
3. Max retry count hard limit enforcement.
4. Recovery window SLA expiration denial.
5. Permitted strategies policy constraint.
6. Permitted payment methods policy constraint.
7. Prohibited recovery situations (fraud, chargebacks, sanctions).
8. Automated amount cap: High-value transaction diverted to human approval (ESCALATED).
9. Terminal workflow lock (cannot revive succeeded or stopped recovery).
10. Application-layer immutability: UPDATE and DELETE operations raise ImmutableAuditViolationError.
11. Cryptographic SHA-256 hash chaining and tamper detection.
12. Execution boundary policy re-validation under Redis lock.
13. Two-stage layered safety: Phase 3 Logical Guard + Phase 7 Financial Policy.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import ImmutableAuditRecord
from app.audit.service import (
    ImmutableAuditLogger,
    ImmutableAuditViolationError,
    canonical_json_serialize,
    compute_audit_payload_hash,
    verify_chain_integrity,
)
from app.events.broker import InMemoryEventBroker
from app.events.schemas import PaymentFailedPayload
from app.execution.provider import MockPaymentProvider
from app.execution.redis_client import InMemoryRedisClient
from app.execution.retry_policy import RecoveryRetryPolicy
from app.execution.service import ExecutionStatus, SafeRecoveryExecutionService
from app.models import (
    Customer,
    Merchant,
    MerchantTier,
    Payment,
    PaymentLifecycleState,
    PaymentMethod,
    PaymentRoute,
    RecoveryCase,
    RecoveryState,
    RecoveryStrategy,
    RetryabilityClass,
    RouteStatus,
)
from app.models.enums import FailureCategory
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.orchestrator.orchestrator import IntelligentRecoveryOrchestrator
from app.policy.engine import FinancialSafetyPolicyEngine
from app.policy.models import PolicyDecision, RecoveryPolicyConfig


def make_context(
    amount_inr: Decimal = Decimal("1500.00"),
    payment_method: PaymentMethod = PaymentMethod.UPI,
    attempt_number: int = 1,
    error_code: str = "GATEWAY_TIMEOUT",
    merchant_recovery_enabled: bool = True,
    merchant_max_retries: int = 2,
    failure_age_seconds: int = 10,
) -> PaymentRecoveryContext:
    now = datetime.now(timezone.utc)
    return PaymentRecoveryContext(
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=amount_inr,
        payment_method=payment_method,
        route_id="ROUTE_UPI_HDFC",
        route_health_score=0.98,
        route_is_active=True,
        route_status=RouteStatus.HEALTHY,
        failure_category=FailureCategory.TRANSIENT,
        error_code=error_code,
        reason=f"Failure reason for {error_code}",
        attempt_number=attempt_number,
        failure_created_at=now - timedelta(seconds=failure_age_seconds),
        merchant_tier=MerchantTier.GROWTH,
        merchant_recovery_enabled=merchant_recovery_enabled,
        merchant_max_auto_retries=merchant_max_retries,
        merchant_min_recovery_amount_inr=Decimal("50.00"),
        merchant_auto_escalate_threshold_inr=Decimal("50000.00"),
        correlation_id=f"corr_{uuid.uuid4().hex[:8]}",
    )


def make_plan(
    strategy: RecoveryStrategy = RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
    confidence: float = 0.95,
) -> RecoveryPlan:
    return RecoveryPlan(
        strategy=strategy,
        retryability=RetryabilityClass.RETRYABLE,
        target_route_id="ROUTE_UPI_HDFC",
        suggested_backoff_sec=2.0,
        decision_confidence=confidence,
        explanation="Test recovery plan",
        parameters={"tier_used": "AI_REASONING"},
    )


# -----------------------------------------------------------------------------
# 1. AI Cannot Override Policy
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ai_cannot_override_policy_max_retries():
    """Invariant: Even if AI has 0.99 confidence, policy denies when attempt count exceeds limit."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context(attempt_number=3, merchant_max_retries=2)
    plan = make_plan(confidence=0.99)

    res = engine.evaluate(context=ctx, plan=plan)

    assert res.decision == PolicyDecision.DENIED
    assert "MAX_RETRY_COUNT_POLICY" in res.violated_policies
    assert "MAX_RETRY_COUNT_EXCEEDED" in res.reason


# -----------------------------------------------------------------------------
# 2. Fail-Closed Principle
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_fail_closed_on_missing_context():
    """Invariant: Missing critical policy fields strictly results in DENIED."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context()
    ctx.amount_inr = Decimal("0.00")  # Invalid amount
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "FAIL_CLOSED_DATA_INTEGRITY_POLICY" in res.violated_policies
    assert "POLICY_DATA_UNAVAILABLE" in res.reason


# -----------------------------------------------------------------------------
# 3. Terminal Workflow Lock
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_terminal_workflow_locked():
    """Invariant: Workflows already SUCCEEDED or STOPPED cannot be re-executed."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context()
    plan = make_plan()

    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=ctx.payment_id,
        recovery_state=RecoveryState.RECOVERY_SUCCEEDED,
    )

    res = engine.evaluate(context=ctx, plan=plan, recovery_case=case)
    assert res.decision == PolicyDecision.DENIED
    assert "TERMINAL_WORKFLOW_LOCK_POLICY" in res.violated_policies
    assert "TERMINAL_WORKFLOW_LOCKED" in res.reason


# -----------------------------------------------------------------------------
# 4. Prohibited Situations (Fraud / Chargeback / Sanctions)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [
    "FRAUD_SUSPECTED",
    "SANCTION_VIOLATION",
    "CARD_BLOCKED",
    "RISK_REJECTED",
    "ACCOUNT_BLOCKED",
])
async def test_policy_prohibited_situations_denied(error_code: str):
    """Invariant: Prohibited fraud, legal, and sanction situations are strictly denied."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context(error_code=error_code)
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "PROHIBITED_RECOVERY_SITUATION_POLICY" in res.violated_policies
    assert "PROHIBITED_RECOVERY_SITUATION" in res.reason


# -----------------------------------------------------------------------------
# 5. Merchant Restrictions (Disabled / Min Amount)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_merchant_recovery_disabled():
    """Invariant: Merchant with disabled recovery is strictly denied."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context(merchant_recovery_enabled=False)
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "MERCHANT_RESTRICTIONS_POLICY" in res.violated_policies
    assert "MERCHANT_RESTRICTION" in res.reason


@pytest.mark.asyncio
async def test_policy_amount_below_min_recovery():
    """Invariant: Transactions below minimum recovery amount are denied."""
    engine = FinancialSafetyPolicyEngine()
    ctx = make_context(amount_inr=Decimal("20.00"))  # min is 50.00
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "MERCHANT_RESTRICTIONS_POLICY" in res.violated_policies
    assert "MERCHANT_RESTRICTION" in res.reason


# -----------------------------------------------------------------------------
# 6. Recovery Window SLA Expiration
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_recovery_window_sla_expired():
    """Invariant: Recovery attempted after SLA window expires is denied."""
    config = RecoveryPolicyConfig(max_window_sec_by_method={PaymentMethod.UPI: 300.0})
    engine = FinancialSafetyPolicyEngine(config=config)
    # Failure created 600s ago (10 minutes ago)
    ctx = make_context(failure_age_seconds=600)
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "MAX_RECOVERY_WINDOW_SLA_POLICY" in res.violated_policies
    assert "MAX_RECOVERY_WINDOW_EXCEEDED" in res.reason


# -----------------------------------------------------------------------------
# 7. Permitted Strategies Policy
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_unpermitted_strategy_denied():
    """Invariant: Unpermitted strategy is denied by policy."""
    config = RecoveryPolicyConfig(
        permitted_strategies={RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF}
    )
    engine = FinancialSafetyPolicyEngine(config=config)
    ctx = make_context()
    plan = make_plan(strategy=RecoveryStrategy.NOTIFY_CUSTOMER_LINK)

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "PERMITTED_STRATEGIES_POLICY" in res.violated_policies
    assert "UNAUTHORIZED_STRATEGY" in res.reason


# -----------------------------------------------------------------------------
# 8. Permitted Payment Methods Policy
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_unpermitted_payment_method_denied():
    """Invariant: Unsupported or unpermitted payment method is denied."""
    config = RecoveryPolicyConfig(
        permitted_retry_methods={PaymentMethod.UPI, PaymentMethod.CREDIT_CARD}
    )
    engine = FinancialSafetyPolicyEngine(config=config)
    ctx = make_context(payment_method=PaymentMethod.NETBANKING)
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.DENIED
    assert "PERMITTED_PAYMENT_METHODS_POLICY" in res.violated_policies
    assert "PAYMENT_METHOD_NOT_SUPPORTED" in res.reason


# -----------------------------------------------------------------------------
# 9. Automated Amount Cap / Human Approval
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_policy_automated_amount_cap_requires_human_approval():
    """Invariant: Transactions exceeding auto-recovery cap require human review."""
    config = RecoveryPolicyConfig(
        system_max_recovery_amount_inr=Decimal("100000.00")
    )
    engine = FinancialSafetyPolicyEngine(config=config)
    ctx = make_context(amount_inr=Decimal("250000.00"))  # 2.5 Lakhs
    plan = make_plan()

    res = engine.evaluate(context=ctx, plan=plan)
    assert res.decision == PolicyDecision.REQUIRES_HUMAN_APPROVAL
    assert "AUTOMATED_AMOUNT_CAP_POLICY" in res.violated_policies
    assert "HIGH_VALUE_TRANSACTION" in res.reason


# -----------------------------------------------------------------------------
# 10. Application-Layer Immutability: Block UPDATE and DELETE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_records_immutable_update_raises(db_session: AsyncSession):
    """Invariant: Any application attempt to UPDATE an ImmutableAuditRecord raises ImmutableAuditViolationError."""
    p_id = uuid.uuid4()
    c_id = uuid.uuid4()

    record = await ImmutableAuditLogger.append_record(
        session=db_session,
        payment_id=p_id,
        recovery_case_id=c_id,
        action="TEST_ACTION",
        strategy="RETRY",
        policy_decision="PERMITTED",
        result="SUCCESS",
    )
    await db_session.commit()

    # Attempt to modify the record attribute
    record.action = "TAMPERED_ACTION"
    with pytest.raises(ImmutableAuditViolationError) as exc_info:
        await db_session.commit()
    assert "cannot be modified" in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_audit_records_immutable_delete_raises(db_session: AsyncSession):
    """Invariant: Any application attempt to DELETE an ImmutableAuditRecord raises ImmutableAuditViolationError."""
    p_id = uuid.uuid4()
    c_id = uuid.uuid4()

    record = await ImmutableAuditLogger.append_record(
        session=db_session,
        payment_id=p_id,
        recovery_case_id=c_id,
        action="TEST_ACTION",
        strategy="RETRY",
        policy_decision="PERMITTED",
        result="SUCCESS",
    )
    await db_session.commit()

    # Attempt to delete the record
    await db_session.delete(record)
    with pytest.raises(ImmutableAuditViolationError) as exc_info:
        await db_session.commit()
    assert "cannot be deleted" in str(exc_info.value)
    await db_session.rollback()


# -----------------------------------------------------------------------------
# 11. Cryptographic SHA-256 Hash Chaining & Tamper Detection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cryptographic_hash_chain_integrity_and_tamper_detection(db_session: AsyncSession):
    """Invariant: Audit records form a verifiable SHA-256 hash chain that detects any tampering."""
    p_id = uuid.uuid4()

    # Append 3 chained records
    rec1 = await ImmutableAuditLogger.append_record(
        session=db_session,
        payment_id=p_id,
        action="INTENT_CLASSIFIED",
        result="SUCCESS",
    )
    await db_session.commit()

    rec2 = await ImmutableAuditLogger.append_record(
        session=db_session,
        payment_id=p_id,
        action="POLICY_EVALUATED",
        policy_decision="PERMITTED",
        result="APPROVED",
    )
    await db_session.commit()

    rec3 = await ImmutableAuditLogger.append_record(
        session=db_session,
        payment_id=p_id,
        action="RECOVERY_EXECUTED",
        result="SUCCESS",
    )
    await db_session.commit()

    # Verify chain properties
    assert rec1.parent_hash is None
    assert rec2.parent_hash == rec1.payload_hash
    assert rec3.parent_hash == rec2.payload_hash

    # Verify integrity passes
    valid, violations = await verify_chain_integrity(db_session, payment_id=p_id)
    assert valid is True
    assert len(violations) == 0

    # Tamper with record 2 via raw SQL (bypassing ORM listeners)
    fake_hash = "deadbeef" * 8
    await db_session.execute(
        text("UPDATE immutable_audit_records SET payload_hash = :fh WHERE id = :rid"),
        {"fh": fake_hash, "rid": rec2.id.hex},
    )
    await db_session.commit()
    db_session.expire_all()

    # Verify integrity now detects tampering!
    valid, violations = await verify_chain_integrity(db_session, payment_id=p_id)
    assert valid is False
    assert len(violations) > 0
    assert any("Hash mismatch" in v or "Chain broken" in v for v in violations)


# -----------------------------------------------------------------------------
# 12. Execution Boundary Policy Re-validation Under Redis Lock
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execution_boundary_policy_revalidation_aborts_when_merchant_disabled(db_session: AsyncSession):
    """Invariant: Phase 4 re-validates policy under Redis lock and aborts if merchant disabled recovery."""
    broker = InMemoryEventBroker()
    redis = InMemoryRedisClient()
    provider = MockPaymentProvider()
    retry_policy = RecoveryRetryPolicy()
    policy_engine = FinancialSafetyPolicyEngine()

    service = SafeRecoveryExecutionService(
        event_broker=broker,
        redis_client=redis,
        provider=provider,
        retry_policy=retry_policy,
        policy_engine=policy_engine,
    )

    # Seed payment, case, merchant, route
    merchant = Merchant(
        id=uuid.uuid4(),
        name="Dynamic Merchant",
        mcc="5411",
        tier=MerchantTier.ENTERPRISE,
        max_auto_retries=2,
        min_recovery_amount_inr=Decimal("50.00"),
        recovery_enabled=True,
    )
    customer = Customer(
        id=uuid.uuid4(),
        external_id="cust_dyn",
        email_masked="test@***.in",
        phone_masked="+91-99999****",
    )
    route = PaymentRoute(
        id="ROUTE_UPI_DYN",
        name="Test Route",
        payment_method=PaymentMethod.UPI,
        is_active=True,
        status=RouteStatus.HEALTHY,
        health_score=0.99,
    )
    db_session.add_all([merchant, customer, route])
    await db_session.flush()

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("1200.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(payment)
    await db_session.flush()

    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=payment.id,
        status=PaymentLifecycleState.RECOVERY_PENDING,
        recovery_state=RecoveryState.APPROVED,
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        attempt_count=0,
        max_attempts=2,
    )
    db_session.add(case)
    await db_session.commit()

    # Now, simulate that the merchant turned off recovery BEFORE worker dequeued
    merchant.recovery_enabled = False
    await db_session.commit()

    # Worker runs under lock
    result = await service.execute_recovery_attempt(
        session=db_session,
        payment_id=payment.id,
        recovery_case_id=case.id,
        attempt_number=1,
        target_route_id=route.id,
    )

    # Must be STOPPED, provider must not be called, and audit record must exist
    assert result.status == ExecutionStatus.STOPPED
    assert "MERCHANT_RESTRICTION" in (result.stop_reason or "") or "MERCHANT_RECOVERY_DISABLED" in (result.stop_reason or "")

    # Verify audit record exists
    stmt = select(ImmutableAuditRecord).where(
        ImmutableAuditRecord.payment_id == payment.id,
        ImmutableAuditRecord.action == "EXECUTION_BOUNDARY_POLICY_REVALIDATION",
    )
    res = await db_session.execute(stmt)
    audit_rec = res.scalar_one_or_none()
    assert audit_rec is not None
    assert audit_rec.policy_decision == "DENIED"
    assert audit_rec.result == "STOPPED"


# -----------------------------------------------------------------------------
# 13. Layered Safety: Phase 3 Guard + Phase 7 Policy Engine in Orchestrator
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orchestrator_layered_safety_both_approve(db_session: AsyncSession):
    """Invariant: Orchestrator runs Phase 3 Guard AND Phase 7 Policy Engine, recording audit logs."""
    broker = InMemoryEventBroker()
    policy_engine = FinancialSafetyPolicyEngine()
    orchestrator = IntelligentRecoveryOrchestrator(
        broker=broker,
        policy_engine=policy_engine,
    )

    merchant = Merchant(
        id=uuid.uuid4(),
        name="Layered Safety Merchant",
        mcc="5411",
        tier=MerchantTier.GROWTH,
        max_auto_retries=2,
        min_recovery_amount_inr=Decimal("50.00"),
        auto_escalate_threshold_inr=Decimal("50000.00"),
        recovery_enabled=True,
    )
    customer = Customer(
        id=uuid.uuid4(),
        external_id="cust_layer",
        email_masked="test@***.in",
        phone_masked="+91-99999****",
    )
    route = PaymentRoute(
        id="ROUTE_UPI_LAYER",
        name="UPI Route",
        payment_method=PaymentMethod.UPI,
        is_active=True,
        status=RouteStatus.HEALTHY,
        health_score=0.95,
    )
    db_session.add_all([merchant, customer, route])
    await db_session.flush()

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("2000.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(payment)
    await db_session.commit()

    payload = PaymentFailedPayload(
        payment_id=payment.id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("2000.00"),
        payment_method=PaymentMethod.UPI,
        route_id=route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="HDFC gateway timed out",
        attempt_number=1,
        recoverable=True,
    )

    case, plan, guard_result = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=payload,
        correlation_id="corr_layer_test",
    )

    # Check that case is APPROVED
    assert case.recovery_state == RecoveryState.APPROVED
    assert guard_result.is_approved is True

    # Check that an ImmutableAuditRecord was logged
    stmt = select(ImmutableAuditRecord).where(
        ImmutableAuditRecord.payment_id == payment.id,
        ImmutableAuditRecord.action == "FINANCIAL_SAFETY_POLICY_AUTHORIZATION",
    )
    res = await db_session.execute(stmt)
    audit_rec = res.scalar_one_or_none()
    assert audit_rec is not None
    assert audit_rec.policy_decision == "PERMITTED"
    assert audit_rec.result == "APPROVED"
    assert audit_rec.parent_hash is None or len(audit_rec.parent_hash) == 64
    assert len(audit_rec.payload_hash) == 64
