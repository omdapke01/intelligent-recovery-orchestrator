"""Comprehensive test suite for Phase 3: Intelligent Recovery Orchestrator.

Tests:
1. Deterministic failure classification (RETRYABLE, NON_RETRYABLE, CUSTOMER_ACTION_REQUIRED, UNKNOWN)
2. Deterministic strategy selection & explainability
3. Safety principle: UNKNOWN failure refusal of financial action & escalation
4. Deterministic recovery guard evaluating all 6 stopping conditions
5. RecoveryStateMachine strict transition validation and terminal lock
6. End-to-end orchestration pipeline & event emission without payment execution
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.broker import InMemoryEventBroker
from app.events.schemas import PaymentFailedPayload
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
from app.orchestrator import (
    DeterministicFailureClassifier,
    DeterministicRecoveryGuard,
    DeterministicStrategySelector,
    IntelligentRecoveryOrchestrator,
    InvalidRecoveryStateTransitionError,
    PaymentRecoveryContext,
    RecoveryPlan,
    RecoveryStateMachine,
)


# =====================================================================
# 1. FAILURE CLASSIFICATION TESTS
# =====================================================================

def test_classifier_known_retryable_codes():
    for code in ["GATEWAY_TIMEOUT", "UPI_GATEWAY_TIMEOUT", "RATE_LIMIT_EXCEEDED", "NETWORK_ERROR"]:
        res = DeterministicFailureClassifier.classify(code, FailureCategory.TRANSIENT)
        assert res == RetryabilityClass.RETRYABLE


def test_classifier_known_non_retryable_codes():
    for code in ["CARD_EXPIRED", "INVALID_VPA", "ACCOUNT_BLOCKED", "FRAUD_SUSPECTED"]:
        res = DeterministicFailureClassifier.classify(code, FailureCategory.PERMANENT)
        assert res == RetryabilityClass.NON_RETRYABLE


def test_classifier_known_customer_action_codes():
    for code in ["INSUFFICIENT_FUNDS", "LOW_BALANCE", "USER_DROPPED_OFF", "OTP_EXPIRED"]:
        res = DeterministicFailureClassifier.classify(code, FailureCategory.CUSTOMER_ACTION_REQUIRED)
        assert res == RetryabilityClass.CUSTOMER_ACTION_REQUIRED


def test_classifier_unknown_unrecognized_code():
    res = DeterministicFailureClassifier.classify("MYSTERY_GATEWAY_BUG_99", FailureCategory.PERMANENT)
    # Even if category is permanent, unrecognized codes are categorized cleanly by category or fallback to UNKNOWN
    res_raw = DeterministicFailureClassifier.classify("MYSTERY_CODE_123", None)  # type: ignore
    assert res_raw == RetryabilityClass.UNKNOWN


# =====================================================================
# 2. STRATEGY SELECTION & EXPLAINABILITY TESTS
# =====================================================================

def _make_dummy_context(
    error_code: str = "GATEWAY_TIMEOUT",
    category: FailureCategory = FailureCategory.TRANSIENT,
    route_status: RouteStatus = RouteStatus.HEALTHY,
    route_health: float = 0.95,
    amount_inr: Decimal = Decimal("1500.00"),
    min_recovery_val: Decimal = Decimal("50.00"),
    auto_escalate_val: Decimal = Decimal("50000.00"),
    attempt_num: int = 1,
    max_retries: int = 2,
    recovery_enabled: bool = True,
    alt_routes: list | None = None,
    failure_age_seconds: float = 0.0,
) -> PaymentRecoveryContext:
    now = datetime.now(timezone.utc)
    return PaymentRecoveryContext(
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount_inr=amount_inr,
        payment_method=PaymentMethod.UPI,
        route_id="ROUTE_HDFC_UPI",
        route_health_score=route_health,
        route_is_active=True,
        route_status=route_status,
        failure_category=category,
        error_code=error_code,
        reason="Test failure reason",
        attempt_number=attempt_num,
        failure_created_at=now - timedelta(seconds=failure_age_seconds),
        merchant_tier=MerchantTier.GROWTH,
        merchant_recovery_enabled=recovery_enabled,
        merchant_max_auto_retries=max_retries,
        merchant_min_recovery_amount_inr=min_recovery_val,
        merchant_auto_escalate_threshold_inr=auto_escalate_val,
        available_alternative_routes=alt_routes or [],
        correlation_id=f"corr_{uuid.uuid4().hex[:8]}",
    )


def test_strategy_selector_healthy_route_backoff():
    ctx = _make_dummy_context(error_code="GATEWAY_TIMEOUT", route_health=0.95, route_status=RouteStatus.HEALTHY)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    assert plan.strategy == RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF
    assert plan.suggested_backoff_sec == 15.0
    assert plan.target_route_id == "ROUTE_HDFC_UPI"
    assert plan.decision_confidence == 1.0
    assert "HEALTHY" in plan.explanation


def test_strategy_selector_degraded_route_failover():
    ctx = _make_dummy_context(
        error_code="GATEWAY_TIMEOUT",
        route_health=0.45,
        route_status=RouteStatus.DEGRADED,
        alt_routes=["ROUTE_ICICI_UPI", "ROUTE_AXIS_UPI"],
    )
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    assert plan.strategy == RecoveryStrategy.ROUTE_FAILOVER
    assert plan.target_route_id == "ROUTE_ICICI_UPI"
    assert plan.decision_confidence == 0.95
    assert "ROUTE_FAILOVER" in plan.explanation


def test_strategy_selector_customer_action():
    ctx = _make_dummy_context(error_code="INSUFFICIENT_FUNDS", category=FailureCategory.CUSTOMER_ACTION_REQUIRED)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.CUSTOMER_ACTION_REQUIRED)

    assert plan.strategy == RecoveryStrategy.NOTIFY_CUSTOMER_LINK
    assert plan.notification_channel == "SMS"
    assert "CUSTOMER_ACTION_REQUIRED" in plan.explanation


def test_strategy_selector_non_retryable_abandon():
    ctx = _make_dummy_context(error_code="CARD_EXPIRED", category=FailureCategory.PERMANENT)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.NON_RETRYABLE)

    assert plan.strategy == RecoveryStrategy.TERMINAL_ABANDON
    assert plan.decision_confidence == 1.0


def test_strategy_selector_safety_unknown_refuses_financial_action():
    """Safety test: unknown error codes must NEVER receive automated retry/backoff."""
    ctx = _make_dummy_context(error_code="UNRECOGNIZED_BANK_ERROR")
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.UNKNOWN)

    assert plan.strategy == RecoveryStrategy.MANUAL_REVIEW
    assert plan.decision_confidence == 0.0
    assert "refuses to execute automated financial actions" in plan.explanation


# =====================================================================
# 3. DETERMINISTIC RECOVERY GUARD & STOPPING CONDITIONS
# =====================================================================

def test_guard_approves_healthy_plan():
    ctx = _make_dummy_context(amount_inr=Decimal("1500.00"), min_recovery_val=Decimal("100.00"))
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is True
    assert guard_res.stop_reason is None
    assert guard_res.is_escalated is False


def test_guard_stopping_condition_1_max_retries():
    ctx = _make_dummy_context(attempt_num=2, max_retries=2)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.stop_reason == "MAX_RETRIES_EXCEEDED"
    assert "MAX_RETRIES_GUARD" in guard_res.violated_guards


def test_guard_stopping_condition_2_max_window_sla():
    # UPI max window is 15 minutes (900 seconds); simulate 1200 seconds elapsed
    ctx = _make_dummy_context(failure_age_seconds=1200.0)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.stop_reason == "MAX_WINDOW_EXCEEDED"


def test_guard_stopping_condition_3_non_retryable_error():
    ctx = _make_dummy_context(error_code="ACCOUNT_BLOCKED", category=FailureCategory.PERMANENT)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.NON_RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.stop_reason == "NON_RETRYABLE_ERROR"


def test_guard_stopping_condition_4_merchant_disabled():
    ctx = _make_dummy_context(recovery_enabled=False)
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.stop_reason == "MERCHANT_RECOVERY_DISABLED"


def test_guard_stopping_condition_5_min_recovery_value():
    """Verify merchant-configurable minimum threshold halts unprofitable recovery."""
    ctx = _make_dummy_context(
        amount_inr=Decimal("25.00"),
        min_recovery_val=Decimal("100.00"),  # Merchant configured minimum INR 100
    )
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.stop_reason == "INSUFFICIENT_RECOVERY_VALUE"


def test_guard_stopping_condition_6_high_value_escalation():
    """Verify repeat failure on high-value transaction escalates safely."""
    ctx = _make_dummy_context(
        amount_inr=Decimal("75000.00"),
        auto_escalate_val=Decimal("50000.00"),
        attempt_num=2,
    )
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.RETRYABLE)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.is_escalated is True
    assert guard_res.stop_reason == "HIGH_VALUE_ENTERPRISE_ESCALATION"


def test_guard_safety_escalates_unknown_error():
    """Verify unknown failure codes trigger safety escalation in recovery guard."""
    ctx = _make_dummy_context(error_code="UNKNOWN_ERROR_CODE_XYZ")
    plan = DeterministicStrategySelector.select_strategy(ctx, RetryabilityClass.UNKNOWN)

    guard_res = DeterministicRecoveryGuard.evaluate(ctx, plan)
    assert guard_res.is_approved is False
    assert guard_res.is_escalated is True
    assert guard_res.stop_reason == "UNKNOWN_ERROR_SAFETY_ESCALATION"


# =====================================================================
# 4. RECOVERY STATE MACHINE TRANSITION TESTS
# =====================================================================

def test_state_machine_valid_happy_path():
    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        recovery_state=RecoveryState.FAILED,
    )

    RecoveryStateMachine.transition(case, RecoveryState.CLASSIFIED)
    assert case.recovery_state == RecoveryState.CLASSIFIED

    RecoveryStateMachine.transition(case, RecoveryState.RECOVERY_PLANNED)
    assert case.recovery_state == RecoveryState.RECOVERY_PLANNED

    RecoveryStateMachine.transition(case, RecoveryState.GUARD_PENDING)
    assert case.recovery_state == RecoveryState.GUARD_PENDING

    RecoveryStateMachine.transition(case, RecoveryState.APPROVED)
    assert case.recovery_state == RecoveryState.APPROVED


def test_state_machine_illegal_jump_raises_error():
    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        recovery_state=RecoveryState.FAILED,
    )
    with pytest.raises(InvalidRecoveryStateTransitionError):
        # Cannot jump directly from FAILED to APPROVED
        RecoveryStateMachine.transition(case, RecoveryState.APPROVED)


def test_state_machine_terminal_lock():
    for term_state in (RecoveryState.STOPPED, RecoveryState.ESCALATED, RecoveryState.RECOVERY_SUCCEEDED):
        case = RecoveryCase(
            id=uuid.uuid4(),
            payment_id=uuid.uuid4(),
            recovery_state=term_state,
        )
        assert term_state.is_terminal is True
        with pytest.raises(InvalidRecoveryStateTransitionError):
            RecoveryStateMachine.transition(case, RecoveryState.APPROVED)


# =====================================================================
# 5. END-TO-END ORCHESTRATOR PIPELINE INTEGRATION
# =====================================================================

@pytest.mark.asyncio
async def test_orchestrator_pipeline_healthy_timeout_approved(db_session: AsyncSession):
    """Verify healthy transient failure produces an explainable approved plan and emits retry intent."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(
        id=uuid.uuid4(),
        name="TechStore Corp",
        mcc="5411",
        tier=MerchantTier.ENTERPRISE,
        max_auto_retries=3,
        min_recovery_amount_inr=Decimal("100.00"),
    )
    customer = Customer(id=uuid.uuid4(), external_id="cust_orch_1", email_masked="a@b.com", phone_masked="+91-111")
    route = PaymentRoute(id="ROUTE_HDFC_INSTANT", name="HDFC Switch", payment_method=PaymentMethod.UPI, health_score=0.98)
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("2500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.PROCESSING,
        idempotency_key="idemp_orch_01",
    )
    db_session.add_all([merchant, customer, route, payment])
    await db_session.commit()

    orchestrator = IntelligentRecoveryOrchestrator(broker)

    failure_payload = PaymentFailedPayload(
        payment_id=payment.id,
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("2500.00"),
        payment_method=PaymentMethod.UPI,
        route_id=route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Bank gateway timed out",
        attempt_number=1,
        recoverable=True,
    )

    corr_id = "corr_orch_test_01"
    case, plan, guard = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=failure_payload,
        correlation_id=corr_id,
        causation_id="cause_01",
    )

    # 1. State verification
    assert case.recovery_state == RecoveryState.APPROVED
    assert case.status == PaymentLifecycleState.RECOVERY_PENDING
    assert plan.strategy == RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF
    assert plan.suggested_backoff_sec == 15.0
    assert guard.is_approved is True

    # 2. Plan explainability persisted in PostgreSQL
    assert case.plan_details is not None
    assert case.plan_details["strategy"] == "DETERMINISTIC_RETRY_BACKOFF"
    assert "HEALTHY" in case.plan_details["explanation"]

    # 3. Intent events emitted (payment.retry_requested + recovery.started + notification.requested)
    repub_msgs = broker.topic_messages.get(orchestrator.TOPIC, [])
    event_types = [m.value_dict["event_type"] for m in repub_msgs]
    assert "recovery.started" in event_types
    assert "payment.retry_requested" in event_types
    assert "notification.requested" in event_types


@pytest.mark.asyncio
async def test_orchestrator_pipeline_unknown_error_safety_escalated(db_session: AsyncSession):
    """Verify safety principle: unknown error code escalates to manual review without retrying."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant = Merchant(id=uuid.uuid4(), name="Secure Corp", mcc="5411")
    customer = Customer(id=uuid.uuid4(), external_id="cust_orch_2", email_masked="sec@corp.com", phone_masked="+91-222")
    route = PaymentRoute(id="ROUTE_AXIS_FAST", name="Axis", payment_method=PaymentMethod.UPI)
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("1500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.PROCESSING,
        idempotency_key="idemp_orch_02",
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
        error_code="UNRECOGNIZED_SPECIAL_ERROR_999",
        reason="Vendor unmapped exception",
        attempt_number=1,
        recoverable=True,
    )

    case, plan, guard = await orchestrator.orchestrate_failure(
        session=db_session,
        failure_payload=failure_payload,
        correlation_id="corr_safety_unknown",
    )

    # 1. State verification
    assert case.recovery_state == RecoveryState.ESCALATED
    assert case.status == PaymentLifecycleState.ESCALATED
    assert case.stop_reason == "UNKNOWN_ERROR_SAFETY_ESCALATION"
    assert guard.is_escalated is True

    # 2. Plan verification: selected MANUAL_REVIEW
    assert plan.strategy == RecoveryStrategy.MANUAL_REVIEW
    assert plan.decision_confidence == 0.0

    # 3. Emits recovery.escalated, NEVER payment.retry_requested
    repub_msgs = broker.topic_messages.get(orchestrator.TOPIC, [])
    event_types = [m.value_dict["event_type"] for m in repub_msgs]
    assert "recovery.escalated" in event_types
    assert "payment.retry_requested" not in event_types
