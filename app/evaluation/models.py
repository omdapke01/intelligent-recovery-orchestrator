"""Data contracts and metric models for the recovery benchmark evaluation pipeline."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import json
from typing import Any, Dict, List, Optional
import uuid

from app.models.enums import FailureCategory, PaymentMethod, RecoveryStrategy


@dataclass
class PricingConfig:
    """Configurable synthetic LLM inference pricing and exchange rate assumptions."""
    fast_input_per_1k_usd: float = 0.0005
    fast_output_per_1k_usd: float = 0.0015
    deep_input_per_1k_usd: float = 0.0100
    deep_output_per_1k_usd: float = 0.0300
    structured_input_per_1k_usd: float = 0.0020
    structured_output_per_1k_usd: float = 0.0060
    usd_to_inr: float = 85.0
    customer_notification_conversion_rate: float = 0.35
    is_synthetic: bool = True
    disclaimer: str = (
        "Synthetic benchmark simulation assumptions (including 35% customer action link conversion). "
        "Not Razorpay contractual rates."
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkCase:
    """Individual payment case with known ground truth for benchmark evaluation."""
    case_id: str
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    customer_id: uuid.UUID
    amount_inr: Decimal
    payment_method: PaymentMethod
    route_id: str
    route_health: float
    error_code: str
    failure_category: FailureCategory
    reason: str
    attempt_number: int = 1
    alternative_routes: List[str] = field(default_factory=list)
    merchant_tier: str = "GROWTH"
    merchant_max_retries: int = 2
    merchant_auto_escalate_inr: Decimal = Decimal("50000.00")
    merchant_recovery_enabled: bool = True

    # Ground truth flags
    is_retryable_ground_truth: bool = True
    expected_optimal_strategy: RecoveryStrategy = RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF
    is_customer_independently_succeeded: bool = False  # Late success / stale retry scenario
    is_in_flight_pending: bool = False                 # In-flight asynchronous state scenario
    is_prohibited: bool = False                        # Fraud / hard decline scenario
    is_high_value: bool = False                        # Exceeds amount cap scenario
    value_segment: str = "STANDARD"                    # MICRO (<500), STANDARD (500-10000), HIGH_VALUE (>50000)


@dataclass
class ExecutionRecord:
    """Single recovery outcome under a specific engine (Naive Baseline vs IRO)."""
    engine_name: str
    payment_id: str
    amount_inr: float
    payment_method: str
    value_segment: str
    failure_category: str
    error_code: str
    strategy_chosen: str
    target_route_id: Optional[str]
    recovered: bool
    recovered_revenue_inr: float
    is_unsafe_action: bool          # Did the engine attempt an unsafe retry? (e.g. fraud, stale success, double debit)
    escalated_to_human: bool
    stopped: bool
    latency_ms: float
    tier_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    synthetic_cost_usd: float = 0.0
    synthetic_cost_inr: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SegmentedGroupMetrics:
    """Metrics aggregated for a specific cohort (e.g. by payment method or value segment)."""
    group_key: str
    total_cases: int
    baseline_recovered: int
    iro_recovered: int
    baseline_recovery_rate: float
    iro_recovery_rate: float
    recovery_rate_lift_pct: float
    baseline_revenue_inr: float
    iro_revenue_inr: float
    revenue_lift_inr: float
    revenue_lift_pct: float
    unsafe_actions_prevented: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AggregateEngineMetrics:
    """Comprehensive aggregated telemetry and performance metrics for a single recovery engine."""
    engine_name: str
    total_cases: int
    retryable_cases: int
    non_retryable_cases: int
    recovered_count: int
    recovery_rate_pct: float               # recovered / total_cases * 100
    retryable_recovery_rate_pct: float     # recovered / retryable_cases * 100
    recovered_revenue_inr: float
    unsafe_actions_count: int              # Unsafe retries executed by this engine
    escalations_count: int
    stopped_count: int
    avg_latency_ms: float
    tier_breakdown: Dict[str, int] = field(default_factory=dict)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    synthetic_total_cost_usd: float = 0.0
    synthetic_total_cost_inr: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkComparisonReport:
    """Comprehensive side-by-side evaluation report comparing Naive Single-Rail Baseline vs IRO."""
    report_id: str
    timestamp: str
    total_cases: int
    baseline_metrics: AggregateEngineMetrics
    iro_metrics: AggregateEngineMetrics

    # Lift & Financial ROI Multipliers
    absolute_recovery_rate_lift_pct: float      # iro_rate - baseline_rate (percentage points)
    relative_recovery_rate_lift_pct: float      # ((iro_rate - baseline_rate) / baseline_rate) * 100
    incremental_recovered_revenue_inr: float    # iro_revenue - baseline_revenue
    revenue_lift_pct: float                     # (incremental / baseline_revenue) * 100
    unsafe_actions_prevented: int               # baseline_unsafe - iro_unsafe
    ai_total_cost_inr: float                    # total AI serving cost
    roi_multiplier: float                       # incremental_revenue / ai_cost (0.0 if cost is 0)

    # Synthetic Pricing Assumptions
    pricing_config: PricingConfig

    # Segmented Cohort Breakdowns
    by_payment_method: Dict[str, SegmentedGroupMetrics] = field(default_factory=dict)
    by_value_segment: Dict[str, SegmentedGroupMetrics] = field(default_factory=dict)
    by_failure_category: Dict[str, SegmentedGroupMetrics] = field(default_factory=dict)

    # Detailed Incident Records
    records: List[ExecutionRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "baseline_metrics": self.baseline_metrics.to_dict(),
            "iro_metrics": self.iro_metrics.to_dict(),
            "comparison": {
                "absolute_recovery_rate_lift_pct": round(self.absolute_recovery_rate_lift_pct, 2),
                "relative_recovery_rate_lift_pct": round(self.relative_recovery_rate_lift_pct, 2),
                "incremental_recovered_revenue_inr": round(self.incremental_recovered_revenue_inr, 2),
                "revenue_lift_pct": round(self.revenue_lift_pct, 2),
                "unsafe_actions_prevented": self.unsafe_actions_prevented,
                "ai_total_cost_inr": round(self.ai_total_cost_inr, 2),
                "roi_multiplier": round(self.roi_multiplier, 1),
            },
            "pricing_assumptions": self.pricing_config.to_dict(),
            "segmented_analysis": {
                "by_payment_method": {k: v.to_dict() for k, v in self.by_payment_method.items()},
                "by_value_segment": {k: v.to_dict() for k, v in self.by_value_segment.items()},
                "by_failure_category": {k: v.to_dict() for k, v in self.by_failure_category.items()},
            },
            "sample_records": [r.to_dict() for r in self.records[:50]],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
