"""Modular, deterministic financial safety rule implementations.

Invariants:
1. FAIL-CLOSED: Missing or corrupt policy data yields immediate DENIED.
2. ZERO AI BYPASS: Evaluates authoritative facts only; model confidence/recommendations cannot override rules.
3. CONFIGURATION-DRIVEN: All thresholds, SLAs, and sets derive from RecoveryPolicyConfig.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.models.enums import PaymentLifecycleState, PaymentMethod, RecoveryState, RecoveryStrategy
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.policy.models import PolicyDecision, RecoveryPolicyConfig


class PolicyRule(ABC):
    """Abstract base class for financial safety policy rules."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the policy rule."""
        pass

    @abstractmethod
    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        """Evaluate the rule.

        Returns None if permitted, or (PolicyDecision, reason, details) if denied/requires approval.
        """
        pass


class FailClosedRule(PolicyRule):
    """Safety principle: Missing authorization data is NOT authorization. Fail closed."""

    @property
    def name(self) -> str:
        return "FAIL_CLOSED_DATA_INTEGRITY_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        if not context.merchant_id:
            return (
                PolicyDecision.DENIED,
                "POLICY_DATA_UNAVAILABLE: Missing merchant_id in context",
                {"missing_field": "merchant_id"},
            )
        if context.amount_inr is None or context.amount_inr <= Decimal("0.00"):
            return (
                PolicyDecision.DENIED,
                "POLICY_DATA_UNAVAILABLE: Invalid or missing payment amount",
                {"amount_inr": str(context.amount_inr)},
            )
        if not context.payment_method:
            return (
                PolicyDecision.DENIED,
                "POLICY_DATA_UNAVAILABLE: Missing payment_method in context",
                {"missing_field": "payment_method"},
            )
        return None


class TerminalWorkflowLockRule(PolicyRule):
    """Deny automated recovery on payments or cases that are already in a terminal state."""

    @property
    def name(self) -> str:
        return "TERMINAL_WORKFLOW_LOCK_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        # 1. Check recovery case states if available
        if recovery_case:
            state = getattr(recovery_case, "recovery_state", None)
            if state and getattr(state, "is_terminal", False):
                return (
                    PolicyDecision.DENIED,
                    f"TERMINAL_WORKFLOW_LOCKED: RecoveryCase is in terminal state '{state.value}'",
                    {"current_state": state.value},
                )
            status = getattr(recovery_case, "status", None)
            if status and getattr(status, "is_terminal", False):
                return (
                    PolicyDecision.DENIED,
                    f"TERMINAL_WORKFLOW_LOCKED: Payment is in terminal status '{status.value}'",
                    {"current_status": status.value},
                )

        # 2. Check context payment status if terminal
        payment_status = getattr(context, "payment_status", None)
        if payment_status and getattr(payment_status, "is_terminal", False):
            return (
                PolicyDecision.DENIED,
                f"TERMINAL_WORKFLOW_LOCKED: Payment is in terminal status '{payment_status.value}'",
                {"current_status": payment_status.value},
            )

        return None


class PendingPaymentReconciliationRule(PolicyRule):
    """Deny automated retries on asynchronous pending/processing payment states to prevent double debits."""

    @property
    def name(self) -> str:
        return "PENDING_PAYMENT_RECONCILIATION_POLICY"

    PENDING_ERROR_CODES = {
        "PAYMENT_PENDING",
        "PROCESSING",
        "PROCESSING_IN_FLIGHT",
        "AWAITING_CONFIRMATION",
        "TRANSACTION_PENDING",
        "PENDING_CAPTURE",
    }

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        err_code = (context.error_code or "").strip().upper()
        if err_code in self.PENDING_ERROR_CODES:
            return (
                PolicyDecision.DENIED,
                f"PENDING_PAYMENT_HOLD: Error code '{err_code}' represents an in-flight pending/processing state. Reconcile or await webhook before retrying.",
                {"error_code": err_code},
            )
        return None


class ProhibitedSituationsRule(PolicyRule):
    """Block recovery on fraud flags, sanction violations, or high-risk indicators."""

    @property
    def name(self) -> str:
        return "PROHIBITED_RECOVERY_SITUATION_POLICY"

    PROHIBITED_ERROR_CODES = {
        "SANCTION_VIOLATION",
        "CARD_BLOCKED",
        "RISK_REJECTED",
        "FRAUD_SUSPECTED",
        "VELOCITY_CHECK_FAILED",
        "ACCOUNT_BLOCKED",
    }

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        err_code = (context.error_code or "").strip().upper()
        if err_code in self.PROHIBITED_ERROR_CODES:
            return (
                PolicyDecision.DENIED,
                f"PROHIBITED_RECOVERY_SITUATION: Error code '{err_code}' is prohibited from automated recovery",
                {"prohibited_error_code": err_code},
            )

        # Check customer risk score
        risk_score = getattr(context, "customer_risk_score", 0.0)
        if risk_score >= config.max_fraud_risk_score_allowed:
            return (
                PolicyDecision.DENIED,
                f"PROHIBITED_RECOVERY_SITUATION: Customer risk score {risk_score:.2f} exceeds threshold {config.max_fraud_risk_score_allowed}",
                {"risk_score": risk_score, "max_allowed": config.max_fraud_risk_score_allowed},
            )
        return None


class MerchantRestrictionsRule(PolicyRule):
    """Enforce merchant recovery opt-in and minimum transaction economic thresholds."""

    @property
    def name(self) -> str:
        return "MERCHANT_RESTRICTIONS_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        if not context.merchant_recovery_enabled:
            return (
                PolicyDecision.DENIED,
                "MERCHANT_RESTRICTION: Merchant has disabled automated recovery",
                {"merchant_id": str(context.merchant_id)},
            )

        if context.amount_inr < context.merchant_min_recovery_amount_inr:
            return (
                PolicyDecision.DENIED,
                f"MERCHANT_RESTRICTION: Transaction amount INR {context.amount_inr} below minimum recovery threshold INR {context.merchant_min_recovery_amount_inr}",
                {
                    "amount_inr": float(context.amount_inr),
                    "min_required_inr": float(context.merchant_min_recovery_amount_inr),
                },
            )
        return None


class MaxRetryCountRule(PolicyRule):
    """Strictly cap automated recovery retry attempts."""

    @property
    def name(self) -> str:
        return "MAX_RETRY_COUNT_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        # Only applies to active payment retry strategies
        if plan.strategy in (RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF, RecoveryStrategy.ROUTE_FAILOVER):
            max_retries = context.merchant_max_auto_retries
            if context.attempt_number >= max_retries:
                return (
                    PolicyDecision.DENIED,
                    f"MAX_RETRY_COUNT_EXCEEDED: Current attempt {context.attempt_number} exceeds maximum allowed retries {max_retries}",
                    {"current_attempt": context.attempt_number, "max_retries": max_retries},
                )
        return None


class RecoveryWindowRule(PolicyRule):
    """Enforce maximum elapsed recovery window SLAs by payment rail."""

    @property
    def name(self) -> str:
        return "MAX_RECOVERY_WINDOW_SLA_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        curr_time = now or datetime.now(timezone.utc)
        max_window_sec = config.max_window_sec_by_method.get(
            context.payment_method, config.default_max_window_sec
        )

        if context.failure_created_at:
            created_tz = context.failure_created_at
            if created_tz.tzinfo is None:
                created_tz = created_tz.replace(tzinfo=timezone.utc)
            elapsed_sec = (curr_time - created_tz).total_seconds()
            if elapsed_sec > max_window_sec:
                return (
                    PolicyDecision.DENIED,
                    f"MAX_RECOVERY_WINDOW_EXCEEDED: Elapsed recovery time {elapsed_sec:.1f}s exceeds SLA {max_window_sec:.1f}s",
                    {"elapsed_sec": elapsed_sec, "max_window_sec": max_window_sec},
                )
        return None


class PermittedStrategiesRule(PolicyRule):
    """Enforce strategy whitelist."""

    @property
    def name(self) -> str:
        return "PERMITTED_STRATEGIES_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        if plan.strategy not in config.permitted_strategies:
            return (
                PolicyDecision.DENIED,
                f"UNAUTHORIZED_STRATEGY: Proposed recovery strategy '{plan.strategy.value}' is not permitted",
                {"unauthorized_strategy": plan.strategy.value},
            )
        return None


class PermittedPaymentMethodsRule(PolicyRule):
    """Ensure payment rail is approved for automated re-execution."""

    @property
    def name(self) -> str:
        return "PERMITTED_PAYMENT_METHODS_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        if plan.strategy in (RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF, RecoveryStrategy.ROUTE_FAILOVER):
            if context.payment_method not in config.permitted_retry_methods:
                return (
                    PolicyDecision.DENIED,
                    f"PAYMENT_METHOD_NOT_SUPPORTED: Method '{context.payment_method.value}' is not authorized for automated retry",
                    {"payment_method": context.payment_method.value},
                )
        return None


class AutomatedAmountCapRule(PolicyRule):
    """Require explicit human approval for high-value transactions."""

    @property
    def name(self) -> str:
        return "AUTOMATED_AMOUNT_CAP_POLICY"

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        config: RecoveryPolicyConfig,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[PolicyDecision, str, Dict[str, Any]]]:
        threshold = min(
            context.merchant_auto_escalate_threshold_inr,
            config.system_max_recovery_amount_inr,
        )

        # Immediate escalation if single attempt exceeds system hard cap
        if context.amount_inr > config.system_max_recovery_amount_inr:
            return (
                PolicyDecision.REQUIRES_HUMAN_APPROVAL,
                f"HIGH_VALUE_TRANSACTION: Amount INR {context.amount_inr} exceeds system maximum automated threshold INR {config.system_max_recovery_amount_inr}",
                {"amount_inr": float(context.amount_inr), "threshold_inr": float(config.system_max_recovery_amount_inr)},
            )

        # Repeated failure on high-value transaction requires approval
        if context.amount_inr >= threshold and context.attempt_number >= 2:
            return (
                PolicyDecision.REQUIRES_HUMAN_APPROVAL,
                f"HIGH_VALUE_TRANSACTION: Repeat failure on high-value transaction INR {context.amount_inr} (limit: INR {threshold}) requires human review",
                {"amount_inr": float(context.amount_inr), "threshold_inr": float(threshold), "attempt_number": context.attempt_number},
            )
        return None
