"""Strongly typed schemas for the Specialist Recovery Investigation Agent and audit trails."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas import AIRecoveryRecommendation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationStatus(str, Enum):
    """Lifecycle / stopping status of the specialist recovery investigation."""
    COMPLETED = "COMPLETED"
    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    MAX_CALLS_REACHED = "MAX_CALLS_REACHED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    POLICY_HALT = "POLICY_HALT"


class DecisionTraceEntry(BaseModel):
    """Auditable trace entry capturing what happened, tool used, evidence extracted,

    and reason code. Replaces private model chain-of-thought with auditable facts.
    """
    step_number: int
    action: str = Field(description="Action type: TOOL_CALL, EVIDENCE_EVALUATION, RECOMMENDATION, etc.")
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    evidence_summary: str = Field(default="", description="Factual summary of findings from this step")
    reason_code: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid")


class ToolCallRecord(BaseModel):
    """Execution record of an individual read-only tool invocation."""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    status: str = "SUCCESS"  # SUCCESS or ERROR
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid")


class AgentInvestigationAuditPayload(BaseModel):
    """Durable audit event payload for 'agent.investigation.completed'.

    Guarantees a permanent record of all tool calls, evidence, decision traces,
    and recommendations generated during the investigation.
    """
    investigation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    payment_id: uuid.UUID
    recovery_case_id: Optional[uuid.UUID] = None
    status: InvestigationStatus
    iterations_count: int = 0
    tool_calls_count: int = 0
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    decision_trace: List[DecisionTraceEntry] = Field(default_factory=list)
    recommendation: AIRecoveryRecommendation
    evidence_collected: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    completed_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid")
