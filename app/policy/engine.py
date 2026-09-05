"""Financial Safety Policy Engine enforcing deterministic financial authorization boundaries."""

from datetime import datetime, timezone
import logging
from typing import Any, List, Optional
import uuid

from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.policy.models import PolicyDecision, PolicyEvaluationResult, RecoveryPolicyConfig
from app.policy.rules import (
    AutomatedAmountCapRule,
    FailClosedRule,
    MaxRetryCountRule,
    MerchantRestrictionsRule,
    PendingPaymentReconciliationRule,
    PermittedPaymentMethodsRule,
    PermittedStrategiesRule,
    PolicyRule,
    ProhibitedSituationsRule,
    RecoveryWindowRule,
    TerminalWorkflowLockRule,
)

logger = logging.getLogger("iro.policy.engine")


class FinancialSafetyPolicyEngine:
    """Deterministic Financial Safety Policy Engine.

    Acts as an authoritative financial authorization boundary between AI/Agent recommendations
    and payment execution.
    Invariants:
    1. AI CANNOT OVERRIDE POLICY: Policy evaluates authoritative relational state and rules.
    2. FAIL-CLOSED: Missing data strictly results in DENIED.
    3. DETERMINISTIC ORDER: Rules evaluate in fixed priority sequence.
    """

    def __init__(
        self,
        config: Optional[RecoveryPolicyConfig] = None,
        custom_rules: Optional[List[PolicyRule]] = None,
    ):
        self.config = config or RecoveryPolicyConfig()
        self.rules: List[PolicyRule] = custom_rules or [
            FailClosedRule(),
            TerminalWorkflowLockRule(),
            PendingPaymentReconciliationRule(),
            ProhibitedSituationsRule(),
            MerchantRestrictionsRule(),
            MaxRetryCountRule(),
            RecoveryWindowRule(),
            PermittedStrategiesRule(),
            PermittedPaymentMethodsRule(),
            AutomatedAmountCapRule(),
        ]

    def evaluate(
        self,
        context: PaymentRecoveryContext,
        plan: RecoveryPlan,
        recovery_case: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> PolicyEvaluationResult:
        """Deterministically evaluate the proposed recovery plan against all financial safety policies."""
        evaluation_id = uuid.uuid4()
        current_time = now or datetime.now(timezone.utc)
        evaluated_policies: List[str] = []
        violated_policies: List[str] = []

        logger.info(
            f"[POLICY EVALUATION START] ID={evaluation_id} Payment={context.payment_id} "
            f"Strategy={plan.strategy.value} Attempt={context.attempt_number}"
        )

        approval_required = False
        approval_reason = ""
        approval_details: dict = {}

        for rule in self.rules:
            evaluated_policies.append(rule.name)
            outcome = rule.evaluate(
                context=context,
                plan=plan,
                config=self.config,
                recovery_case=recovery_case,
                now=current_time,
            )

            if outcome is not None:
                decision, reason, details = outcome
                violated_policies.append(rule.name)

                # Hard Denial: Immediately stop evaluation and return DENIED
                if decision == PolicyDecision.DENIED:
                    logger.warning(
                        f"[POLICY DENIED] Rule '{rule.name}' denied recovery for Payment={context.payment_id}. "
                        f"Reason: {reason}"
                    )
                    return PolicyEvaluationResult(
                        evaluation_id=evaluation_id,
                        policy_version=self.config.policy_version,
                        decision=PolicyDecision.DENIED,
                        violated_policies=violated_policies,
                        reason=reason,
                        evaluated_policies=evaluated_policies,
                        risk_level="HIGH",
                        details=details,
                        timestamp=current_time,
                    )

                # Soft Escalation: Mark that human approval is required
                elif decision == PolicyDecision.REQUIRES_HUMAN_APPROVAL:
                    logger.info(
                        f"[POLICY REQUIRES APPROVAL] Rule '{rule.name}' requires human approval for "
                        f"Payment={context.payment_id}. Reason: {reason}"
                    )
                    approval_required = True
                    approval_reason = reason
                    approval_details = details

        # If any rule required human review, return REQUIRES_HUMAN_APPROVAL
        if approval_required:
            return PolicyEvaluationResult(
                evaluation_id=evaluation_id,
                policy_version=self.config.policy_version,
                decision=PolicyDecision.REQUIRES_HUMAN_APPROVAL,
                violated_policies=violated_policies,
                reason=approval_reason,
                evaluated_policies=evaluated_policies,
                risk_level="MEDIUM",
                details=approval_details,
                timestamp=current_time,
            )

        # All financial policies passed!
        logger.info(f"[POLICY PERMITTED] All {len(evaluated_policies)} policies approved Payment={context.payment_id}")
        return PolicyEvaluationResult(
            evaluation_id=evaluation_id,
            policy_version=self.config.policy_version,
            decision=PolicyDecision.PERMITTED,
            violated_policies=[],
            reason="Approved under financial safety policy",
            evaluated_policies=evaluated_policies,
            risk_level="LOW",
            details={"approved_strategy": plan.strategy.value},
            timestamp=current_time,
        )
