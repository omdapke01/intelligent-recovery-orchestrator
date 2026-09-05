"""Safe Recovery Execution Service coordinating Redis locks, PostgreSQL idempotency,

payment provider execution, and event emission.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal
from app.audit.service import ImmutableAuditLogger
from app.config import settings
from app.events.broker import EventBroker
from app.events.schemas import (
    EventEnvelope,
    PaymentRetryRequestedPayload,
    PaymentSucceededPayload,
    RecoveryCompletedPayload,
    RecoveryFailedPayload,
    RecoveryStoppedPayload,
)
from app.execution.idempotency import PostgresIdempotencyBarrier
from app.execution.lock import RedisDistributedLock
from app.execution.provider import (
    DuplicateGatewayRequestException,
    MockPaymentProvider,
    PaymentExecutionRequest,
    ProviderOutcome,
    ProviderTimeoutException,
    ProviderUnavailableException,
)
from app.execution.redis_client import get_redis_client
from app.execution.retry_policy import RecoveryRetryPolicy
from app.models.base import utc_now
from app.models.enums import (
    AttemptStatus,
    FailureCategory,
    MerchantTier,
    PaymentLifecycleState,
    RecoveryState,
    RecoveryStrategy,
    RetryabilityClass,
    RouteStatus,
)
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.payment_failure import PaymentFailure
from app.models.payment_route import PaymentRoute
from app.models.recovery_case import RecoveryCase
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.orchestrator.state_machine import RecoveryStateMachine
from app.policy.engine import FinancialSafetyPolicyEngine
from app.policy.models import PolicyDecision

logger = logging.getLogger("iro.execution.service")


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    STOPPED = "STOPPED"
    LOCK_CONTENTION = "LOCK_CONTENTION"
    DUPLICATE_EXECUTION_BLOCKED = "DUPLICATE_EXECUTION_BLOCKED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    ERROR = "ERROR"


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    payment_id: uuid.UUID
    recovery_case_id: Optional[uuid.UUID] = None
    attempt_number: int = 1
    gateway_ref_id: Optional[str] = None
    error_code: Optional[str] = None
    stop_reason: Optional[str] = None
    backoff_sec: float = 0.0
    message: str = ""


class SafeRecoveryExecutionService:
    """Coordinates safe, isolated payment recovery execution.

    Invariants:
    1. Lock acquired BEFORE state transition (APPROVED -> EXECUTING only under lock).
    2. Redis lock key: lock:recovery:payment:{payment_id} with unique ownership token.
    3. PostgreSQL is the durable source of truth (not Redis).
    4. Unique idempotency key: recovery:{recovery_case_id}:attempt:{attempt_number}.
    5. Database unique constraint prevents concurrent duplicate execution; provider called exactly once.
    6. Execution duration strictly bounded below lock TTL.
    """

    def __init__(
        self,
        event_broker: EventBroker,
        redis_client: Optional[Any] = None,
        provider: Optional[MockPaymentProvider] = None,
        retry_policy: Optional[RecoveryRetryPolicy] = None,
        policy_engine: Optional[FinancialSafetyPolicyEngine] = None,
        lock_ttl_ms: Optional[int] = None,
    ):
        self.broker = event_broker
        self.redis = redis_client or get_redis_client()
        self.provider = provider or MockPaymentProvider()
        self.retry_policy = retry_policy or RecoveryRetryPolicy()
        self.policy_engine = policy_engine
        self.lock_ttl_ms = lock_ttl_ms or settings.REDIS_LOCK_TTL_MS

    async def execute_recovery_attempt(
        self,
        session: AsyncSession,
        payment_id: uuid.UUID,
        recovery_case_id: uuid.UUID,
        attempt_number: int,
        target_route_id: str,
        strategy: RecoveryStrategy = RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        worker_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Executes a single recovery attempt under atomic lock and idempotency barrier."""
        corr_id = correlation_id or f"corr_exec_{uuid.uuid4().hex[:8]}"

        # STEP 1: Acquire Distributed Lock (SET NX + TTL)
        lock = RedisDistributedLock.for_payment(
            redis_client=self.redis,
            payment_id=payment_id,
            ttl_ms=self.lock_ttl_ms,
            worker_id=worker_id,
        )
        acquired = await lock.acquire()
        if not acquired:
            logger.warning(
                f"[EXECUTION BLOCKED] Lock contention for payment {payment_id}. "
                f"Another worker is actively executing recovery."
            )
            return ExecutionResult(
                status=ExecutionStatus.LOCK_CONTENTION,
                payment_id=payment_id,
                recovery_case_id=recovery_case_id,
                attempt_number=attempt_number,
                message="Lock contention: another worker owns the recovery lock for this payment.",
            )

        try:
            # STEP 2: Load Payment and RecoveryCase from PostgreSQL (Authoritative Source)
            payment = await session.get(Payment, payment_id)
            if not payment:
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    message=f"Payment {payment_id} not found in database.",
                )

            case = await session.get(RecoveryCase, recovery_case_id)
            if not case:
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    message=f"RecoveryCase {recovery_case_id} not found in database.",
                )

            # Check if payment is already recovered, succeeded, or in-flight
            if payment.status in {PaymentLifecycleState.SUCCESS, PaymentLifecycleState.RECOVERED}:
                logger.info(f"[EXECUTION ALREADY COMPLETED] Payment {payment_id} is already in state {payment.status.value}.")
                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    action="STALE_RECOVERY_DISCARDED",
                    strategy=strategy.value,
                    policy_decision="ALREADY_TERMINAL_OR_SUCCEEDED",
                    result="DISCARDED",
                    actor_source="recovery-execution-service",
                    escalation_reason=f"Payment already {payment.status.value}; discarded stale retry attempt {attempt_number}.",
                )
                await session.commit()
                return ExecutionResult(
                    status=ExecutionStatus.ALREADY_COMPLETED,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    attempt_number=attempt_number,
                    message=f"Payment already in terminal state {payment.status.value}; stale recovery aborted.",
                )

            if payment.status in {PaymentLifecycleState.PROCESSING, PaymentLifecycleState.CREATED}:
                logger.warning(f"[EXECUTION IN_FLIGHT] Payment {payment_id} is currently {payment.status.value}. Aborting duplicate attempt.")
                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    action="IN_FLIGHT_EXECUTION_DISCARDED",
                    strategy=strategy.value,
                    policy_decision="IN_FLIGHT_TRANSACTION_LOCKED",
                    result="DISCARDED",
                    actor_source="recovery-execution-service",
                    escalation_reason=f"Payment is currently {payment.status.value}; cannot initiate concurrent retry attempt {attempt_number}.",
                )
                await session.commit()
                return ExecutionResult(
                    status=ExecutionStatus.DUPLICATE_EXECUTION_BLOCKED,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    attempt_number=attempt_number,
                    message=f"Payment is currently in-flight ({payment.status.value}); retry aborted to prevent duplicate debits.",
                )

            # STEP 3: State Machine Validation & Transition (APPROVED -> EXECUTING)
            # Must ONLY transition after lock is acquired
            if case.recovery_state in {RecoveryState.RECOVERY_SUCCEEDED, RecoveryState.STOPPED, RecoveryState.ESCALATED}:
                logger.info(f"[EXECUTION TERMINAL] RecoveryCase {case.id} is in terminal state {case.recovery_state.value}.")
                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    action="STALE_RECOVERY_DISCARDED",
                    strategy=strategy.value,
                    policy_decision="ALREADY_TERMINAL_OR_SUCCEEDED",
                    result="DISCARDED",
                    actor_source="recovery-execution-service",
                    escalation_reason=f"Recovery case is already in terminal state {case.recovery_state.value}; discarded retry attempt {attempt_number}.",
                )
                await session.commit()
                return ExecutionResult(
                    status=ExecutionStatus.ALREADY_COMPLETED,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    attempt_number=attempt_number,
                    message=f"Recovery case is in terminal state {case.recovery_state.value}.",
                )

            # STEP 3b: Execution Boundary Policy Re-Validation Under Redis Lock
            merchant = await session.get(Merchant, payment.merchant_id)
            if self.policy_engine:
                route_res = await session.execute(select(PaymentRoute).where(PaymentRoute.id == target_route_id))
                route = route_res.scalar_one_or_none()
                route_health = route.health_score if route else 1.0
                route_is_active = route.is_active if route else True
                route_status = route.status if route else RouteStatus.HEALTHY

                ctx = PaymentRecoveryContext(
                    payment_id=payment.id,
                    merchant_id=payment.merchant_id,
                    customer_id=payment.customer_id,
                    amount_inr=payment.amount_inr,
                    payment_method=payment.payment_method,
                    route_id=target_route_id,
                    route_health_score=route_health,
                    route_is_active=route_is_active,
                    route_status=route_status,
                    failure_category=FailureCategory.TRANSIENT,
                    error_code="RETRY_EXECUTION",
                    reason="Pre-execution authorization revalidation",
                    attempt_number=attempt_number,
                    failure_created_at=case.created_at or utc_now(),
                    merchant_tier=merchant.tier if merchant else MerchantTier.GROWTH,
                    merchant_recovery_enabled=merchant.recovery_enabled if merchant else True,
                    merchant_max_auto_retries=merchant.max_auto_retries if merchant else 2,
                    merchant_min_recovery_amount_inr=merchant.min_recovery_amount_inr if merchant else Decimal("50.00"),
                    merchant_auto_escalate_threshold_inr=merchant.auto_escalate_threshold_inr if merchant else Decimal("50000.00"),
                    correlation_id=corr_id,
                )
                plan = RecoveryPlan(
                    strategy=strategy,
                    retryability=RetryabilityClass.RETRYABLE,
                    target_route_id=target_route_id,
                )
                policy_res = self.policy_engine.evaluate(context=ctx, plan=plan, recovery_case=case)
                if policy_res.decision != PolicyDecision.PERMITTED:
                    logger.warning(
                        f"[EXECUTION BLOCKED BY POLICY] Policy revalidation failed for payment {payment_id}. "
                        f"Decision: {policy_res.decision.value}, Reason: {policy_res.reason}"
                    )
                    is_escalate = (policy_res.decision == PolicyDecision.REQUIRES_HUMAN_APPROVAL)
                    target_state = RecoveryState.ESCALATED if is_escalate else RecoveryState.STOPPED
                    RecoveryStateMachine.transition(case, target_state, reason=policy_res.reason)
                    case.stop_reason = policy_res.reason

                    await ImmutableAuditLogger.append_record(
                        session=session,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        policy_evaluation_id=policy_res.evaluation_id,
                        policy_version=policy_res.policy_version,
                        action="EXECUTION_BOUNDARY_POLICY_REVALIDATION",
                        strategy=strategy.value,
                        policy_decision=policy_res.decision.value,
                        policy_violations=policy_res.violated_policies,
                        result="STOPPED" if not is_escalate else "ESCALATED",
                        actor_source="recovery-execution-service",
                        escalation_reason=policy_res.reason,
                    )
                    await session.commit()
                    return ExecutionResult(
                        status=ExecutionStatus.STOPPED,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        attempt_number=attempt_number,
                        stop_reason=policy_res.reason,
                        message=f"Execution blocked by policy engine: {policy_res.reason}",
                    )
            elif merchant and not merchant.recovery_enabled:
                logger.warning(f"[EXECUTION BLOCKED] Merchant {merchant.id} has recovery disabled at execution boundary.")
                RecoveryStateMachine.transition(case, RecoveryState.STOPPED, reason="MERCHANT_RECOVERY_DISABLED")
                case.stop_reason = "MERCHANT_RECOVERY_DISABLED"
                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    action="EXECUTION_BOUNDARY_POLICY_REVALIDATION",
                    strategy=strategy.value,
                    policy_decision="DENIED",
                    policy_violations=["MerchantRestrictionsRule"],
                    result="STOPPED",
                    actor_source="recovery-execution-service",
                    escalation_reason="MERCHANT_RECOVERY_DISABLED",
                )
                await session.commit()
                return ExecutionResult(
                    status=ExecutionStatus.STOPPED,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    attempt_number=attempt_number,
                    stop_reason="MERCHANT_RECOVERY_DISABLED",
                    message="Merchant has recovery disabled at execution boundary.",
                )

            RecoveryStateMachine.validate_transition(
                case.recovery_state,
                RecoveryState.EXECUTING,
                reason="Acquired distributed lock and initiating execution",
            )
            case.recovery_state = RecoveryState.EXECUTING
            case.attempt_count = attempt_number
            await session.flush()

            # STEP 4: PostgreSQL Idempotency Barrier
            idempotency_key = PostgresIdempotencyBarrier.generate_idempotency_key(
                recovery_case_id=recovery_case_id,
                attempt_number=attempt_number,
            )

            reservation = await PostgresIdempotencyBarrier.reserve_attempt(
                session=session,
                payment_id=payment_id,
                attempt_number=attempt_number,
                idempotency_key=idempotency_key,
                route_id=target_route_id,
                payment_method=payment.payment_method,
            )

            if not reservation.is_new:
                logger.warning(
                    f"[IDEMPOTENCY DUPLICATE BLOCKED] Provider execution skipped for "
                    f"key '{idempotency_key}'. Reason: {reservation.reason}"
                )
                return ExecutionResult(
                    status=ExecutionStatus.DUPLICATE_EXECUTION_BLOCKED,
                    payment_id=payment_id,
                    recovery_case_id=recovery_case_id,
                    attempt_number=attempt_number,
                    message=f"Duplicate execution blocked by DB idempotency: {reservation.reason}",
                )

            attempt = reservation.attempt

            # STEP 5: Execute Against Payment Provider Sandbox
            exec_req = PaymentExecutionRequest(
                payment_id=payment_id,
                attempt_number=attempt_number,
                idempotency_key=idempotency_key,
                amount_inr=payment.amount_inr,
                route_id=target_route_id,
                payment_method=payment.payment_method,
            )

            provider_response = None
            failure_code = None
            failure_reason = None

            try:
                provider_response = await self.provider.execute_payment(exec_req)
                if not provider_response.success:
                    failure_code = provider_response.error_code or "PAYMENT_FAILED"
                    failure_reason = provider_response.error_message or "Gateway declined payment"
            except ProviderTimeoutException as err:
                failure_code = "GATEWAY_TIMEOUT"
                failure_reason = str(err)
            except ProviderUnavailableException as err:
                failure_code = "DOWNSTREAM_503"
                failure_reason = str(err)
            except DuplicateGatewayRequestException as err:
                failure_code = "DUPLICATE_IDEMPOTENCY_KEY"
                failure_reason = str(err)
            except Exception as err:
                failure_code = "UNKNOWN_ERROR"
                failure_reason = str(err)

            # STEP 6: Process Outcome & Persist Authoritative State
            if provider_response and provider_response.success:
                # --- SUCCESS PATH ---
                attempt.status = AttemptStatus.SUCCESS
                attempt.gateway_ref_id = provider_response.gateway_ref_id
                attempt.latency_ms = provider_response.latency_ms
                attempt.completed_at = utc_now()

                payment.status = PaymentLifecycleState.RECOVERED
                case.recovery_state = RecoveryState.RECOVERY_SUCCEEDED
                case.completed_at = utc_now()

                await ImmutableAuditLogger.append_record(
                    session=session,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    action="PAYMENT_RECOVERY_EXECUTION_SUCCESS",
                    strategy=strategy.value,
                    policy_decision="PERMITTED",
                    result="SUCCESS",
                    actor_source="recovery-execution-service",
                )

                await session.commit()

                # Emit Success Events
                e_succ = EventEnvelope(
                    event_type="payment.succeeded",
                    correlation_id=corr_id,
                    producer="recovery-execution-service",
                    data=PaymentSucceededPayload(
                        payment_id=payment.id,
                        merchant_id=payment.merchant_id,
                        amount_inr=payment.amount_inr,
                        attempt_number=attempt_number,
                        route_id=target_route_id,
                        gateway_ref_id=attempt.gateway_ref_id,
                    ).model_dump(mode="json"),
                )
                await self.broker.publish(
                    topic="payment.events",
                    value=e_succ.model_dump(mode="json"),
                    key=str(payment.merchant_id),
                )

                e_comp = EventEnvelope(
                    event_type="recovery.completed",
                    correlation_id=corr_id,
                    producer="recovery-execution-service",
                    data=RecoveryCompletedPayload(
                        recovery_case_id=case.id,
                        payment_id=payment.id,
                        recovered_amount_inr=payment.amount_inr,
                        total_attempts=attempt_number,
                    ).model_dump(mode="json"),
                )
                await self.broker.publish(
                    topic="payment.events",
                    value=e_comp.model_dump(mode="json"),
                    key=str(payment.merchant_id),
                )

                logger.info(f"[RECOVERY SUCCEEDED] Payment {payment.id} recovered on attempt {attempt_number}!")
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    payment_id=payment.id,
                    recovery_case_id=case.id,
                    attempt_number=attempt_number,
                    gateway_ref_id=attempt.gateway_ref_id,
                    message="Payment successfully recovered.",
                )

            else:
                # --- FAILURE PATH ---
                attempt.status = AttemptStatus.FAILED
                attempt.completed_at = utc_now()

                # Fetch merchant max retries
                merchant = await session.get(Merchant, payment.merchant_id)
                max_retries = merchant.max_auto_retries if merchant else settings.DEFAULT_MAX_AUTO_RETRIES

                # Evaluate Retry Policy & Stopping Conditions
                should_retry, backoff_sec, stop_reason = self.retry_policy.evaluate_next_retry(
                    current_attempt=attempt_number,
                    max_retries=max_retries,
                    error_code=failure_code,
                )

                payment_failure = PaymentFailure(
                    id=uuid.uuid4(),
                    attempt_id=attempt.id,
                    payment_id=payment.id,
                    failure_category=FailureCategory.TRANSIENT if self.retry_policy.is_retryable(failure_code) else FailureCategory.PERMANENT,
                    error_code=failure_code or "PAYMENT_FAILED",
                    reason=failure_reason or "Unknown failure",
                    recoverable=should_retry,
                    suggested_backoff_sec=int(backoff_sec),
                )
                session.add(payment_failure)

                if should_retry:
                    case.recovery_state = RecoveryState.RECOVERY_FAILED
                    await ImmutableAuditLogger.append_record(
                        session=session,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        action="PAYMENT_RECOVERY_EXECUTION_ATTEMPT_FAILED",
                        strategy=strategy.value,
                        policy_decision="PERMITTED",
                        result="RETRY_SCHEDULED",
                        actor_source="recovery-execution-service",
                        escalation_reason=failure_code,
                    )
                    await session.commit()

                    # Emit payment.retry_requested via Phase 2 Kafka retry / backoff flow
                    next_attempt = attempt_number + 1
                    scheduled_time = utc_now() + timedelta(seconds=backoff_sec)
                    e_retry = EventEnvelope(
                        event_type="payment.retry_requested",
                        correlation_id=corr_id,
                        producer="recovery-execution-service",
                        data=PaymentRetryRequestedPayload(
                            payment_id=payment.id,
                            recovery_case_id=case.id,
                            attempt_number=next_attempt,
                            target_route_id=target_route_id,
                            strategy=strategy,
                            scheduled_at=scheduled_time,
                        ).model_dump(mode="json"),
                    )
                    await self.broker.publish(
                        topic="payment.events",
                        value=e_retry.model_dump(mode="json"),
                        key=str(payment.merchant_id),
                    )

                    logger.info(
                        f"[RETRY SCHEDULED] Attempt {attempt_number} failed ({failure_code}). "
                        f"Next attempt {next_attempt} scheduled with {backoff_sec:.1f}s backoff."
                    )
                    return ExecutionResult(
                        status=ExecutionStatus.RETRY_SCHEDULED,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        attempt_number=attempt_number,
                        error_code=failure_code,
                        backoff_sec=backoff_sec,
                        message=f"Retry scheduled with backoff {backoff_sec:.1f}s.",
                    )
                else:
                    # Retries exhausted or non-retryable failure
                    case.recovery_state = RecoveryState.STOPPED
                    case.stop_reason = stop_reason
                    await ImmutableAuditLogger.append_record(
                        session=session,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        action="PAYMENT_RECOVERY_EXECUTION_STOPPED",
                        strategy=strategy.value,
                        policy_decision="PERMITTED",
                        result="STOPPED",
                        actor_source="recovery-execution-service",
                        escalation_reason=stop_reason,
                    )
                    await session.commit()

                    e_fail = EventEnvelope(
                        event_type="recovery.failed",
                        correlation_id=corr_id,
                        producer="recovery-execution-service",
                        data=RecoveryFailedPayload(
                            recovery_case_id=case.id,
                            payment_id=payment.id,
                            attempt_number=attempt_number,
                            error_code=failure_code or "UNKNOWN",
                            reason=failure_reason or "Exhausted",
                        ).model_dump(mode="json"),
                    )
                    await self.broker.publish(
                        topic="payment.events",
                        value=e_fail.model_dump(mode="json"),
                        key=str(payment.merchant_id),
                    )

                    e_stop = EventEnvelope(
                        event_type="recovery.stopped",
                        correlation_id=corr_id,
                        producer="recovery-execution-service",
                        data=RecoveryStoppedPayload(
                            recovery_case_id=case.id,
                            payment_id=payment.id,
                            stop_reason=stop_reason or "STOPPED",
                        ).model_dump(mode="json"),
                    )
                    await self.broker.publish(
                        topic="payment.events",
                        value=e_stop.model_dump(mode="json"),
                        key=str(payment.merchant_id),
                    )

                    logger.info(
                        f"[RECOVERY STOPPED] Halted for payment {payment.id}. "
                        f"Reason: {stop_reason}"
                    )
                    return ExecutionResult(
                        status=ExecutionStatus.STOPPED,
                        payment_id=payment.id,
                        recovery_case_id=case.id,
                        attempt_number=attempt_number,
                        error_code=failure_code,
                        stop_reason=stop_reason,
                        message=f"Recovery stopped: {stop_reason}",
                    )

        finally:
            # STEP 7: Always Release Lock with Token Verification
            await lock.release()
