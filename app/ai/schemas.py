"""Strongly typed Pydantic schemas for the AI Model Gateway and recommendation layer."""

from enum import Enum
from typing import List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RecoveryStrategy


class AIRecoveryStrategy(str, Enum):
    """Initial set of recovery strategies recommended by the AI layer."""
    RETRY = "RETRY"
    RETRY_LATER = "RETRY_LATER"
    CUSTOMER_NOTIFICATION = "CUSTOMER_NOTIFICATION"
    ALTERNATE_METHOD = "ALTERNATE_METHOD"
    ESCALATE = "ESCALATE"
    STOP = "STOP"


# Whitelist of permitted tools that the AI may declare in required_tools
ALLOWED_AI_TOOLS: Set[str] = {
    "query_route_health",
    "check_customer_risk",
    "calculate_backoff",
    "request_alternative_rail",
}


class AIRecoveryRecommendation(BaseModel):
    """Structured, schema-validated recommendation produced by the AI Model Gateway.

    Invariants:
    1. AI only produces a recommendation; it never mutates state or executes payment actions.
    2. required_tools are strictly DECLARATIVE in Phase 5; tools are not executed.
    3. Low confidence (< threshold) or unknown tools trigger immediate escalation.
    """
    recommended_strategy: AIRecoveryStrategy
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence score between 0.0 and 1.0")
    reason_codes: List[str] = Field(default_factory=list, description="Diagnostic taxonomy codes justifying the decision")
    required_tools: List[str] = Field(default_factory=list, description="Declarative list of tools relevant to this recommendation")
    requires_human_review: bool = Field(default=False, description="Whether human review is required before execution")
    explanation: str = Field(default="", description="Human-readable explanation of why this strategy was selected")
    suggested_delay_sec: Optional[int] = Field(default=None, description="Suggested delay before execution (for RETRY_LATER)")
    target_route: Optional[str] = Field(default=None, description="Suggested alternative route (for ALTERNATE_METHOD)")

    model_config = ConfigDict(extra="forbid")


def map_ai_strategy_to_domain(strategy: AIRecoveryStrategy) -> RecoveryStrategy:
    """Map AI recommendation strategy to the core orchestrator domain strategy."""
    mapping = {
        AIRecoveryStrategy.RETRY: RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        AIRecoveryStrategy.RETRY_LATER: RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        AIRecoveryStrategy.CUSTOMER_NOTIFICATION: RecoveryStrategy.NOTIFY_CUSTOMER_LINK,
        AIRecoveryStrategy.ALTERNATE_METHOD: RecoveryStrategy.ROUTE_FAILOVER,
        AIRecoveryStrategy.ESCALATE: RecoveryStrategy.MANUAL_REVIEW,
        AIRecoveryStrategy.STOP: RecoveryStrategy.TERMINAL_ABANDON,
    }
    return mapping.get(strategy, RecoveryStrategy.MANUAL_REVIEW)
