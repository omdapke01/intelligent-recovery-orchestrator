"""Domain models and result contracts for the Hard Financial Safety Policy Engine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid

from app.models.enums import PaymentMethod, RecoveryStrategy


class PolicyDecision(str, Enum):
    """Authoritative outcome of financial policy evaluation."""
    PERMITTED = "PERMITTED"
    DENIED = "DENIED"
    REQUIRES_HUMAN_APPROVAL = "REQUIRES_HUMAN_APPROVAL"

    @property
    def is_permitted(self) -> bool:
        return self == PolicyDecision.PERMITTED

    @property
    def is_denied(self) -> bool:
        return self == PolicyDecision.DENIED

    @property
    def requires_human_approval(self) -> bool:
        return self == PolicyDecision.REQUIRES_HUMAN_APPROVAL


@dataclass
class RecoveryPolicyConfig:
    """Configuration-driven thresholds and bounds for recovery policies.

    Eliminates magic constants from rule evaluations.
    """
    # Max Recovery Windows by Payment Method (in seconds)
    max_window_sec_by_method: Dict[PaymentMethod, float] = field(
        default_factory=lambda: {
            PaymentMethod.UPI: 900.0,         # 15 minutes
            PaymentMethod.CREDIT_CARD: 7200.0, # 2 hours
            PaymentMethod.DEBIT_CARD: 7200.0,  # 2 hours
            PaymentMethod.NETBANKING: 3600.0,  # 1 hour
            PaymentMethod.WALLET: 1800.0,      # 30 minutes
        }
    )
    default_max_window_sec: float = 3600.0

    # High-Value Automated Limits
    system_max_recovery_amount_inr: Decimal = Decimal("100000.00")
    default_auto_escalate_threshold_inr: Decimal = Decimal("50000.00")

    # Permitted Strategy Whitelist
    permitted_strategies: Set[RecoveryStrategy] = field(
        default_factory=lambda: {
            RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
            RecoveryStrategy.ROUTE_FAILOVER,
            RecoveryStrategy.NOTIFY_CUSTOMER_LINK,
            RecoveryStrategy.MANUAL_REVIEW,
            RecoveryStrategy.TERMINAL_ABANDON,
        }
    )

    # Permitted Payment Rails for Automated Retry
    permitted_retry_methods: Set[PaymentMethod] = field(
        default_factory=lambda: {
            PaymentMethod.UPI,
            PaymentMethod.CREDIT_CARD,
            PaymentMethod.DEBIT_CARD,
            PaymentMethod.NETBANKING,
        }
    )

    # Security Limits
    max_fraud_risk_score_allowed: float = 0.85
    policy_version: str = "financial_safety_v1.0"


@dataclass
class PolicyEvaluationResult:
    """Detailed result of evaluating financial authorization policies."""
    evaluation_id: uuid.UUID
    policy_version: str
    decision: PolicyDecision
    violated_policies: List[str] = field(default_factory=list)
    reason: str = ""
    evaluated_policies: List[str] = field(default_factory=list)
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_id": str(self.evaluation_id),
            "policy_version": self.policy_version,
            "decision": self.decision.value,
            "violated_policies": self.violated_policies,
            "reason": self.reason,
            "evaluated_policies": self.evaluated_policies,
            "risk_level": self.risk_level,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }
