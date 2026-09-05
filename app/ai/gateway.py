"""AI Model Gateway providing a unified, schema-validated interface for recovery recommendations."""

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional

from pydantic import ValidationError

from app.ai.providers.base import ModelProviderError, ModelTimeoutError, ModelUnavailableError
from app.ai.router import ModelRouter, TaskComplexity
from app.ai.sanitizer import SYSTEM_PROMPT, PromptSanitizer
from app.ai.schemas import (
    ALLOWED_AI_TOOLS,
    AIRecoveryRecommendation,
    AIRecoveryStrategy,
)
from app.config import settings
from app.orchestrator.models import PaymentRecoveryContext

logger = logging.getLogger("iro.ai.gateway")


class AIModelGateway:
    """Gateway orchestrating prompt defense, model routing, schema validation,

    confidence thresholding, and tool whitelisting.

    Invariants:
    1. Orchestrator never talks directly to concrete LLMs; it interacts exclusively via this Gateway.
    2. All outputs are strictly schema-validated.
    3. Low confidence (< threshold) or hallucinated tools force ESCALATE with requires_human_review=True.
    4. ZERO tool execution in Phase 5; tools are strictly declarative.
    5. Zero direct financial execution: returns an advisory recommendation only.
    """

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        confidence_threshold: Optional[float] = None,
        timeout_sec: Optional[float] = None,
    ):
        self.router = router or ModelRouter()
        self.confidence_threshold = (
            confidence_threshold if confidence_threshold is not None else settings.AI_CONFIDENCE_THRESHOLD
        )
        self.timeout_sec = timeout_sec or settings.AI_GATEWAY_TIMEOUT_SEC

    async def get_recommendation(
        self,
        context: PaymentRecoveryContext,
        task_type: str = "recommendation",
    ) -> AIRecoveryRecommendation:
        """Fetch a structured, schema-validated recommendation for a payment failure."""
        complexity = self.router.assess_complexity(context, task_type=task_type)
        prompt = PromptSanitizer.build_prompt(context)
        context_dict = {
            "payment_id": str(context.payment_id),
            "error_code": context.error_code,
            "reason": context.reason,
            "amount_inr": float(context.amount_inr),
            "attempt_number": context.attempt_number,
            "available_alternative_routes": context.available_alternative_routes,
        }

        try:
            raw_response, provider_name = await asyncio.wait_for(
                self.router.route_and_generate(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    context=context_dict,
                    complexity=complexity,
                ),
                timeout=self.timeout_sec,
            )
            return self._parse_and_validate(raw_response, provider_name)

        except asyncio.TimeoutError:
            logger.warning(f"[AI GATEWAY TIMEOUT] Request exceeded bounded timeout of {self.timeout_sec}s.")
            return AIRecoveryRecommendation(
                recommended_strategy=AIRecoveryStrategy.ESCALATE,
                confidence=0.0,
                reason_codes=["GATEWAY_TIMEOUT", "FALLBACK_TO_DETERMINISTIC"],
                required_tools=[],
                requires_human_review=True,
                explanation=f"AI Model Gateway timed out after {self.timeout_sec}s; escalated to human review.",
            )

        except ModelProviderError as err:
            logger.error(f"[AI GATEWAY PROVIDER ERROR] Provider error: {err}")
            return AIRecoveryRecommendation(
                recommended_strategy=AIRecoveryStrategy.ESCALATE,
                confidence=0.0,
                reason_codes=["PROVIDER_OUTAGE", "FALLBACK_TO_DETERMINISTIC"],
                required_tools=[],
                requires_human_review=True,
                explanation=f"AI model provider unavailable ({err}); escalated to human review.",
            )

        except Exception as err:
            logger.error(f"[AI GATEWAY UNEXPECTED ERROR] Unexpected failure in gateway: {err}")
            return AIRecoveryRecommendation(
                recommended_strategy=AIRecoveryStrategy.ESCALATE,
                confidence=0.0,
                reason_codes=["GATEWAY_UNEXPECTED_ERROR"],
                required_tools=[],
                requires_human_review=True,
                explanation=f"Unexpected failure in AI gateway ({err}); escalated to human review.",
            )

    def _parse_and_validate(self, raw_text: str, provider_name: str) -> AIRecoveryRecommendation:
        """Extract JSON, validate against Pydantic schema, check confidence and tools."""
        cleaned_json = self._extract_json_substring(raw_text)

        try:
            data = json.loads(cleaned_json)
            recommendation = AIRecoveryRecommendation(**data)
        except (json.JSONDecodeError, ValidationError, TypeError) as err:
            logger.warning(f"[AI SCHEMA VALIDATION FAILED] Provider '{provider_name}' produced invalid output: {err}")
            return AIRecoveryRecommendation(
                recommended_strategy=AIRecoveryStrategy.ESCALATE,
                confidence=0.0,
                reason_codes=["MALFORMED_OUTPUT_OR_INVALID_STRATEGY"],
                required_tools=[],
                requires_human_review=True,
                explanation="Model output failed schema validation; escalated to human review.",
            )

        # 1. Tool Whitelist Validation (Declaration only, zero execution)
        for tool in recommendation.required_tools:
            if tool not in ALLOWED_AI_TOOLS:
                logger.warning(
                    f"[HALLUCINATED TOOL REJECTED] Model declared unauthorized tool '{tool}'. "
                    f"Overruling recommendation to ESCALATE."
                )
                recommendation.recommended_strategy = AIRecoveryStrategy.ESCALATE
                recommendation.requires_human_review = True
                recommendation.reason_codes.append("UNAUTHORIZED_OR_HALLUCINATED_TOOL")
                recommendation.explanation = (
                    f"Model declared unauthorized tool '{tool}'. Escalated to human review."
                )
                return recommendation

        # 2. Confidence Threshold Enforcement
        if recommendation.confidence < self.confidence_threshold:
            logger.info(
                f"[LOW CONFIDENCE OVERRULE] Score {recommendation.confidence:.2f} is below "
                f"threshold {self.confidence_threshold:.2f}. Forcing human review."
            )
            recommendation.recommended_strategy = AIRecoveryStrategy.ESCALATE
            recommendation.requires_human_review = True
            recommendation.reason_codes.append("LOW_CONFIDENCE_ESCALATION")
            recommendation.explanation = (
                f"Recommendation confidence ({recommendation.confidence * 100:.0f}%) below "
                f"policy threshold ({self.confidence_threshold * 100:.0f}%). Escalated to human review."
            )

        return recommendation

    @staticmethod
    def _extract_json_substring(text: str) -> str:
        """Extract JSON substring even if model wraps output in markdown code blocks."""
        text = text.strip()
        # Look for ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Or find first '{' to last '}'
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text
