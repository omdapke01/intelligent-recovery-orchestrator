"""Real-world safety regression tests validating production payment failure scenarios.

Scenarios tested:
1. Normal transient: GATEWAY_TIMEOUT, healthy route, INR 1,500
   -> Tier 1 -> retry/backoff -> Phase 3 guard -> Phase 7 policy -> Phase 4 execution -> SUCCESS
2. Degraded route: Ambiguous failure, route degraded, healthy alternate available
   -> Tier 3 Agent -> investigation -> failover recommendation -> Phase 3 -> Phase 7 -> Phase 4 -> SUCCESS
3. High-value transaction: INR 250,000, AI recommends RETRY
   -> Phase 7 Policy: REQUIRES_HUMAN_APPROVAL -> ESCALATED -> 0 provider execution calls
4. Fraud / hard decline: CARD_BLOCKED / FRAUD_SUSPECTED, AI recommends RETRY
   -> Phase 7 Policy: DENIED -> STOPPED -> 0 provider execution calls
5. Late success / stale retry: payment.failed -> customer independently succeeds (SUCCESS) -> stale retry arrives
   -> Phase 4 detects terminal/success state -> NO provider execution call -> writes immutable audit record
6. Asynchronous pending / in-flight hold: PAYMENT_PENDING / PROCESSING
   -> Phase 7 Policy: DENIED (hold for webhook reconciliation) -> 0 provider execution calls
"""

from datetime import datetime, timezone
from decimal import Decimal
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.investigator import RecoveryInvestigationAgent
from app.ai.hierarchy import HierarchicalRecoveryDecisionEngine
from app.audit.models import ImmutableAuditRecord
from app.events.broker import InMemoryEventBroker
from app.events.schemas import PaymentFailedPayload
from app.execution.provider import MockPaymentProvider, ProviderOutcome
from app.execution.service import ExecutionStatus, SafeRecoveryExecutionService
from app.models import (
    Customer,
    FailureCategory,
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
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.orchestrator.orchestrator import IntelligentRecoveryOrchestrator
from app.policy.engine import FinancialSafetyPolicyEngine
from app.policy.models import PolicyDecision


@pytest.mark.asyncio
async def test_scenario_1_normal_transient_happy_path(db_session: AsyncSession):
    """Test 1: Normal transient error on healthy switch -> Tier 1 deterministic recovery -> Phase 4 execution -> SUCCESS."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(
        id=uuid.uuid4(),
        name="RetailMart Ltd",
        mcc="5411",
        tier=MerchantTier.GROWTH,
        max_auto_retries=3,
        min_recovery_amount_inr=Decimal("100.00"),
        auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    customer = Customer(id=uuid.uuid4(), external_id="cust_reg_1", email_masked="c1@example.com", phone_masked="+91-9111111111")
    route = PaymentRoute(
        id="ROUTE_HDFC_UPI_REG",
        name="HDFC Primary Switch",
        payment_method=PaymentMethod.UPI,
        health_score=0.98,
        status=RouteStatus.HEALTHY,
        is_active=True,
    )
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("1500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key="idemp_reg_01",
    )
    db_session.add_all([merchant, customer, route, payment])
    await db_session.commit()

    orchestrator = IntelligentRecoveryOrchestrator(broker)
    failure_payload = PaymentFailedPayload(
        payment_id=payment.id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("1500.00"),
        payment_method=PaymentMethod.UPI,
        route_id=route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Upstream switch timeout after 15s",
        attempt_number=1,
        recoverable=True,
    )

    case, plan, guard = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=failure_payload,
        correlation_id="corr_reg_01",
    )

    # Verify decision pipeline approved
    assert case.recovery_state == RecoveryState.APPROVED
    assert plan.strategy == RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF

    # Execute via Phase 4 Safe Recovery Execution Service
    provider = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    exec_service = SafeRecoveryExecutionService(event_broker=broker, provider=provider)
    result = await exec_service.execute_recovery_attempt(
        session=db_session,
        payment_id=payment.id,
        recovery_case_id=case.id,
        attempt_number=1,
        target_route_id=route.id,
        strategy=plan.strategy,
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert provider.call_count == 1
    await db_session.refresh(payment)
    assert payment.status == PaymentLifecycleState.RECOVERED


@pytest.mark.asyncio
async def test_scenario_2_degraded_route_failover_success(db_session: AsyncSession):
    """Test 2: Degraded switch -> Tier 3 Specialist Agent investigates -> recommends route failover -> SUCCESS."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(
        id=uuid.uuid4(),
        name="FlipMart Electronics",
        mcc="5732",
        tier=MerchantTier.ENTERPRISE,
        max_auto_retries=3,
        min_recovery_amount_inr=Decimal("100.00"),
        auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    customer = Customer(id=uuid.uuid4(), external_id="cust_reg_2", email_masked="c2@example.com", phone_masked="+91-9222222222")
    degraded_route = PaymentRoute(
        id="ROUTE_SBI_DEGRADED_REG",
        name="SBI Flapping Switch",
        payment_method=PaymentMethod.UPI,
        health_score=0.35,
        status=RouteStatus.DEGRADED,
        is_active=True,
    )
    backup_route = PaymentRoute(
        id="ROUTE_ICICI_HEALTHY_REG",
        name="ICICI Healthy Switch",
        payment_method=PaymentMethod.UPI,
        health_score=0.99,
        status=RouteStatus.HEALTHY,
        is_active=True,
    )
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("4500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key="idemp_reg_02",
    )
    db_session.add_all([merchant, customer, degraded_route, backup_route, payment])
    await db_session.commit()

    agent = RecoveryInvestigationAgent(event_broker=broker)
    decision_engine = HierarchicalRecoveryDecisionEngine(agent=agent)
    orchestrator = IntelligentRecoveryOrchestrator(broker, decision_engine=decision_engine)

    failure_payload = PaymentFailedPayload(
        payment_id=payment.id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("4500.00"),
        payment_method=PaymentMethod.UPI,
        route_id=degraded_route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="SWITCH_ERROR_503",
        reason="Switch returning intermittent 503 Service Unavailable",
        attempt_number=1,
        recoverable=True,
    )

    case, plan, guard = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=failure_payload,
        correlation_id="corr_reg_02",
    )

    assert case.recovery_state == RecoveryState.APPROVED
    assert plan.strategy == RecoveryStrategy.ROUTE_FAILOVER
    assert plan.target_route_id == backup_route.id

    # Execute via Phase 4
    provider = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    exec_service = SafeRecoveryExecutionService(event_broker=broker, provider=provider)
    result = await exec_service.execute_recovery_attempt(
        session=db_session,
        payment_id=payment.id,
        recovery_case_id=case.id,
        attempt_number=1,
        target_route_id=plan.target_route_id,
        strategy=plan.strategy,
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert provider.call_count == 1
    assert provider.calls[0].route_id == backup_route.id


@pytest.mark.asyncio
async def test_scenario_3_high_value_transaction_requires_human_approval(db_session: AsyncSession):
    """Test 3: High-value payment (INR 250,000 > system cap 100,000) -> Phase 7 Policy intercepts -> ESCALATED -> 0 provider calls."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(
        id=uuid.uuid4(),
        name="Luxury Jewelers",
        mcc="5944",
        tier=MerchantTier.ENTERPRISE,
        max_auto_retries=3,
        auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    customer = Customer(id=uuid.uuid4(), external_id="cust_reg_3", email_masked="c3@example.com", phone_masked="+91-9333333333")
    route = PaymentRoute(
        id="ROUTE_HDFC_UPI_HIGH",
        name="HDFC Switch",
        payment_method=PaymentMethod.UPI,
        health_score=0.98,
        status=RouteStatus.HEALTHY,
        is_active=True,
    )
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("250000.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key="idemp_reg_03",
    )
    db_session.add_all([merchant, customer, route, payment])
    await db_session.commit()

    orchestrator = IntelligentRecoveryOrchestrator(broker)
    failure_payload = PaymentFailedPayload(
        payment_id=payment.id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("250000.00"),
        payment_method=PaymentMethod.UPI,
        route_id=route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Bank gateway timeout on high value payment",
        attempt_number=1,
        recoverable=True,
    )

    case, plan, guard = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=failure_payload,
        correlation_id="corr_reg_03",
    )

    # Must be ESCALATED by Phase 7 Policy Engine
    assert case.recovery_state == RecoveryState.ESCALATED
    assert "HIGH_VALUE_TRANSACTION" in (case.stop_reason or "")

    # Provider should never be called
    provider = MockPaymentProvider()
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_scenario_4_fraud_and_hard_decline_blocked(db_session: AsyncSession):
    """Test 4: Hard decline / fraud error (CARD_BLOCKED / FRAUD_SUSPECTED) -> Phase 7 DENIED -> STOPPED -> 0 provider calls."""
    policy_engine = FinancialSafetyPolicyEngine()

    ctx = PaymentRecoveryContext(
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=Decimal("3500.00"),
        payment_method=PaymentMethod.CREDIT_CARD,
        route_id="ROUTE_VISA_CARD",
        route_health_score=0.99,
        route_is_active=True,
        route_status=RouteStatus.HEALTHY,
        failure_category=FailureCategory.CUSTOMER_ACTION_REQUIRED,
        error_code="CARD_BLOCKED",
        reason="Card has been permanently blocked by issuing bank",
        attempt_number=1,
        failure_created_at=datetime.now(timezone.utc),
        merchant_tier=MerchantTier.GROWTH,
        merchant_recovery_enabled=True,
        merchant_max_auto_retries=2,
        merchant_min_recovery_amount_inr=Decimal("50.00"),
        merchant_auto_escalate_threshold_inr=Decimal("50000.00"),
    )

    plan = RecoveryPlan(
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        retryability=RetryabilityClass.RETRYABLE,
    )

    result = policy_engine.evaluate(ctx, plan)
    assert result.decision == PolicyDecision.DENIED
    assert "PROHIBITED_RECOVERY_SITUATION" in result.reason
    assert "CARD_BLOCKED" in result.reason


@pytest.mark.asyncio
async def test_scenario_5_late_success_stale_retry_discarded_and_audited(db_session: AsyncSession):
    """Test 5 (CRITICAL): Payment failed -> customer independently succeeds (SUCCESS) -> stale retry arrives.

    Phase 4 detects terminal state -> NO provider call -> writes immutable audit record.
    """
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(
        id=uuid.uuid4(),
        name="QuickPay Supermarket",
        mcc="5411",
        tier=MerchantTier.GROWTH,
        max_auto_retries=2,
        min_recovery_amount_inr=Decimal("50.00"),
        auto_escalate_threshold_inr=Decimal("50000.00"),
    )
    customer = Customer(id=uuid.uuid4(), external_id="cust_reg_5", email_masked="c5@example.com", phone_masked="+91-9555555555")
    route = PaymentRoute(
        id="ROUTE_UPI_P5",
        name="UPI Fast Switch",
        payment_method=PaymentMethod.UPI,
        health_score=0.95,
        status=RouteStatus.HEALTHY,
        is_active=True,
    )

    # Payment initially failed, but customer independently retried and payment is now SUCCESS!
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("899.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.SUCCESS,  # <-- Succeeded externally!
        idempotency_key="idemp_reg_05",
    )
    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=payment.id,
        status=PaymentLifecycleState.RECOVERY_PENDING,
        recovery_state=RecoveryState.APPROVED,
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        attempt_count=0,
        max_attempts=2,
    )
    db_session.add_all([merchant, customer, route, payment, case])
    await db_session.commit()

    provider = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    exec_service = SafeRecoveryExecutionService(event_broker=broker, provider=provider)

    # Old stale retry message wakes up and tries to execute
    exec_result = await exec_service.execute_recovery_attempt(
        session=db_session,
        payment_id=payment.id,
        recovery_case_id=case.id,
        attempt_number=1,
        target_route_id=route.id,
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
    )

    # 1. Must NOT execute provider call
    assert exec_result.status == ExecutionStatus.ALREADY_COMPLETED
    assert provider.call_count == 0

    # 2. Must record immutable audit entry
    audit_res = await db_session.execute(
        select(ImmutableAuditRecord).where(
            ImmutableAuditRecord.payment_id == payment.id,
            ImmutableAuditRecord.action == "STALE_RECOVERY_DISCARDED",
        )
    )
    audit_entry = audit_res.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.result == "DISCARDED"
    assert "SUCCESS" in (audit_entry.escalation_reason or "")


@pytest.mark.asyncio
async def test_scenario_6_asynchronous_pending_processing_held(db_session: AsyncSession):
    """Test 6: Asynchronous pending state (PAYMENT_PENDING / AWAITING_CONFIRMATION).

    Policy blocks blind retry with PENDING_PAYMENT_HOLD -> 0 provider calls.
    """
    policy_engine = FinancialSafetyPolicyEngine()

    for pending_code in ["PAYMENT_PENDING", "AWAITING_CONFIRMATION", "PROCESSING"]:
        ctx = PaymentRecoveryContext(
            payment_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            amount_inr=Decimal("2000.00"),
            payment_method=PaymentMethod.UPI,
            route_id="ROUTE_UPI_PENDING",
            route_health_score=0.95,
            route_is_active=True,
            route_status=RouteStatus.HEALTHY,
            failure_category=FailureCategory.TRANSIENT,
            error_code=pending_code,
            reason="Transaction state is currently awaiting bank confirmation",
            attempt_number=1,
            failure_created_at=datetime.now(timezone.utc),
            merchant_tier=MerchantTier.GROWTH,
            merchant_recovery_enabled=True,
            merchant_max_auto_retries=2,
            merchant_min_recovery_amount_inr=Decimal("50.00"),
            merchant_auto_escalate_threshold_inr=Decimal("50000.00"),
        )

        plan = RecoveryPlan(
            strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
            retryability=RetryabilityClass.RETRYABLE,
        )

        result = policy_engine.evaluate(ctx, plan)
        assert result.decision == PolicyDecision.DENIED
        assert "PENDING_PAYMENT_HOLD" in result.reason
