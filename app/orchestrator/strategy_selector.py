"""Deterministic Strategy Selector building explainable recovery plans based on context."""

import logging
from typing import Optional

from app.models.enums import RecoveryStrategy, RetryabilityClass, RouteStatus
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan

logger = logging.getLogger(__name__)


class DeterministicStrategySelector:
    """
    Selects the initial recovery strategy deterministically without predictive ML.
    Produces an explainable RecoveryPlan detailing the selected strategy, parameters,
    and a human-readable justification.
    """

    BASE_BACKOFF_SECONDS = 15.0

    @classmethod
    def select_strategy(
        cls,
        context: PaymentRecoveryContext,
        retryability: RetryabilityClass,
    ) -> RecoveryPlan:
        """
        Evaluate context and retryability class to construct a deterministic RecoveryPlan.
        """
        if retryability == RetryabilityClass.RETRYABLE:
            return cls._handle_retryable(context)

        elif retryability == RetryabilityClass.CUSTOMER_ACTION_REQUIRED:
            return cls._handle_customer_action(context)

        elif retryability == RetryabilityClass.NON_RETRYABLE:
            return cls._handle_non_retryable(context)

        else:
            # RetryabilityClass.UNKNOWN
            return cls._handle_unknown(context)

    @classmethod
    def _handle_retryable(cls, ctx: PaymentRecoveryContext) -> RecoveryPlan:
        is_route_healthy = (
            ctx.route_is_active
            and ctx.route_status == RouteStatus.HEALTHY
            and ctx.route_health_score >= 0.8
        )

        if is_route_healthy:
            backoff = cls.BASE_BACKOFF_SECONDS * (2 ** max(0, ctx.attempt_number - 1))
            return RecoveryPlan(
                strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
                retryability=RetryabilityClass.RETRYABLE,
                target_route_id=ctx.route_id,
                suggested_backoff_sec=backoff,
                decision_confidence=1.0,
                explanation=(
                    f"Classified as RETRYABLE ({ctx.error_code}). Current route '{ctx.route_id}' "
                    f"is HEALTHY (score: {ctx.route_health_score:.2f}). Selected DETERMINISTIC_RETRY_BACKOFF "
                    f"with {backoff:.1f}s delay."
                ),
                parameters={
                    "backoff_sec": backoff,
                    "target_route_id": ctx.route_id,
                },
            )

        # Route is degraded or down: attempt failover if alternative healthy route exists
        if ctx.available_alternative_routes:
            alt_route = ctx.available_alternative_routes[0]
            return RecoveryPlan(
                strategy=RecoveryStrategy.ROUTE_FAILOVER,
                retryability=RetryabilityClass.RETRYABLE,
                target_route_id=alt_route,
                suggested_backoff_sec=5.0,
                decision_confidence=0.95,
                explanation=(
                    f"Classified as RETRYABLE ({ctx.error_code}). Current route '{ctx.route_id}' "
                    f"is DEGRADED/DOWN (status: {ctx.route_status.value}, score: {ctx.route_health_score:.2f}). "
                    f"Selected ROUTE_FAILOVER targeting healthy rail '{alt_route}'."
                ),
                parameters={
                    "original_route_id": ctx.route_id,
                    "target_route_id": alt_route,
                    "backoff_sec": 5.0,
                },
            )
        else:
            # Degraded route with no alternative: fall back to backoff retry with extended delay
            backoff = cls.BASE_BACKOFF_SECONDS * 2.5
            return RecoveryPlan(
                strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
                retryability=RetryabilityClass.RETRYABLE,
                target_route_id=ctx.route_id,
                suggested_backoff_sec=backoff,
                decision_confidence=0.75,
                explanation=(
                    f"Classified as RETRYABLE ({ctx.error_code}). Route '{ctx.route_id}' is DEGRADED, "
                    f"but no alternative routes are registered. Falling back to extended backoff ({backoff:.1f}s)."
                ),
                parameters={
                    "backoff_sec": backoff,
                    "target_route_id": ctx.route_id,
                },
            )

    @classmethod
    def _handle_customer_action(cls, ctx: PaymentRecoveryContext) -> RecoveryPlan:
        return RecoveryPlan(
            strategy=RecoveryStrategy.NOTIFY_CUSTOMER_LINK,
            retryability=RetryabilityClass.CUSTOMER_ACTION_REQUIRED,
            notification_channel="SMS",
            notification_template="PAYMENT_ACTION_REQUIRED_LINK",
            decision_confidence=1.0,
            explanation=(
                f"Classified as CUSTOMER_ACTION_REQUIRED ({ctx.error_code}). Automated bank retry blocked. "
                f"Selected NOTIFY_CUSTOMER_LINK to request customer intervention (e.g. balance top-up / UPI approval)."
            ),
            parameters={
                "channel": "SMS",
                "template": "PAYMENT_ACTION_REQUIRED_LINK",
            },
        )

    @classmethod
    def _handle_non_retryable(cls, ctx: PaymentRecoveryContext) -> RecoveryPlan:
        return RecoveryPlan(
            strategy=RecoveryStrategy.TERMINAL_ABANDON,
            retryability=RetryabilityClass.NON_RETRYABLE,
            decision_confidence=1.0,
            explanation=(
                f"Classified as NON_RETRYABLE ({ctx.error_code}). Error represents permanent account block, "
                f"expired instrument, or fraud. Selected TERMINAL_ABANDON."
            ),
            parameters={
                "reason": ctx.reason,
            },
        )

    @classmethod
    def _handle_unknown(cls, ctx: PaymentRecoveryContext) -> RecoveryPlan:
        # Core safety philosophy: do NOT guess or retry unknown failures
        return RecoveryPlan(
            strategy=RecoveryStrategy.MANUAL_REVIEW,
            retryability=RetryabilityClass.UNKNOWN,
            decision_confidence=0.0,
            explanation=(
                f"Unrecognized failure code '{ctx.error_code}'. System refuses to execute automated financial actions "
                f"on unknown errors. Selected MANUAL_REVIEW / ESCALATE for safety."
            ),
            parameters={
                "safety_halt": True,
                "unknown_code": ctx.error_code,
            },
        )
