"""Intelligent Recovery Orchestrator coordinating deterministic recovery planning."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
from typing import Any, Optional, Tuple
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import ImmutableAuditLogger
from app.events.broker import EventBroker
from app.events.schemas import (
    EventEnvelope,
    NotificationRequestedPayload,
    PaymentFailedPayload,
    PaymentRetryRequestedPayload,
    RecoveryEscalatedPayload,
    RecoveryStartedPayload,
    RecoveryStoppedPayload,
)
from app.models import (
    Customer,
    Merchant,
    MerchantTier,
    Payment,
    PaymentLifecycleState,
    PaymentRoute,
    RecoveryCase,
    RecoveryState,
    RecoveryStrategy,
    RetryabilityClass,
    RouteStatus,
)
from app.orchestrator.classifier import DeterministicFailureClassifier
from app.orchestrator.guard import DeterministicRecoveryGuard
from app.orchestrator.models import (
    GuardEvaluationResult,
    PaymentRecoveryContext,
    RecoveryPlan,
)
from app.orchestrator.state_machine import RecoveryStateMachine
from app.orchestrator.strategy_selector import DeterministicStrategySelector
from app.policy.engine import FinancialSafetyPolicyEngine
from app.policy.models import PolicyDecision

logger = logging.getLogger(__name__)


class IntelligentRecoveryOrchestrator:
    """
    Intelligent Recovery Orchestrator (IRO) decision engine.
    Executes steps 1-8 of recovery planning, coordinating layered logical guards
    and hard financial safety policies before emitting Phase 4 intent events.
    """

    PRODUCER_NAME = "intelligent-recovery-orchestrator"
    TOPIC = "payment.events"

    def __init__(
        self,
        broker: EventBroker,
        decision_engine: Optional[Any] = None,
        policy_engine: Optional[FinancialSafetyPolicyEngine] = None,
    ):
        self.broker = broker
        self.decision_engine = decision_engine
        self.policy_engine = policy_engine or FinancialSafetyPolicyEngine()


    async def orchestrate_failure(
        self,
        session: AsyncSession,
        failure_payload: PaymentFailedPayload,
        correlation_id: str,
        causation_id: Optional[str] = None,
    ) -> Tuple[RecoveryCase, RecoveryPlan, GuardEvaluationResult]:
        """
        Orchestrate a failed payment into a validated, explainable recovery plan.
        Persists recovery state transitions and emits downstream intent events.
        """
        payment_id = failure_payload.payment_id

        # -------------------------------------------------------------
        # STEP 1: Create or Load RecoveryCase
        # -------------------------------------------------------------
        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.payment_id == payment_id)
        )
        case = case_res.scalar_one_or_none()

        if not case:
            case = RecoveryCase(
                id=uuid.uuid4(),
                payment_id=payment_id,
                status=PaymentLifecycleState.RECOVERY_PENDING,
                recovery_state=RecoveryState.FAILED,
                strategy=RecoveryStrategy.NONE,
                attempt_count=max(0, failure_payload.attempt_number - 1),
                started_at=datetime.now(timezone.utc),
            )
            session.add(case)
            await session.flush()
        else:
            # Case exists: reset to FAILED for new failure orchestration cycle
            case.recovery_state = RecoveryState.FAILED

        # -------------------------------------------------------------
        # STEP 2: Retrieve Minimum Context (PaymentRecoveryContext)
        # -------------------------------------------------------------
        context = await self._build_context(session, failure_payload, case, correlation_id, causation_id)

        # Update case max attempts from merchant configuration
        case.max_attempts = context.merchant_max_auto_retries

        # -------------------------------------------------------------
        # STEP 3: Classify Known Failure Types
        # -------------------------------------------------------------
        retryability = DeterministicFailureClassifier.classify(
            error_code=context.error_code,
            failure_category=context.failure_category,
        )
        RecoveryStateMachine.transition(
            case,
            RecoveryState.CLASSIFIED,
            reason=f"Classified as {retryability.value} from {context.error_code}",
        )

        # -------------------------------------------------------------
        # STEP 4: Select Initial Strategy & Generate Explainable Plan
        # -------------------------------------------------------------
        if self.decision_engine:
            import inspect
            sig = inspect.signature(self.decision_engine.decide_recovery_plan)
            if "session" in sig.parameters:
                plan = await self.decision_engine.decide_recovery_plan(
                    context, session=session, recovery_case_id=case.id
                )
            else:
                plan = await self.decision_engine.decide_recovery_plan(context)
        else:
            plan = DeterministicStrategySelector.select_strategy(context, retryability)

        RecoveryStateMachine.transition(
            case,
            RecoveryState.RECOVERY_PLANNED,
            reason=f"Selected strategy {plan.strategy.value}",
        )
        case.strategy = plan.strategy
        case.plan_details = plan.to_dict()

        # -------------------------------------------------------------
        # STEP 5: Validate Strategy with Phase 3 Guard AND Phase 7 Policy Engine
        # -------------------------------------------------------------
        RecoveryStateMachine.transition(
            case,
            RecoveryState.GUARD_PENDING,
            reason="Evaluating logical recovery guard and financial safety policy",
        )


        now = datetime.now(timezone.utc)
        guard_result = DeterministicRecoveryGuard.evaluate(context, plan, now=now)

        # STAGE 1: Phase 3 Logical Recovery Safety Guard
        if guard_result.is_escalated:
            RecoveryStateMachine.transition(
                case,
                RecoveryState.ESCALATED,
                reason=guard_result.stop_reason or "Escalated to manual review by guard",
            )
            case.status = PaymentLifecycleState.ESCALATED
            case.stop_reason = guard_result.stop_reason
            case.completed_at = now

            await ImmutableAuditLogger.append_record(
                session=session,
                payment_id=context.payment_id,
                recovery_case_id=case.id,
                action="RECOVERY_PLAN_LOGICAL_GUARD",
                strategy=plan.strategy.value,
                model_used=plan.parameters.get("tier_used"),
                model_confidence=plan.decision_confidence,
                policy_decision="REQUIRES_HUMAN_APPROVAL",
                result="ESCALATED",
                actor_source=self.PRODUCER_NAME,
                escalation_reason=guard_result.stop_reason,
            )
            await session.commit()
            await self._emit_escalated(case, context, guard_result.stop_reason or "MANUAL_REVIEW")

        elif not guard_result.is_approved:
            RecoveryStateMachine.transition(
                case,
                RecoveryState.STOPPED,
                reason=guard_result.stop_reason or "Halted by logical recovery guard",
            )
            case.status = PaymentLifecycleState.STOPPED
            case.stop_reason = guard_result.stop_reason
            case.completed_at = now

            await ImmutableAuditLogger.append_record(
                session=session,
                payment_id=context.payment_id,
                recovery_case_id=case.id,
                action="RECOVERY_PLAN_LOGICAL_GUARD",
                strategy=plan.strategy.value,
                model_used=plan.parameters.get("tier_used"),
                model_confidence=plan.decision_confidence,
                policy_decision="DENIED",
                result="STOPPED",
                actor_source=self.PRODUCER_NAME,
                escalation_reason=guard_result.stop_reason,
            )
            await session.commit()
            await self._emit_stopped(case, context, guard_result.stop_reason or "STOPPED_BY_GUARD")

        else:
            # STAGE 2: Phase 7 Financial Safety Policy Authorization
            policy_result = self.policy_engine.evaluate(context, plan, recovery_case=case, now=now)

            if policy_result.decision == PolicyDecision.DENIED:
                RecoveryStateMachine.transition(
                    case,
                    RecoveryState.STOPPED,
                    reason=policy_result.reason,
                )
                case.status = PaymentLifecycleState.STOPPED
                case.stop_reason = policy_result.reason
                case.completed_at = now
                guard_result.is_approved = False
                guard_result.stop_reason = policy_result.reason

                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=context.payment_id,
                    recovery_case_id=case.id,
                    policy_evaluation_id=policy_result.evaluation_id,
                    policy_version=policy_result.policy_version,
                    action="FINANCIAL_SAFETY_POLICY_AUTHORIZATION",
                    strategy=plan.strategy.value,
                    model_used=plan.parameters.get("tier_used"),
                    model_confidence=plan.decision_confidence,
                    policy_decision=policy_result.decision.value,
                    policy_violations=policy_result.violated_policies,
                    result="STOPPED",
                    actor_source=self.PRODUCER_NAME,
                    escalation_reason=policy_result.reason,
                )
                await session.commit()
                await self._emit_stopped(case, context, policy_result.reason)

            elif policy_result.decision == PolicyDecision.REQUIRES_HUMAN_APPROVAL:
                RecoveryStateMachine.transition(
                    case,
                    RecoveryState.ESCALATED,
                    reason=policy_result.reason,
                )
                case.status = PaymentLifecycleState.ESCALATED
                case.stop_reason = policy_result.reason
                case.completed_at = now
                guard_result.is_approved = False
                guard_result.is_escalated = True
                guard_result.stop_reason = policy_result.reason

                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=context.payment_id,
                    recovery_case_id=case.id,
                    policy_evaluation_id=policy_result.evaluation_id,
                    policy_version=policy_result.policy_version,
                    action="FINANCIAL_SAFETY_POLICY_AUTHORIZATION",
                    strategy=plan.strategy.value,
                    model_used=plan.parameters.get("tier_used"),
                    model_confidence=plan.decision_confidence,
                    policy_decision=policy_result.decision.value,
                    policy_violations=policy_result.violated_policies,
                    result="ESCALATED",
                    actor_source=self.PRODUCER_NAME,
                    escalation_reason=policy_result.reason,
                )
                await session.commit()
                await self._emit_escalated(case, context, policy_result.reason)

            else:
                # Both Guard AND Policy Engine Approved!
                RecoveryStateMachine.transition(
                    case,
                    RecoveryState.APPROVED,
                    reason="Passed both logical recovery guard and financial safety policy",
                )
                case.status = PaymentLifecycleState.RECOVERY_PENDING

                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=context.payment_id,
                    recovery_case_id=case.id,
                    policy_evaluation_id=policy_result.evaluation_id,
                    policy_version=policy_result.policy_version,
                    action="FINANCIAL_SAFETY_POLICY_AUTHORIZATION",
                    strategy=plan.strategy.value,
                    model_used=plan.parameters.get("tier_used"),
                    model_confidence=plan.decision_confidence,
                    policy_decision=policy_result.decision.value,
                    policy_violations=[],
                    result="APPROVED",
                    actor_source=self.PRODUCER_NAME,
                )
                await session.commit()
                await self._emit_approved(case, context, plan)

        return case, plan, guard_result


    async def _build_context(
        self,
        session: AsyncSession,
        payload: PaymentFailedPayload,
        case: RecoveryCase,
        correlation_id: str,
        causation_id: Optional[str],
    ) -> PaymentRecoveryContext:
        """Fetch minimum required relational entities and assemble PaymentRecoveryContext."""
        pay_res = await session.execute(select(Payment).where(Payment.id == payload.payment_id))
        payment = pay_res.scalar_one_or_none()

        amount_inr = payment.amount_inr if payment else payload.amount_inr
        payment_method = payment.payment_method if payment else payload.payment_method
        merchant_id = payment.merchant_id if payment else payload.merchant_id
        customer_id = payment.customer_id if payment else payload.customer_id

        # Merchant context
        merch_res = await session.execute(select(Merchant).where(Merchant.id == merchant_id))
        merchant = merch_res.scalar_one_or_none()
        merchant_tier = merchant.tier if merchant else MerchantTier.GROWTH
        merchant_recovery_enabled = merchant.recovery_enabled if merchant else True
        merchant_max_retries = merchant.max_auto_retries if merchant else 2
        merchant_min_recovery = merchant.min_recovery_amount_inr if merchant else Decimal("50.00")
        merchant_auto_escalate = merchant.auto_escalate_threshold_inr if merchant else Decimal("50000.00")

        # Route context
        route_res = await session.execute(select(PaymentRoute).where(PaymentRoute.id == payload.route_id))
        route = route_res.scalar_one_or_none()
        route_health = route.health_score if route else 1.0
        route_is_active = route.is_active if route else True
        route_status = route.status if route else RouteStatus.HEALTHY

        # Alternative routes
        alt_routes_stmt = select(PaymentRoute.id).where(
            PaymentRoute.payment_method == payment_method,
            PaymentRoute.is_active == True,
            PaymentRoute.id != payload.route_id,
            PaymentRoute.status == RouteStatus.HEALTHY,
        )
        alt_res = await session.execute(alt_routes_stmt)
        alt_route_ids = list(alt_res.scalars().all())

        return PaymentRecoveryContext(
            payment_id=payload.payment_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            amount_inr=amount_inr,
            payment_method=payment_method,
            route_id=payload.route_id,
            route_health_score=route_health,
            route_is_active=route_is_active,
            route_status=route_status,
            failure_category=payload.failure_category,
            error_code=payload.error_code,
            reason=payload.reason,
            attempt_number=payload.attempt_number,
            failure_created_at=datetime.now(timezone.utc),
            merchant_tier=merchant_tier,
            merchant_recovery_enabled=merchant_recovery_enabled,
            merchant_max_auto_retries=merchant_max_retries,
            merchant_min_recovery_amount_inr=merchant_min_recovery,
            merchant_auto_escalate_threshold_inr=merchant_auto_escalate,
            available_alternative_routes=alt_route_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payment_status=payment.status if payment else None,
        )

    async def _emit_approved(
        self,
        case: RecoveryCase,
        ctx: PaymentRecoveryContext,
        plan: RecoveryPlan,
    ) -> None:
        """Emit recovery.started and downstream intent event."""
        # 1. Emit recovery.started
        e_started = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="recovery.started",
            producer=self.PRODUCER_NAME,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.causation_id,
            data=RecoveryStartedPayload(
                recovery_case_id=case.id,
                payment_id=ctx.payment_id,
                merchant_id=ctx.merchant_id,
                strategy=plan.strategy,
                attempt_count=case.attempt_count,
            ).model_dump(mode="json"),
        )
        await self.broker.publish(self.TOPIC, e_started.model_dump(mode="json"), key=str(ctx.merchant_id))

        # 2. Emit customer notification request
        template = plan.notification_template or "PAYMENT_FAILED_RECOVERY_STARTED"
        channel = plan.notification_channel or "SMS"
        e_notif = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="notification.requested",
            producer=self.PRODUCER_NAME,
            correlation_id=ctx.correlation_id,
            causation_id=str(e_started.event_id),
            data=NotificationRequestedPayload(
                notification_id=uuid.uuid4(),
                customer_id=ctx.customer_id,
                payment_id=ctx.payment_id,
                channel=channel,
                template=template,
                payload={
                    "amount_inr": float(ctx.amount_inr),
                    "error_code": ctx.error_code,
                    "explanation": plan.explanation,
                    "strategy": plan.strategy.value,
                },
            ).model_dump(mode="json"),
        )
        await self.broker.publish(self.TOPIC, e_notif.model_dump(mode="json"), key=str(ctx.customer_id) if ctx.customer_id else None)

        # 3. Emit payment.retry_requested for Phase 4 executor if retry strategy
        if plan.strategy in (RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF, RecoveryStrategy.ROUTE_FAILOVER):
            target_route = plan.target_route_id or ctx.route_id
            delay_sec = plan.suggested_backoff_sec or 0.0
            scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay_sec)

            e_retry = EventEnvelope(
                event_id=uuid.uuid4(),
                event_type="payment.retry_requested",
                producer=self.PRODUCER_NAME,
                correlation_id=ctx.correlation_id,
                causation_id=str(e_started.event_id),
                data=PaymentRetryRequestedPayload(
                    payment_id=ctx.payment_id,
                    recovery_case_id=case.id,
                    attempt_number=ctx.attempt_number + 1,
                    target_route_id=target_route,
                    strategy=plan.strategy,
                    scheduled_at=scheduled_at,
                ).model_dump(mode="json"),
            )
            await self.broker.publish(self.TOPIC, e_retry.model_dump(mode="json"), key=str(ctx.merchant_id))

    async def _emit_stopped(
        self,
        case: RecoveryCase,
        ctx: PaymentRecoveryContext,
        stop_reason: str,
    ) -> None:
        """Emit recovery.stopped event."""
        e_stopped = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="recovery.stopped",
            producer=self.PRODUCER_NAME,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.causation_id,
            data=RecoveryStoppedPayload(
                recovery_case_id=case.id,
                payment_id=ctx.payment_id,
                stop_reason=stop_reason,
            ).model_dump(mode="json"),
        )
        await self.broker.publish(self.TOPIC, e_stopped.model_dump(mode="json"), key=str(ctx.merchant_id))

    async def _emit_escalated(
        self,
        case: RecoveryCase,
        ctx: PaymentRecoveryContext,
        escalation_reason: str,
    ) -> None:
        """Emit recovery.escalated event."""
        e_escalated = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="recovery.escalated",
            producer=self.PRODUCER_NAME,
            correlation_id=ctx.correlation_id,
            causation_id=ctx.causation_id,
            data=RecoveryEscalatedPayload(
                recovery_case_id=case.id,
                payment_id=ctx.payment_id,
                escalation_reason=escalation_reason,
                amount_inr=ctx.amount_inr,
            ).model_dump(mode="json"),
        )
        await self.broker.publish(self.TOPIC, e_escalated.model_dump(mode="json"), key=str(ctx.merchant_id))
