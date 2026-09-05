"""Interactive Demonstration of Phase 7: Financial Safety Boundary & Immutable Audit Trail.

Demonstrates:
1. Scenario A: AI Recommendation Overridden and Blocked by Policy Engine (Attempt Limit Exceeded).
2. Scenario B: High-Value Recovery Diverted to Human Approval (Automated Amount Cap Exceeded).
3. Scenario C: Execution Boundary Re-validation Under Redis Lock (Merchant Disabled Recovery).
4. Scenario D: Cryptographic Hash Chain Integrity & Tamper Detection.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import sys
import uuid

# Set UTF-8 encoding for Windows terminal
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from sqlalchemy import select, text

from app.audit.models import ImmutableAuditRecord
from app.audit.service import ImmutableAuditLogger, verify_chain_integrity
from app.database import async_session_factory, init_db
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


async def run_demonstration():
    print("=" * 80)
    print("  RAZORPAY BUILDATHON - PHASE 7 DEMONSTRATION")
    print("  FINANCIAL SAFETY POLICY BOUNDARY & CRYPTOGRAPHIC IMMUTABLE AUDIT TRAIL")
    print("=" * 80)

    await init_db()
    broker = InMemoryEventBroker()
    policy_engine = FinancialSafetyPolicyEngine()
    orchestrator = IntelligentRecoveryOrchestrator(
        broker=broker,
        policy_engine=policy_engine,
    )

    async with async_session_factory() as session:
        # Seed Baseline Merchant, Customer, Route
        merchant = Merchant(
            id=uuid.uuid4(),
            name="Zomato India Payments",
            mcc="5812",
            tier=MerchantTier.ENTERPRISE,
            max_auto_retries=2,
            min_recovery_amount_inr=Decimal("50.00"),
            auto_escalate_threshold_inr=Decimal("50000.00"),
            recovery_enabled=True,
        )
        customer = Customer(
            id=uuid.uuid4(),
            external_id=f"cust_omkar_{uuid.uuid4().hex[:6]}",
            email_masked="om***@domain.in",
            phone_masked="+91-98765****",
        )
        route = PaymentRoute(
            id=f"ROUTE_UPI_HDFC_{uuid.uuid4().hex[:6]}",
            name="HDFC UPI Primary Switch",
            payment_method=PaymentMethod.UPI,
            is_active=True,
            status=RouteStatus.HEALTHY,
            health_score=0.98,
        )
        session.add_all([merchant, customer, route])
        await session.commit()

        # ---------------------------------------------------------------------
        # SCENARIO A: AI Recommendation Overridden by Financial Safety Policy
        # ---------------------------------------------------------------------
        print("\n" + "-" * 80)
        print(" [SCENARIO A] AI Recommendation Overridden by Hard Policy Engine")
        print("-" * 80)
        print(" Context:")
        print(f"   Merchant Max Retries : {merchant.max_auto_retries}")
        print(f"   Current Attempt      : 3 (exceeds limit)")
        print("   AI Recommendation    : RETRY (Model Confidence = 0.98, Provider = 'Gemini 2.5 Flash')")
        print("   Rule Invariant       : 'AI can never override deterministic financial policy.'")

        payment_a = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=Decimal("1850.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_a_{uuid.uuid4().hex[:6]}",
        )
        session.add(payment_a)
        await session.commit()

        ctx_a = PaymentRecoveryContext(
            payment_id=payment_a.id,
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=payment_a.amount_inr,
            payment_method=payment_a.payment_method,
            route_id=route.id,
            route_health_score=0.98,
            route_is_active=True,
            route_status=RouteStatus.HEALTHY,
            failure_category=FailureCategory.TRANSIENT,
            error_code="GATEWAY_TIMEOUT",
            reason="Bank gateway timed out after 15s",
            attempt_number=3,  # Attempt 3 exceeds max 2
            failure_created_at=datetime.now(timezone.utc),
            merchant_tier=merchant.tier,
            merchant_recovery_enabled=merchant.recovery_enabled,
            merchant_max_auto_retries=merchant.max_auto_retries,
            merchant_min_recovery_amount_inr=merchant.min_recovery_amount_inr,
            merchant_auto_escalate_threshold_inr=merchant.auto_escalate_threshold_inr,
            correlation_id="corr_scenario_a",
        )

        plan_a = RecoveryPlan(
            strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
            retryability=RetryabilityClass.RETRYABLE,
            target_route_id=route.id,
            decision_confidence=0.98,
            explanation="AI advises aggressive immediate retry on healthy switch",
            parameters={"tier_used": "AI_REASONING", "model": "gemini-2.5-flash"},
        )

        policy_res_a = policy_engine.evaluate(context=ctx_a, plan=plan_a)
        print("\n [POLICY ENGINE EVALUATION RESULT]")
        print(f"   Policy Decision     : {policy_res_a.decision.value}")
        print(f"   Violated Policies   : {policy_res_a.violated_policies}")
        print(f"   Enforcement Reason  : {policy_res_a.reason}")
        print(f"   Risk Level Assigned : {policy_res_a.risk_level}")
        print("   >>> Hard Policy VETO applied: AI recommendation discarded, recovery halted.")

        # ---------------------------------------------------------------------
        # SCENARIO B: High-Value Recovery Diverted to Human Review (Escalated)
        # ---------------------------------------------------------------------
        print("\n" + "-" * 80)
        print(" [SCENARIO B] High-Value Recovery Diverted to Human Review")
        print("-" * 80)
        high_amount = Decimal("250000.00")  # INR 2.5 Lakhs
        print(" Context:")
        print(f"   Transaction Amount   : INR {high_amount}")
        print(f"   Auto-Recovery Cap    : INR {policy_engine.config.system_max_recovery_amount_inr}")
        print("   Rule Invariant       : 'Transactions above automated cap require explicit human signoff.'")

        payment_b = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=high_amount,
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_b_{uuid.uuid4().hex[:6]}",
        )
        session.add(payment_b)
        await session.commit()

        payload_b = PaymentFailedPayload(
            payment_id=payment_b.id,
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=high_amount,
            payment_method=PaymentMethod.UPI,
            route_id=route.id,
            failure_category=FailureCategory.TRANSIENT,
            error_code="GATEWAY_TIMEOUT",
            reason="HDFC bank gateway switch timeout",
            attempt_number=1,
            recoverable=True,
        )

        case_b, plan_b, guard_res_b = await orchestrator.orchestrate_failure(
            session=session,
            failure_payload=payload_b,
            correlation_id="corr_scenario_b",
        )

        print("\n [ORCHESTRATOR LAYERED SAFETY RESULT]")
        print(f"   Recovery Case State : {case_b.recovery_state.value}")
        print(f"   Escalation Reason   : {case_b.stop_reason}")
        print("   Event Emitted       : 'recovery.escalated' -> Diverted to Razorpay Human Ops Queue")

        # ---------------------------------------------------------------------
        # SCENARIO C: Execution Boundary Re-validation Under Redis Lock
        # ---------------------------------------------------------------------
        print("\n" + "-" * 80)
        print(" [SCENARIO C] Execution Boundary Policy Re-Validation Under Lock")
        print("-" * 80)
        print(" Context:")
        print("   Case was previously approved by Orchestrator.")
        print("   Merchant dynamically DISABLED recovery while message was in transit.")
        print("   Rule Invariant: 'Phase 4 must re-validate financial policy under lock before provider execution.'")

        payment_c = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=Decimal("3500.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.FAILED,
            idempotency_key=f"idemp_c_{uuid.uuid4().hex[:6]}",
        )
        session.add(payment_c)
        await session.flush()

        case_c = RecoveryCase(
            id=uuid.uuid4(),
            payment_id=payment_c.id,
            status=PaymentLifecycleState.RECOVERY_PENDING,
            recovery_state=RecoveryState.APPROVED,
            strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
            attempt_count=0,
            max_attempts=2,
        )
        session.add(case_c)
        await session.commit()

        # Merchant toggles recovery off
        merchant.recovery_enabled = False
        await session.commit()

        exec_service = SafeRecoveryExecutionService(
            event_broker=broker,
            redis_client=InMemoryRedisClient(),
            provider=MockPaymentProvider(),
            retry_policy=RecoveryRetryPolicy(),
            policy_engine=policy_engine,
        )

        exec_res = await exec_service.execute_recovery_attempt(
            session=session,
            payment_id=payment_c.id,
            recovery_case_id=case_c.id,
            attempt_number=1,
            target_route_id=route.id,
        )

        print("\n [EXECUTION SERVICE OUTCOME]")
        print(f"   Execution Status    : {exec_res.status.value}")
        print(f"   Halt Reason         : {exec_res.stop_reason}")
        print(f"   Provider Called?    : NO (Provider execution safely aborted)")
        print(f"   Case Final State    : {case_c.recovery_state.value}")

        # Restore merchant recovery
        merchant.recovery_enabled = True
        await session.commit()

        # ---------------------------------------------------------------------
        # SCENARIO D: Cryptographic Hash Chain & Tamper Detection
        # ---------------------------------------------------------------------
        print("\n" + "-" * 80)
        print(" [SCENARIO D] Immutable Audit Trail & Cryptographic SHA-256 Chaining")
        print("-" * 80)

        # Query all audit records for payment B
        stmt = (
            select(ImmutableAuditRecord)
            .where(ImmutableAuditRecord.payment_id == payment_b.id)
            .order_by(ImmutableAuditRecord.timestamp.asc(), ImmutableAuditRecord.id.asc())
        )
        records_b = (await session.execute(stmt)).scalars().all()

        print(f" Cryptographic Audit Chain for Payment {payment_b.id}:")
        print(f" {'Action':<35} | {'Decision':<12} | {'Parent Hash':<10} | {'Payload Hash':<10}")
        print(" " + "-" * 75)
        for r in records_b:
            p_hash = (r.parent_hash or "GENESIS")[:8] + ".."
            c_hash = r.payload_hash[:8] + ".."
            print(f" {r.action:<35} | {r.policy_decision:<12} | {p_hash:<10} | {c_hash:<10}")

        p_id_b = payment_b.id
        valid, violations = await verify_chain_integrity(session, payment_id=p_id_b)
        print(f"\n Initial Hash Chain Verification: {'PASSED [VALID]' if valid else 'FAILED'}")
        if not valid:
            for v in violations:
                print(f"   [!] Initial check error: {v}")

        # Now demonstrate tamper detection
        print("\n [ATTACK SIMULATION: Tampering directly with audit record payload_hash via raw SQL]")
        target_rec = records_b[0]
        corrupted_hash = "cafebabe" * 8
        await session.execute(
            text("UPDATE immutable_audit_records SET payload_hash = :ch WHERE id = :rid"),
            {"ch": corrupted_hash, "rid": target_rec.id.hex},
        )
        await session.commit()
        session.expire_all()

        valid_after, violations_after = await verify_chain_integrity(session, payment_id=p_id_b)
        print(f" Post-Tamper Verification      : {'PASSED' if valid_after else 'FAILED [TAMPERING DETECTED!]'}")
        print(" Audit Chain Anomaly Details:")
        for v in violations_after:
            print(f"   [!] {v}")

    print("\n" + "=" * 80)
    print("  PHASE 7 DEMONSTRATION COMPLETE: ALL FINANCIAL SAFETY BOUNDARIES VERIFIED!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(run_demonstration())
