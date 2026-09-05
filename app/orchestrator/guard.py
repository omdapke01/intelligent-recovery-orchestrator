"""Deterministic Recovery Guard evaluating strategy eligibility and stopping conditions."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import List

from app.models.enums import PaymentMethod, RecoveryStrategy, RetryabilityClass
from app.orchestrator.models import (
    GuardEvaluationResult,
    PaymentRecoveryContext,
    RecoveryPlan,
)

logger = logging.getLogger(__name__)


class DeterministicRecoveryGuard:
    """
    Evaluates whether a planned recovery strategy is permitted by deterministic eligibility guards.
    Distinguishes decision guards from the Phase 7 hard authorization policy boundary.
    """

    # Maximum recovery window SLAs
    MAX_WINDOW_SECONDS_UPI = 900.0        # 15 minutes
    MAX_WINDOW_SECONDS_CARDS = 7200.0     # 2 hours
    MAX_WINDOW_SECONDS_DEFAULT = 3600.0   # 1 hour

    @classmethod
    def evaluate(
        cls,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        now: datetime | None = None,
    ) -> GuardEvaluationResult:
        """
        Evaluate all 6 stopping conditions and policy guards.
        Returns a structured GuardEvaluationResult.
        """
        current_time = now or datetime.now(timezone.utc)
        guards_checked: List[str] = []
        violated_guards: List[str] = []

        # 1. Safety Guard for UNKNOWN failure codes
        guards_checked.append("SAFETY_UNKNOWN_ERROR_GUARD")
        if plan.retryability == RetryabilityClass.UNKNOWN or plan.strategy == RecoveryStrategy.MANUAL_REVIEW:
            violated_guards.append("SAFETY_UNKNOWN_ERROR_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=True,
                stop_reason="UNKNOWN_ERROR_SAFETY_ESCALATION",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={"reason": "Refusing automated recovery on unrecognized error code"},
            )

        # 2. Non-Retryable Error Guard
        guards_checked.append("NON_RETRYABLE_ERROR_GUARD")
        if plan.retryability == RetryabilityClass.NON_RETRYABLE or plan.strategy == RecoveryStrategy.TERMINAL_ABANDON:
            violated_guards.append("NON_RETRYABLE_ERROR_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=False,
                stop_reason="NON_RETRYABLE_ERROR",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={"reason": "Error is permanent or fraud-flagged"},
            )

        # 3. Merchant Opt-In / Policy Restriction Guard
        guards_checked.append("MERCHANT_ENABLED_GUARD")
        if not context.merchant_recovery_enabled:
            violated_guards.append("MERCHANT_ENABLED_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=False,
                stop_reason="MERCHANT_RECOVERY_DISABLED",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={"merchant_id": str(context.merchant_id)},
            )

        # 4. Repeated High-Value Failure Escalation Guard
        guards_checked.append("HIGH_VALUE_ESCALATION_GUARD")
        if (
            context.amount_inr >= context.merchant_auto_escalate_threshold_inr
            and context.attempt_number >= 2
        ):
            violated_guards.append("HIGH_VALUE_ESCALATION_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=True,
                stop_reason="HIGH_VALUE_ENTERPRISE_ESCALATION",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={
                    "amount_inr": float(context.amount_inr),
                    "threshold_inr": float(context.merchant_auto_escalate_threshold_inr),
                    "attempt_number": context.attempt_number,
                },
            )

        # 5. Maximum Retries Exceeded Guard
        guards_checked.append("MAX_RETRIES_GUARD")
        if context.attempt_number >= context.merchant_max_auto_retries:
            violated_guards.append("MAX_RETRIES_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=False,
                stop_reason="MAX_RETRIES_EXCEEDED",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={
                    "attempt_number": context.attempt_number,
                    "max_retries": context.merchant_max_auto_retries,
                },
            )

        # 5. Minimum Economic Recovery Value Guard
        guards_checked.append("MIN_RECOVERY_VALUE_GUARD")
        if context.amount_inr < context.merchant_min_recovery_amount_inr:
            violated_guards.append("MIN_RECOVERY_VALUE_GUARD")
            plan.guards_evaluated = guards_checked
            return GuardEvaluationResult(
                is_approved=False,
                is_escalated=False,
                stop_reason="INSUFFICIENT_RECOVERY_VALUE",
                guards_checked=guards_checked,
                violated_guards=violated_guards,
                details={
                    "amount_inr": float(context.amount_inr),
                    "min_required_inr": float(context.merchant_min_recovery_amount_inr),
                },
            )

        # 6. Maximum Recovery Window SLA Guard
        guards_checked.append("MAX_WINDOW_SLA_GUARD")
        if context.payment_method == PaymentMethod.UPI:
            max_window = cls.MAX_WINDOW_SECONDS_UPI
        elif context.payment_method in (PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD):
            max_window = cls.MAX_WINDOW_SECONDS_CARDS
        else:
            max_window = cls.MAX_WINDOW_SECONDS_DEFAULT

        if context.failure_created_at:
            created_tz = context.failure_created_at
            if created_tz.tzinfo is None:
                created_tz = created_tz.replace(tzinfo=timezone.utc)
            elapsed = (current_time - created_tz).total_seconds()
            if elapsed > max_window:
                violated_guards.append("MAX_WINDOW_SLA_GUARD")
                plan.guards_evaluated = guards_checked
                return GuardEvaluationResult(
                    is_approved=False,
                    is_escalated=False,
                    stop_reason="MAX_WINDOW_EXCEEDED",
                    guards_checked=guards_checked,
                    violated_guards=violated_guards,
                    details={"elapsed_seconds": elapsed, "max_window_seconds": max_window},
                )

        # All eligibility guards passed!
        plan.guards_evaluated = guards_checked
        return GuardEvaluationResult(
            is_approved=True,
            stop_reason=None,
            is_escalated=False,
            guards_checked=guards_checked,
            violated_guards=[],
            details={"approved_strategy": plan.strategy.value},
        )
