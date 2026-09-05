import logging
from typing import Any, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession


from app.ai.gateway import AIModelGateway
from app.ai.schemas import AIRecoveryStrategy, map_ai_strategy_to_domain
from app.models.enums import RecoveryStrategy, RetryabilityClass
from app.orchestrator.classifier import DeterministicFailureClassifier
from app.orchestrator.models import PaymentRecoveryContext, RecoveryPlan
from app.orchestrator.strategy_selector import DeterministicStrategySelector

logger = logging.getLogger("iro.ai.hierarchy")


class HierarchicalRecoveryDecisionEngine:
    """Coordinates the 3-tier recovery decision hierarchy:

    1. Tier 1: Deterministic Rules (Known failure codes, unambiguous paths, safety halts)
    2. Tier 2: Real Heuristics / Route Degradation Pattern Check
    3. Tier 3: AI Reasoning Layer (Ambiguous failure codes, edge cases, multi-failure histories)
       Optionally delegated to bounded Specialist Recovery Agent when available.

    Invariants:
    1. AI is strictly optional. If AI fails, the deterministic engine decides if possible,
       escalating only if no safe deterministic rule exists.
    2. AI recommendations cannot mutate database state or execute payment attempts directly.
    3. All recommendations are validated by DeterministicRecoveryGuard before approval.
    """

    def __init__(
        self,
        ai_gateway: Optional[AIModelGateway] = None,
        agent: Optional[Any] = None,
    ):
        self.gateway = ai_gateway or AIModelGateway()
        self.agent = agent

    async def decide_recovery_plan(
        self,
        context: PaymentRecoveryContext,
        session: Optional[AsyncSession] = None,
        recovery_case_id: Optional[uuid.UUID] = None,
    ) -> RecoveryPlan:
        """Evaluate context through the 3-tier hierarchy and produce an explainable RecoveryPlan."""


        # -----------------------------------------------------------------
        # TIER 1: Deterministic Classification
        # -----------------------------------------------------------------
        retryability = DeterministicFailureClassifier.classify(
            error_code=context.error_code,
            failure_category=context.failure_category,
        )

        # Clear, unambiguous non-retryable errors or standard transient timeouts on healthy rails
        is_clear_case = (
            retryability in (RetryabilityClass.NON_RETRYABLE, RetryabilityClass.CUSTOMER_ACTION_REQUIRED)
            or (retryability == RetryabilityClass.RETRYABLE and context.route_is_active and context.route_health_score >= 0.90)
        )

        # If unambiguous and not a multi-attempt repeat failure, use deterministic fast-path
        if is_clear_case and context.attempt_number <= 1:
            logger.info(f"[DECISION TIER 1] Deterministic fast-path resolved failure code '{context.error_code}'")
            return DeterministicStrategySelector.select_strategy(context, retryability)

        # -----------------------------------------------------------------
        # TIER 2: Heuristic Route Degradation Pattern Check
        # -----------------------------------------------------------------
        # If route is clearly degraded or down, and we have a healthy alternative route,
        # and retryability is known retryable, we can deterministically recommend route failover
        if (
            retryability == RetryabilityClass.RETRYABLE
            and context.route_health_score < 0.50
            and context.available_alternative_routes
            and context.attempt_number <= 1
        ):
            logger.info(f"[DECISION TIER 2] Heuristic route failover triggered for degraded route '{context.route_id}'")
            return DeterministicStrategySelector.select_strategy(context, retryability)


        # -----------------------------------------------------------------
        # TIER 3: Specialist Agent or AI Reasoning Layer
        # -----------------------------------------------------------------
        tier_label = "TIER_3_SPECIALIST_AGENT" if (self.agent and session) else "TIER_3_AI_GATEWAY"
        logger.info(
            f"[DECISION {tier_label}] Investigating ambiguous context "
            f"(code='{context.error_code}', attempt={context.attempt_number}, amount=INR {context.amount_inr:.2f})"

        )

        if self.agent and session:
            ai_rec = await self.agent.investigate(
                session=session,
                context=context,
                recovery_case_id=recovery_case_id,
            )
        else:
            ai_rec = await self.gateway.get_recommendation(context)

        # Check if AI recommendation succeeded and does not require manual escalation
        if not ai_rec.requires_human_review and ai_rec.recommended_strategy != AIRecoveryStrategy.ESCALATE:
            domain_strategy = map_ai_strategy_to_domain(ai_rec.recommended_strategy)
            target_route = ai_rec.target_route or context.route_id
            delay_sec = float(ai_rec.suggested_delay_sec) if ai_rec.suggested_delay_sec else None

            logger.info(
                f"[DECISION {tier_label} SUCCESS] Recommended strategy '{ai_rec.recommended_strategy.value}' "
                f"(confidence: {ai_rec.confidence * 100:.0f}%)"
            )

            return RecoveryPlan(
                strategy=domain_strategy,
                retryability=RetryabilityClass.RETRYABLE if domain_strategy in (
                    RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF, RecoveryStrategy.ROUTE_FAILOVER
                ) else RetryabilityClass.CUSTOMER_ACTION_REQUIRED,
                target_route_id=target_route,
                suggested_backoff_sec=delay_sec or (10.0 if domain_strategy == RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF else None),
                decision_confidence=ai_rec.confidence,
                explanation=f"{'Specialist Agent' if self.agent and session else 'AI'} Recommendation ({ai_rec.confidence * 100:.0f}% confidence): {ai_rec.explanation}",
                parameters={
                    "ai_strategy": ai_rec.recommended_strategy.value,
                    "ai_reason_codes": ai_rec.reason_codes,
                    "ai_declared_tools": ai_rec.required_tools,
                    "tier_used": tier_label,
                },
            )


        # -----------------------------------------------------------------
        # AI FAILURE / LOW-CONFIDENCE / ESCALATION FALLBACK
        # Rule: If AI fails, check if deterministic engine can decide safely;
        #       escalate only if no safe deterministic rule exists.
        # -----------------------------------------------------------------
        logger.warning(
            f"[DECISION TIER 3 FALLBACK] AI recommendation requires human review or failed "
            f"(reasons={ai_rec.reason_codes}). Evaluating deterministic fallback eligibility..."
        )

        # Check if deterministic classifier can provide a safe rule
        if retryability == RetryabilityClass.RETRYABLE and context.route_is_active:
            logger.info("[DECISION FALLBACK] Deterministic fallback found: healthy retryable failure.")
            plan = DeterministicStrategySelector.select_strategy(context, retryability)
            plan.explanation = f"[Fallback from AI] {plan.explanation}"
            plan.parameters["fallback_from_ai"] = True
            plan.parameters["ai_reason_codes"] = ai_rec.reason_codes
            return plan

        # No safe deterministic rule exists (unknown code, non-retryable, or low confidence) -> ESCALATE
        logger.info("[DECISION FALLBACK] No safe deterministic alternative; escalating to human review.")
        return RecoveryPlan(
            strategy=RecoveryStrategy.MANUAL_REVIEW,
            retryability=RetryabilityClass.UNKNOWN,
            decision_confidence=0.0,
            explanation=f"Escalated to human review: {ai_rec.explanation}",
            parameters={
                "escalation_reasons": ai_rec.reason_codes,
                "ai_attempted": True,
                "tier_used": "FALLBACK_MANUAL_REVIEW",
            },
        )
