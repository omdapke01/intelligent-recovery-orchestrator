"""Model Router managing task complexity routing across lightweight, deep reasoning,

and structured extraction models, integrated with the L7 Model Load Balancer.
"""

from enum import Enum
import logging
from typing import Any, Dict, Optional, Tuple

from app.ai.load_balancer import L7ModelLoadBalancer
from app.ai.providers.base import ModelProvider, ModelProviderError
from app.ai.providers.mock_provider import MockAIModelProvider
from app.models.enums import FailureCategory
from app.orchestrator.models import PaymentRecoveryContext

logger = logging.getLogger("iro.ai.router")


class TaskComplexity(str, Enum):
    """Task complexity tiers for specialized model selection."""
    FAST_CLASSIFICATION = "FAST_CLASSIFICATION"      # Fast, lightweight tier (transient / standard)
    DEEP_REASONING = "DEEP_REASONING"                # High-capacity reasoning tier (ambiguous / degraded)
    STRUCTURED_EXTRACTION = "STRUCTURED_EXTRACTION"  # Parameter / schema extraction tier


class ModelRouter:
    """Routes recovery tasks between lightweight, deep reasoning, and structured extraction tiers

    with L7 load balancing distribution or multi-provider failover.
    """

    def __init__(
        self,
        primary_provider: Optional[ModelProvider] = None,
        fallback_provider: Optional[ModelProvider] = None,
        load_balancer: Optional[L7ModelLoadBalancer] = None,
    ):
        self.primary_provider = primary_provider or MockAIModelProvider()
        self.fallback_provider = fallback_provider or MockAIModelProvider()
        self.load_balancer = load_balancer
        self.routing_stats: Dict[str, int] = {
            "fast_classification": 0,
            "deep_reasoning": 0,
            "structured_extraction": 0,
            "fallback_invocations": 0,
            "load_balancer_dispatches": 0,
        }

    def assess_complexity(
        self,
        context: PaymentRecoveryContext,
        task_type: str = "recommendation",
    ) -> TaskComplexity:
        """Assess transaction, failure characteristics, and task purpose to select appropriate model tier."""
        if task_type == "extraction":
            return TaskComplexity.STRUCTURED_EXTRACTION

        # High value (>= ₹50,000), repeat failure (attempt >= 2), or route degradation requires deep reasoning
        if (
            context.amount_inr >= 50000
            or context.attempt_number >= 2
            or context.failure_category == FailureCategory.ROUTE_DEGRADATION
        ):
            return TaskComplexity.DEEP_REASONING

        # Standard transient failure or moderate value uses fast lightweight classification
        return TaskComplexity.FAST_CLASSIFICATION

    async def route_and_generate(
        self,
        prompt: str,
        system_prompt: str,
        context: Dict[str, Any],
        complexity: TaskComplexity,
    ) -> Tuple[str, str]:
        """Dispatch prompt to appropriate tier through L7 Load Balancer or local provider with fallback.

        Returns:
            Tuple[raw_response_text, instance_or_provider_name_used]
        """
        tier_key = complexity.value.lower()
        if tier_key in self.routing_stats:
            self.routing_stats[tier_key] += 1

        # PATH A: Horizontally Scaled L7 Load Balancer Dispatch
        if self.load_balancer:
            logger.info(
                f"[MODEL ROUTER] Dispatching task with complexity={complexity.value} to L7 Load Balancer"
            )
            self.routing_stats["load_balancer_dispatches"] += 1
            raw_text, instance_used = await self.load_balancer.dispatch(
                tier=complexity.value,
                prompt=prompt,
                system_prompt=system_prompt,
                context=context,
            )
            return raw_text, instance_used

        # PATH B: Direct Provider with Fallback (Standalone / Test Mode)
        provider_name = f"{self.primary_provider.__class__.__name__}:{complexity.value}"
        logger.info(f"[MODEL ROUTER] Dispatching task with complexity={complexity.value} to {provider_name}")

        try:
            raw_text = await self.primary_provider.generate_recommendation(
                prompt=prompt,
                system_prompt=system_prompt,
                context=context,
            )
            return raw_text, provider_name

        except ModelProviderError as err:
            logger.warning(
                f"[MODEL ROUTER FAILOVER] Primary provider failed ({err}). "
                f"Attempting fallback provider {self.fallback_provider.__class__.__name__}..."
            )
            self.routing_stats["fallback_invocations"] += 1

            try:
                fallback_name = f"{self.fallback_provider.__class__.__name__}:FALLBACK"
                raw_text = await self.fallback_provider.generate_recommendation(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    context=context,
                )
                logger.info(f"[MODEL ROUTER FAILOVER SUCCESS] Resolved via {fallback_name}")
                return raw_text, fallback_name
            except Exception as fallback_err:
                logger.error(f"[MODEL ROUTER ERROR] Fallback provider also failed: {fallback_err}")
                raise
