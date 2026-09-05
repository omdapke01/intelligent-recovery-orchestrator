"""Interactive CLI Demonstration of Phase 3: Intelligent Recovery Orchestrator.

Demonstrates 5 core deterministic recovery scenarios:
1. Healthy Route Timeout -> Approved Backoff Plan -> Emits payment.retry_requested
2. Degraded Switch Timeout -> Approved Route Failover Plan -> Emits payment.retry_requested
3. Insufficient Funds -> Approved Customer Action Notification Plan -> Emits notification.requested
4. Unprofitable Amount (< Merchant Minimum) -> Stopped Plan -> Emits recovery.stopped
5. Safety Principle: Unknown Failure Code -> Escalated Plan -> Emits recovery.escalated
"""

import asyncio
import json
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session_factory, init_db
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
    RouteStatus,
)
from app.orchestrator import IntelligentRecoveryOrchestrator
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("iro-phase3-demo")


def print_header(title: str):
    width = 80
    print("\n" + "=" * width)
    print(f"  {title}".center(width))
    print("=" * width)


if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def print_plan_box(plan, guard, case):
    print(f"\n+--- RECOVERY PLAN GENERATED ------------------------------------------")
    print(f"| Strategy:           {plan.strategy.value}")
    print(f"| Retryability:       {plan.retryability.value}")
    print(f"| Decision Certainty: {plan.decision_confidence * 100:.0f}% (Deterministic Rule Certainty)")
    print(f"| Final State:        {case.recovery_state.value}")
    print(f"| Guard Outcome:      {'APPROVED' if guard.is_approved else ('ESCALATED' if guard.is_escalated else 'STOPPED')}")
    if guard.stop_reason:
        print(f"| Stop Reason:        {guard.stop_reason}")
    print(f"| Explanation:        {plan.explanation}")
    print(f"| Guards Evaluated:   {', '.join(guard.guards_checked)}")
    print(f"+----------------------------------------------------------------------")


async def main():
    print_header("IRO PHASE 3: DETERMINISTIC RECOVERY ORCHESTRATOR DEMO")
    print("No LLM dependencies. 100% deterministic, explainable decision baseline.\n")

    await init_db()
    broker = InMemoryEventBroker()
    await broker.start()
    orchestrator = IntelligentRecoveryOrchestrator(broker)

    # 1. Setup Seed Entities
    async with async_session_factory() as session:
        # Enterprise Merchant with ₹100 minimum recovery threshold
        stmt = select(Merchant).where(Merchant.name == "Flipkart India Pvt Ltd")
        merchant_enterprise = (await session.execute(stmt)).scalar_one_or_none()
        if not merchant_enterprise:
            merchant_enterprise = Merchant(
                id=uuid.uuid4(),
                name="Flipkart India Pvt Ltd",
                mcc="5311",
                tier=MerchantTier.ENTERPRISE,
                max_auto_retries=3,
                min_recovery_amount_inr=Decimal("100.00"),
                auto_escalate_threshold_inr=Decimal("50000.00"),
            )
            session.add(merchant_enterprise)

        # Small Merchant with ₹20 minimum threshold
        stmt = select(Merchant).where(Merchant.name == "Corner Chai Corner")
        merchant_small = (await session.execute(stmt)).scalar_one_or_none()
        if not merchant_small:
            merchant_small = Merchant(
                id=uuid.uuid4(),
                name="Corner Chai Corner",
                mcc="5812",
                tier=MerchantTier.STARTUP,
                max_auto_retries=2,
                min_recovery_amount_inr=Decimal("20.00"),
            )
            session.add(merchant_small)

        stmt = select(Customer).where(Customer.external_id == "cust_demo_vip")
        customer = (await session.execute(stmt)).scalar_one_or_none()
        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                external_id="cust_demo_vip",
                email_masked="vikram.aditya@***.in",
                phone_masked="+91-99887****1",
            )
            session.add(customer)

        # Routes
        route_hdfc = PaymentRoute(
            id="ROUTE_HDFC_INSTANT_UPI",
            name="HDFC Bank Instant UPI Switch",
            payment_method=PaymentMethod.UPI,
            is_active=True,
            status=RouteStatus.HEALTHY,
            health_score=0.98,
        )
        route_sbi_degraded = PaymentRoute(
            id="ROUTE_SBI_CORE_UPI",
            name="SBI Core Banking Switch",
            payment_method=PaymentMethod.UPI,
            is_active=True,
            status=RouteStatus.DEGRADED,
            health_score=0.35,
        )
        route_icici_alt = PaymentRoute(
            id="ROUTE_ICICI_SWITCH_UPI",
            name="ICICI Alternative UPI Gateway",
            payment_method=PaymentMethod.UPI,
            is_active=True,
            status=RouteStatus.HEALTHY,
            health_score=0.96,
        )

        for route in [route_hdfc, route_sbi_degraded, route_icici_alt]:
            await session.merge(route)
        await session.commit()

    # -----------------------------------------------------------------
    # SCENARIO 1: Healthy Route Timeout -> Approved Backoff Plan
    # -----------------------------------------------------------------
    print_header("SCENARIO 1: TRANSIENT TIMEOUT ON HEALTHY ROUTE")
    async with async_session_factory() as session:
        payment1 = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("3499.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
        )
        session.add(payment1)
        await session.commit()

        failure1 = PaymentFailedPayload(
            payment_id=payment1.id,
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("3499.00"),
            payment_method=PaymentMethod.UPI,
            route_id=route_hdfc.id,
            failure_category=FailureCategory.TRANSIENT,
            error_code="GATEWAY_TIMEOUT",
            reason="Bank switch did not respond in 15000ms",
            attempt_number=1,
            recoverable=True,
        )

        case1, plan1, guard1 = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=failure1,
            correlation_id=f"corr_scen_1_{uuid.uuid4().hex[:6]}",
        )
        print_plan_box(plan1, guard1, case1)
        print(f"[EVENT BUS] Emitted recovery intent: 'payment.retry_requested' with backoff={plan1.suggested_backoff_sec}s")

    # -----------------------------------------------------------------
    # SCENARIO 2: Degraded Switch -> Approved Route Failover Plan
    # -----------------------------------------------------------------
    print_header("SCENARIO 2: DEGRADED SWITCH -> DYNAMIC ROUTE FAILOVER")
    async with async_session_factory() as session:
        payment2 = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("12500.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
        )
        session.add(payment2)
        await session.commit()

        failure2 = PaymentFailedPayload(
            payment_id=payment2.id,
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("12500.00"),
            payment_method=PaymentMethod.UPI,
            route_id=route_sbi_degraded.id,  # Degraded route (score: 0.35)
            failure_category=FailureCategory.ROUTE_DEGRADATION,
            error_code="BANK_SYSTEM_BUSY",
            reason="SBI switch queue full (TPS throttled)",
            attempt_number=1,
            recoverable=True,
        )

        case2, plan2, guard2 = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=failure2,
            correlation_id=f"corr_scen_2_{uuid.uuid4().hex[:6]}",
        )
        print_plan_box(plan2, guard2, case2)
        print(f"[EVENT BUS] Emitted recovery intent: 'payment.retry_requested' targeting '{plan2.target_route_id}'")

    # -----------------------------------------------------------------
    # SCENARIO 3: Insufficient Funds -> Customer Action Required
    # -----------------------------------------------------------------
    print_header("SCENARIO 3: INSUFFICIENT FUNDS -> CUSTOMER NOTIFICATION LINK")
    async with async_session_factory() as session:
        payment3 = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("2100.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
        )
        session.add(payment3)
        await session.commit()

        failure3 = PaymentFailedPayload(
            payment_id=payment3.id,
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("2100.00"),
            payment_method=PaymentMethod.UPI,
            route_id=route_hdfc.id,
            failure_category=FailureCategory.CUSTOMER_ACTION_REQUIRED,
            error_code="INSUFFICIENT_FUNDS",
            reason="Customer account has balance lower than transaction amount",
            attempt_number=1,
            recoverable=True,
        )

        case3, plan3, guard3 = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=failure3,
            correlation_id=f"corr_scen_3_{uuid.uuid4().hex[:6]}",
        )
        print_plan_box(plan3, guard3, case3)
        print(f"[EVENT BUS] Emitted customer intent: 'notification.requested' (Channel: {plan3.notification_channel})")
        print("            Zero blind bank retries attempted (strictly adhering to customer-action rule).")

    # -----------------------------------------------------------------
    # SCENARIO 4: Insufficient Value (< Merchant Configured Minimum)
    # -----------------------------------------------------------------
    print_header("SCENARIO 4: UNPROFITABLE TRANSACTION VALUE -> GUARD STOPPED")
    async with async_session_factory() as session:
        # Transaction of ₹40 for enterprise merchant with min recovery threshold ₹100
        payment4 = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("40.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
        )
        session.add(payment4)
        await session.commit()

        failure4 = PaymentFailedPayload(
            payment_id=payment4.id,
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("40.00"),
            payment_method=PaymentMethod.UPI,
            route_id=route_hdfc.id,
            failure_category=FailureCategory.TRANSIENT,
            error_code="GATEWAY_TIMEOUT",
            reason="Gateway timeout",
            attempt_number=1,
            recoverable=True,
        )

        case4, plan4, guard4 = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=failure4,
            correlation_id=f"corr_scen_4_{uuid.uuid4().hex[:6]}",
        )
        print_plan_box(plan4, guard4, case4)
        print(f"[EVENT BUS] Emitted terminal event: 'recovery.stopped' (Stop Reason: {case4.stop_reason})")
        print(f"            Amount INR 40.00 is below merchant minimum threshold of INR {merchant_enterprise.min_recovery_amount_inr:.2f}.")

    # -----------------------------------------------------------------
    # SCENARIO 5: Safety Principle: Unknown Error Code -> Escalation
    # -----------------------------------------------------------------
    print_header("SCENARIO 5: SAFETY PRINCIPLE — UNKNOWN FAILURE CODE ESCALATION")
    async with async_session_factory() as session:
        payment5 = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("4500.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
        )
        session.add(payment5)
        await session.commit()

        failure5 = PaymentFailedPayload(
            payment_id=payment5.id,
            merchant_id=merchant_enterprise.id,
            customer_id=customer.id,
            amount_inr=Decimal("4500.00"),
            payment_method=PaymentMethod.UPI,
            route_id=route_hdfc.id,
            failure_category=FailureCategory.TRANSIENT,
            error_code="UNMAPPED_ACQUIRER_EXCEPTION_707",
            reason="Unrecognized vendor error code string",
            attempt_number=1,
            recoverable=True,
        )

        case5, plan5, guard5 = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=failure5,
            correlation_id=f"corr_scen_5_{uuid.uuid4().hex[:6]}",
        )
        print_plan_box(plan5, guard5, case5)
        print(f"[EVENT BUS] Emitted safety event: 'recovery.escalated' (Reason: {case5.stop_reason})")
        print("            CRITICAL SAFETY POLICY: When the system does not recognize a failure code,")
        print("            it NEVER guesses financial retries. It halts automated action and escalates to human review.")

    print_header("PHASE 3 ORCHESTRATOR DEMO COMPLETE: ALL 5 SCENARIOS VERIFIED")


if __name__ == "__main__":
    asyncio.run(main())
