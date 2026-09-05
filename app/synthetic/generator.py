"""High-performance, streaming Synthetic Payment Data Generator."""

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Generator, List, Optional

from app.models.enums import (
    AttemptStatus,
    FailureCategory,
    MerchantTier,
    PaymentLifecycleState,
    PaymentMethod,
    RecoveryStrategy,
    RouteStatus,
)
from app.synthetic.profiles import (
    FAILURE_TAXONOMY,
    MERCHANT_PROFILES,
    ROUTE_PROFILES,
)


@dataclass
class SyntheticRecord:
    """Consolidated representation of a synthesized payment transaction and all linked entities."""
    merchant_data: Dict[str, Any]
    customer_data: Dict[str, Any]
    route_data: Dict[str, Any]
    payment_data: Dict[str, Any]
    attempts_data: List[Dict[str, Any]] = field(default_factory=list)
    failures_data: List[Dict[str, Any]] = field(default_factory=list)
    recovery_case_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to a JSON-serializable dictionary."""
        return {
            "payment": {
                **self.payment_data,
                "id": str(self.payment_data["id"]),
                "merchant_id": str(self.payment_data["merchant_id"]),
                "customer_id": str(self.payment_data["customer_id"]),
                "amount_inr": float(self.payment_data["amount_inr"]),
                "status": self.payment_data["status"].value,
                "payment_method": self.payment_data["payment_method"].value,
                "created_at": self.payment_data["created_at"].isoformat(),
                "updated_at": self.payment_data["updated_at"].isoformat(),
            },
            "merchant": {
                **self.merchant_data,
                "id": str(self.merchant_data["id"]),
                "tier": self.merchant_data["tier"].value,
                "max_recovery_amount_inr": float(self.merchant_data["max_recovery_amount_inr"]),
                "auto_escalate_threshold_inr": float(self.merchant_data["auto_escalate_threshold_inr"]),
                "allowed_methods": [m.value for m in self.merchant_data["allowed_methods"]],
            },
            "customer": {
                **self.customer_data,
                "id": str(self.customer_data["id"]),
            },
            "route": {
                **self.route_data,
                "payment_method": self.route_data["payment_method"].value,
                "status": self.route_data["status"].value,
            },
            "attempts": [
                {
                    **att,
                    "id": str(att["id"]),
                    "payment_id": str(att["payment_id"]),
                    "payment_method": att["payment_method"].value,
                    "status": att["status"].value,
                    "initiated_at": att["initiated_at"].isoformat(),
                    "completed_at": att["completed_at"].isoformat() if att.get("completed_at") else None,
                }
                for att in self.attempts_data
            ],
            "failures": [
                {
                    **fail,
                    "id": str(fail["id"]),
                    "attempt_id": str(fail["attempt_id"]),
                    "payment_id": str(fail["payment_id"]),
                    "failure_category": fail["failure_category"].value,
                    "error_code": fail["error_code"],
                    "reason": fail.get("reason", fail.get("error_message", "")),
                    "recoverable": fail.get("recoverable", fail.get("is_recoverable", False)),
                    "suggested_backoff_sec": fail.get("suggested_backoff_sec", 0),
                    "detected_at": fail["detected_at"].isoformat(),
                }
                for fail in self.failures_data
            ],
            "recovery_case": (
                {
                    "id": str(self.recovery_case_data["id"]),
                    "payment_id": str(self.recovery_case_data["payment_id"]),
                    "status": self.recovery_case_data["status"].value,
                    "strategy": (self.recovery_case_data.get("strategy") or self.recovery_case_data.get("recommended_strategy")).value,
                    "attempt_count": self.recovery_case_data.get("attempt_count", self.recovery_case_data.get("retry_count", 0)),
                    "max_attempts": self.recovery_case_data.get("max_attempts", self.recovery_case_data.get("max_retries", 2)),
                    "started_at": (
                        self.recovery_case_data["started_at"].isoformat()
                        if self.recovery_case_data.get("started_at")
                        else None
                    ),
                    "completed_at": (
                        self.recovery_case_data["completed_at"].isoformat()
                        if self.recovery_case_data.get("completed_at")
                        else (
                            self.recovery_case_data["resolved_at"].isoformat()
                            if self.recovery_case_data.get("resolved_at")
                            else None
                        )
                    ),
                    "stop_reason": self.recovery_case_data.get("stop_reason"),
                    "estimated_recovery_rate": self.recovery_case_data.get("estimated_recovery_rate"),
                    "recovered_amount_inr": float(self.recovery_case_data["recovered_amount_inr"]),
                }
                if self.recovery_case_data
                else None
            ),
        }


class SyntheticPaymentGenerator:
    """
    High-volume synthetic payment data generator.
    Supports stream generation (O(1) memory) for 100,000+ payments.
    """

    def __init__(
        self,
        seed: Optional[int] = 42,
        base_success_rate: float = 0.78,
        customer_pool_size: int = 1000,
    ):
        self.rng = random.Random(seed)
        self.base_success_rate = base_success_rate
        self.merchants = self._init_merchants()
        self.routes = self._init_routes()
        self.customers = self._init_customer_pool(customer_pool_size)

    def _init_merchants(self) -> List[Dict[str, Any]]:
        merchants = []
        for p in MERCHANT_PROFILES:
            merchants.append({
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "name": p["name"],
                "mcc": p["mcc"],
                "tier": p["tier"],
                "recovery_enabled": p["recovery_enabled"],
                "max_auto_retries": p["max_auto_retries"],
                "max_recovery_amount_inr": p["max_recovery_amount_inr"],
                "auto_escalate_threshold_inr": p["auto_escalate_threshold_inr"],
                "allowed_methods": p["allowed_methods"],
            })
        return merchants

    def _init_routes(self) -> List[Dict[str, Any]]:
        return list(ROUTE_PROFILES)

    def _init_customer_pool(self, size: int) -> List[Dict[str, Any]]:
        customers = []
        for i in range(size):
            # Realistic risk tier distribution
            dice = self.rng.random()
            if dice < 0.70:
                success_rate = round(self.rng.uniform(0.92, 0.99), 3)
                risk_score = round(self.rng.uniform(0.01, 0.08), 3)
            elif dice < 0.90:
                success_rate = round(self.rng.uniform(0.80, 0.92), 3)
                risk_score = round(self.rng.uniform(0.08, 0.25), 3)
            else:
                success_rate = round(self.rng.uniform(0.50, 0.80), 3)
                risk_score = round(self.rng.uniform(0.30, 0.75), 3)

            customers.append({
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "external_id": f"cust_{i+1:06d}",
                "email_masked": f"c***{i+1}@example.com",
                "phone_masked": f"+91-98****{i+1:04d}",
                "historical_success_rate": success_rate,
                "total_transactions": self.rng.randint(5, 120),
                "risk_score": risk_score,
            })
        return customers

    def _pick_amount(self) -> Decimal:
        """Pick realistic amount spanning micro to high-value tickets."""
        r = self.rng.random()
        if r < 0.40:
            # Micro: ₹50 - ₹500
            val = self.rng.uniform(50, 500)
        elif r < 0.80:
            # Retail: ₹500 - ₹5,000
            val = self.rng.uniform(500, 5000)
        elif r < 0.95:
            # Mid-value: ₹5,000 - ₹25,000
            val = self.rng.uniform(5000, 25000)
        else:
            # High-value: ₹25,000 - ₹120,000
            val = self.rng.uniform(25000, 120000)
        return Decimal(f"{val:.2f}")

    def _pick_route(self, payment_method: PaymentMethod) -> Dict[str, Any]:
        compatible_routes = [r for r in self.routes if r["payment_method"] == payment_method]
        if not compatible_routes:
            compatible_routes = self.routes
        return self.rng.choice(compatible_routes)

    def _pick_failure(self, route: Dict[str, Any], customer: Dict[str, Any]) -> Dict[str, Any]:
        """Weighted selection of failure category and error code."""
        # Route health influences category probability
        route_degraded = route["status"] == RouteStatus.DEGRADED or route["health_score"] < 0.90
        customer_high_risk = customer["risk_score"] > 0.40

        cat_weights = {
            FailureCategory.TRANSIENT: 0.40,
            FailureCategory.ROUTE_DEGRADATION: 0.20,
            FailureCategory.CUSTOMER_ACTION_REQUIRED: 0.22,
            FailureCategory.PERMANENT: 0.14,
            FailureCategory.FRAUD: 0.04,
        }

        if route_degraded:
            cat_weights[FailureCategory.ROUTE_DEGRADATION] += 0.30
            cat_weights[FailureCategory.TRANSIENT] += 0.15
        if customer_high_risk:
            cat_weights[FailureCategory.FRAUD] += 0.25
            cat_weights[FailureCategory.PERMANENT] += 0.15

        categories = list(cat_weights.keys())
        weights = [cat_weights[c] for c in categories]
        chosen_cat = self.rng.choices(categories, weights=weights, k=1)[0]

        # Select specific error within category
        errors_in_cat = FAILURE_TAXONOMY[chosen_cat]
        err_weights = [e["weight"] for e in errors_in_cat]
        chosen_err = self.rng.choices(errors_in_cat, weights=err_weights, k=1)[0]

        return {
            "failure_category": chosen_cat,
            "error_code": chosen_err["error_code"],
            "reason": chosen_err.get("reason", chosen_err.get("message", "")),
            "error_message": chosen_err.get("reason", chosen_err.get("message", "")),
            "recoverable": chosen_err["recoverable"],
            "is_recoverable": chosen_err["recoverable"],
            "suggested_backoff_sec": chosen_err["suggested_backoff_sec"],
        }

    def generate_record(self, index: int, base_time: Optional[datetime] = None) -> SyntheticRecord:
        """Generate a single realistic payment transaction record."""
        now = base_time or datetime.now(timezone.utc)
        payment_id = uuid.UUID(int=self.rng.getrandbits(128))

        merchant = self.rng.choice(self.merchants)
        customer = self.rng.choice(self.customers)
        payment_method = self.rng.choice(merchant["allowed_methods"])
        route = self._pick_route(payment_method)
        amount = self._pick_amount()

        # Dynamic success probability calculation
        # Route health (0.8 - 1.0) * Customer history (0.5 - 1.0) * Base success rate
        combined_prob = (
            self.base_success_rate
            * route["health_score"]
            * (0.8 + 0.2 * customer["historical_success_rate"])
        )
        is_successful = self.rng.random() < combined_prob

        attempts: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        recovery_case: Optional[Dict[str, Any]] = None

        if is_successful:
            # Clean first-attempt success
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            latency = int(self.rng.gauss(route["avg_latency_ms"], 30))
            attempts.append({
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.SUCCESS,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": max(45, latency),
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=max(45, latency)),
            })
            lifecycle_state = PaymentLifecycleState.SUCCESS
            final_error_code = None

        else:
            # Failed payment scenario
            fail_meta = self._pick_failure(route, customer)
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            latency = (
                int(route["avg_latency_ms"] * 2.5)
                if fail_meta["error_code"] == "GATEWAY_TIMEOUT"
                else int(self.rng.gauss(route["avg_latency_ms"], 40))
            )

            attempts.append({
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": max(60, latency),
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=max(60, latency)),
            })

            failure_id = uuid.UUID(int=self.rng.getrandbits(128))
            failures.append({
                "id": failure_id,
                "attempt_id": attempt_id,
                "payment_id": payment_id,
                "failure_category": fail_meta["failure_category"],
                "error_code": fail_meta["error_code"],
                "reason": fail_meta["reason"],
                "error_message": fail_meta["reason"],
                "recoverable": fail_meta["recoverable"],
                "is_recoverable": fail_meta["recoverable"],
                "suggested_backoff_sec": fail_meta["suggested_backoff_sec"],
                "detected_at": now + timedelta(milliseconds=max(60, latency)),
            })

            # Check if repeated failure (attempt 2 also failed)
            repeated_failure = self.rng.random() < 0.20
            if repeated_failure and fail_meta["recoverable"]:
                attempt_2_id = uuid.UUID(int=self.rng.getrandbits(128))
                fail_2_meta = self._pick_failure(route, customer)
                attempts.append({
                    "id": attempt_2_id,
                    "payment_id": payment_id,
                    "attempt_number": 2,
                    "route_id": route["id"],
                    "payment_method": payment_method,
                    "status": AttemptStatus.FAILED,
                    "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                    "latency_ms": 250,
                    "initiated_at": now + timedelta(seconds=fail_meta["suggested_backoff_sec"]),
                    "completed_at": now + timedelta(seconds=fail_meta["suggested_backoff_sec"], milliseconds=250),
                })
                failures.append({
                    "id": uuid.UUID(int=self.rng.getrandbits(128)),
                    "attempt_id": attempt_2_id,
                    "payment_id": payment_id,
                    "failure_category": fail_2_meta["failure_category"],
                    "error_code": fail_2_meta["error_code"],
                    "reason": fail_2_meta["reason"],
                    "error_message": fail_2_meta["reason"],
                    "recoverable": fail_2_meta["recoverable"],
                    "is_recoverable": fail_2_meta["recoverable"],
                    "suggested_backoff_sec": fail_2_meta["suggested_backoff_sec"],
                    "detected_at": now + timedelta(seconds=fail_meta["suggested_backoff_sec"], milliseconds=250),
                })

            final_error_code = failures[-1]["error_code"]
            stop_reason: Optional[str] = None

            # Set lifecycle state based on recoverability, amounts, and attempt limits
            if not fail_meta["recoverable"]:
                lifecycle_state = PaymentLifecycleState.STOPPED
                recovery_strategy = RecoveryStrategy.TERMINAL_ABANDON
                estimated_rate = 0.0
                stop_reason = (
                    "FRAUD_DETECTED"
                    if fail_meta["failure_category"] == FailureCategory.FRAUD
                    else "UNRECOVERABLE_FAILURE"
                )
            elif amount >= merchant["auto_escalate_threshold_inr"]:
                lifecycle_state = PaymentLifecycleState.ESCALATED
                recovery_strategy = RecoveryStrategy.MANUAL_REVIEW
                estimated_rate = 0.50
                stop_reason = "HIGH_VALUE_THRESHOLD_EXCEEDED"
            elif len(attempts) >= merchant["max_auto_retries"]:
                lifecycle_state = PaymentLifecycleState.STOPPED
                recovery_strategy = RecoveryStrategy.TERMINAL_ABANDON
                estimated_rate = 0.0
                stop_reason = "MAX_RETRIES_EXCEEDED"
            else:
                lifecycle_state = PaymentLifecycleState.RECOVERY_PENDING
                if fail_meta["failure_category"] == FailureCategory.TRANSIENT:
                    recovery_strategy = RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF
                    estimated_rate = 0.85
                elif fail_meta["failure_category"] == FailureCategory.ROUTE_DEGRADATION:
                    recovery_strategy = RecoveryStrategy.ROUTE_FAILOVER
                    estimated_rate = 0.75
                else:
                    recovery_strategy = RecoveryStrategy.NOTIFY_CUSTOMER_LINK
                    estimated_rate = 0.60

            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": recovery_strategy,
                "recommended_strategy": recovery_strategy,
                "attempt_count": len(attempts) - 1,
                "retry_count": len(attempts) - 1,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": now if lifecycle_state in (PaymentLifecycleState.STOPPED, PaymentLifecycleState.ESCALATED) else None,
                "resolved_at": now if lifecycle_state in (PaymentLifecycleState.STOPPED, PaymentLifecycleState.ESCALATED) else None,
                "stop_reason": stop_reason,
                "estimated_recovery_rate": estimated_rate,
                "recovered_amount_inr": Decimal("0.00"),
            }

        payment_data = {
            "id": payment_id,
            "merchant_id": merchant["id"],
            "customer_id": customer["id"],
            "amount_inr": amount,
            "currency": "INR",
            "payment_method": payment_method,
            "preferred_route_id": route["id"],
            "status": lifecycle_state,
            "final_error_code": final_error_code,
            "idempotency_key": f"pay_syn_{payment_id.hex[:16]}",
            "metadata_json": {
                "synthetic_index": index,
                "source": "IRO_SYNTHETIC_V1",
            },
            "created_at": now,
            "updated_at": now,
        }

        return SyntheticRecord(
            merchant_data=merchant,
            customer_data=customer,
            route_data=route,
            payment_data=payment_data,
            attempts_data=attempts,
            failures_data=failures,
            recovery_case_data=recovery_case,
        )

    def generate_stream(
        self,
        count: int,
        start_time: Optional[datetime] = None,
    ) -> Generator[SyntheticRecord, None, None]:
        """Yield records one-by-one to support streaming generation for 100,000+ items."""
        base = start_time or datetime.now(timezone.utc)
        for i in range(count):
            # Time progression: each payment arrives roughly every 1-10 seconds
            t = base + timedelta(seconds=i * self.rng.uniform(1.0, 5.0))
            yield self.generate_record(index=i, base_time=t)

    def generate_batch(self, count: int) -> List[SyntheticRecord]:
        """Generate a batch in-memory for testing small datasets (e.g. 10, 100, 1000)."""
        return list(self.generate_stream(count))

    def generate_scenario(self, scenario_name: str, index: int = 0) -> SyntheticRecord:
        """
        Generate a deterministic payment transaction matching an explicit demo scenario.
        Scenarios:
          - 'healthy-transient': UPI timeout on healthy HDFC route -> backoff retry
          - 'degraded-route': UPI failure on degraded SBI route -> route failover
          - 'customer-action': Insufficient funds -> customer notification/link (no blind retry)
          - 'repeated-failure': Repeated failures on high-value payment -> escalation
          - 'fraud-stop': Fraud engine trigger on high-risk profile -> immediate STOPPED
          - 'max-retries': Retry limits reached -> STOPPED
        """
        now = datetime.now(timezone.utc)
        payment_id = uuid.UUID(int=self.rng.getrandbits(128))

        if scenario_name == "healthy-transient":
            merchant = self.merchants[0]  # Zomato
            customer = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "external_id": "cust_prime_001",
                "email_masked": "p***@example.com",
                "phone_masked": "+91-98****0001",
                "historical_success_rate": 0.98,
                "total_transactions": 50,
                "risk_score": 0.02,
            }
            route = next(r for r in self.routes if r["id"] == "ROUTE_HDFC_UPI")
            payment_method = PaymentMethod.UPI
            amount = Decimal("850.00")
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [{
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": 300,
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=300),
            }]
            failures = [{
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "attempt_id": attempt_id,
                "payment_id": payment_id,
                "failure_category": FailureCategory.TRANSIENT,
                "error_code": "GATEWAY_TIMEOUT",
                "reason": "Gateway timed out waiting for upstream bank response (504)",
                "error_message": "Gateway timed out waiting for upstream bank response (504)",
                "recoverable": True,
                "is_recoverable": True,
                "suggested_backoff_sec": 45,
                "detected_at": now + timedelta(milliseconds=300),
            }]
            lifecycle_state = PaymentLifecycleState.RECOVERY_PENDING
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
                "recommended_strategy": RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
                "attempt_count": 0,
                "retry_count": 0,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": None,
                "resolved_at": None,
                "stop_reason": None,
                "estimated_recovery_rate": 0.90,
                "recovered_amount_inr": Decimal("0.00"),
            }

        elif scenario_name == "degraded-route":
            merchant = self.merchants[1]  # Croma
            customer = self.customers[0]
            route = next(r for r in self.routes if r["id"] == "ROUTE_SBI_UPI")
            payment_method = PaymentMethod.UPI
            amount = Decimal("3200.00")
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [{
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": 650,
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=650),
            }]
            failures = [{
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "attempt_id": attempt_id,
                "payment_id": payment_id,
                "failure_category": FailureCategory.ROUTE_DEGRADATION,
                "error_code": "BANK_DOWNTIME",
                "reason": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
                "error_message": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
                "recoverable": True,
                "is_recoverable": True,
                "suggested_backoff_sec": 180,
                "detected_at": now + timedelta(milliseconds=650),
            }]
            lifecycle_state = PaymentLifecycleState.RECOVERY_PENDING
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.ROUTE_FAILOVER,
                "recommended_strategy": RecoveryStrategy.ROUTE_FAILOVER,
                "attempt_count": 0,
                "retry_count": 0,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": None,
                "resolved_at": None,
                "stop_reason": None,
                "estimated_recovery_rate": 0.75,
                "recovered_amount_inr": Decimal("0.00"),
            }

        elif scenario_name == "customer-action":
            merchant = self.merchants[2]  # Urban Company
            customer = self.customers[1]
            route = next(r for r in self.routes if r["id"] == "ROUTE_AXIS_CARDS")
            payment_method = PaymentMethod.CREDIT_CARD
            amount = Decimal("4500.00")
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [{
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": 220,
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=220),
            }]
            failures = [{
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "attempt_id": attempt_id,
                "payment_id": payment_id,
                "failure_category": FailureCategory.CUSTOMER_ACTION_REQUIRED,
                "error_code": "INSUFFICIENT_FUNDS",
                "reason": "Declined by issuing bank due to insufficient balance",
                "error_message": "Declined by issuing bank due to insufficient balance",
                "recoverable": True,
                "is_recoverable": True,
                "suggested_backoff_sec": 300,
                "detected_at": now + timedelta(milliseconds=220),
            }]
            lifecycle_state = PaymentLifecycleState.RECOVERY_PENDING
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.NOTIFY_CUSTOMER_LINK,
                "recommended_strategy": RecoveryStrategy.NOTIFY_CUSTOMER_LINK,
                "attempt_count": 0,
                "retry_count": 0,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": None,
                "resolved_at": None,
                "stop_reason": None,
                "estimated_recovery_rate": 0.60,
                "recovered_amount_inr": Decimal("0.00"),
            }

        elif scenario_name == "repeated-failure":
            merchant = self.merchants[1]  # Croma
            customer = self.customers[2]
            route = next(r for r in self.routes if r["id"] == "ROUTE_HDFC_UPI")
            payment_method = PaymentMethod.UPI
            amount = Decimal("85000.00")  # High value!
            att1_id = uuid.UUID(int=self.rng.getrandbits(128))
            att2_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [
                {
                    "id": att1_id,
                    "payment_id": payment_id,
                    "attempt_number": 1,
                    "route_id": route["id"],
                    "payment_method": payment_method,
                    "status": AttemptStatus.FAILED,
                    "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                    "latency_ms": 300,
                    "initiated_at": now - timedelta(seconds=60),
                    "completed_at": now - timedelta(seconds=59),
                },
                {
                    "id": att2_id,
                    "payment_id": payment_id,
                    "attempt_number": 2,
                    "route_id": route["id"],
                    "payment_method": payment_method,
                    "status": AttemptStatus.FAILED,
                    "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                    "latency_ms": 350,
                    "initiated_at": now,
                    "completed_at": now + timedelta(milliseconds=350),
                },
            ]
            failures = [
                {
                    "id": uuid.UUID(int=self.rng.getrandbits(128)),
                    "attempt_id": att1_id,
                    "payment_id": payment_id,
                    "failure_category": FailureCategory.TRANSIENT,
                    "error_code": "GATEWAY_TIMEOUT",
                    "reason": "Gateway timed out waiting for upstream bank response (504)",
                    "error_message": "Gateway timed out waiting for upstream bank response (504)",
                    "recoverable": True,
                    "is_recoverable": True,
                    "suggested_backoff_sec": 45,
                    "detected_at": now - timedelta(seconds=59),
                },
                {
                    "id": uuid.UUID(int=self.rng.getrandbits(128)),
                    "attempt_id": att2_id,
                    "payment_id": payment_id,
                    "failure_category": FailureCategory.ROUTE_DEGRADATION,
                    "error_code": "BANK_DOWNTIME",
                    "reason": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
                    "error_message": "Acquiring bank switch reports scheduled maintenance or unplanned outage",
                    "recoverable": True,
                    "is_recoverable": True,
                    "suggested_backoff_sec": 180,
                    "detected_at": now + timedelta(milliseconds=350),
                },
            ]
            lifecycle_state = PaymentLifecycleState.ESCALATED
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.MANUAL_REVIEW,
                "recommended_strategy": RecoveryStrategy.MANUAL_REVIEW,
                "attempt_count": 1,
                "retry_count": 1,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now - timedelta(seconds=60),
                "completed_at": now,
                "resolved_at": now,
                "stop_reason": "HIGH_VALUE_THRESHOLD_EXCEEDED",
                "estimated_recovery_rate": 0.40,
                "recovered_amount_inr": Decimal("0.00"),
            }

        elif scenario_name == "fraud-stop":
            merchant = self.merchants[0]
            customer = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "external_id": "cust_risk_high_999",
                "email_masked": "suspect***@example.com",
                "phone_masked": "+91-99****9999",
                "historical_success_rate": 0.50,
                "total_transactions": 8,
                "risk_score": 0.88,
            }
            route = next(r for r in self.routes if r["id"] == "ROUTE_AXIS_CARDS")
            payment_method = PaymentMethod.CREDIT_CARD
            amount = Decimal("35000.00")
            attempt_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [{
                "id": attempt_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": 110,
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=110),
            }]
            failures = [{
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "attempt_id": attempt_id,
                "payment_id": payment_id,
                "failure_category": FailureCategory.FRAUD,
                "error_code": "FRAUD_SUSPECTED",
                "reason": "Flagged by risk engine due to anomalous IP, velocity, or blacklisted card",
                "error_message": "Flagged by risk engine due to anomalous IP, velocity, or blacklisted card",
                "recoverable": False,
                "is_recoverable": False,
                "suggested_backoff_sec": 0,
                "detected_at": now + timedelta(milliseconds=110),
            }]
            lifecycle_state = PaymentLifecycleState.STOPPED
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.TERMINAL_ABANDON,
                "recommended_strategy": RecoveryStrategy.TERMINAL_ABANDON,
                "attempt_count": 0,
                "retry_count": 0,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": now,
                "resolved_at": now,
                "stop_reason": "FRAUD_DETECTED",
                "estimated_recovery_rate": 0.0,
                "recovered_amount_inr": Decimal("0.00"),
            }

        elif scenario_name == "max-retries":
            merchant = self.merchants[4]  # Aura (max_auto_retries = 1)
            customer = self.customers[0]
            route = next(r for r in self.routes if r["id"] == "ROUTE_HDFC_UPI")
            payment_method = PaymentMethod.UPI
            amount = Decimal("2500.00")
            att1_id = uuid.UUID(int=self.rng.getrandbits(128))
            attempts = [{
                "id": att1_id,
                "payment_id": payment_id,
                "attempt_number": 1,
                "route_id": route["id"],
                "payment_method": payment_method,
                "status": AttemptStatus.FAILED,
                "gateway_ref_id": f"pay_gw_{self.rng.getrandbits(32):x}",
                "latency_ms": 280,
                "initiated_at": now,
                "completed_at": now + timedelta(milliseconds=280),
            }]
            failures = [{
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "attempt_id": att1_id,
                "payment_id": payment_id,
                "failure_category": FailureCategory.TRANSIENT,
                "error_code": "GATEWAY_TIMEOUT",
                "reason": "Gateway timed out waiting for upstream bank response (504)",
                "error_message": "Gateway timed out waiting for upstream bank response (504)",
                "recoverable": True,
                "is_recoverable": True,
                "suggested_backoff_sec": 45,
                "detected_at": now + timedelta(milliseconds=280),
            }]
            lifecycle_state = PaymentLifecycleState.STOPPED
            recovery_case = {
                "id": uuid.UUID(int=self.rng.getrandbits(128)),
                "payment_id": payment_id,
                "status": lifecycle_state,
                "strategy": RecoveryStrategy.TERMINAL_ABANDON,
                "recommended_strategy": RecoveryStrategy.TERMINAL_ABANDON,
                "attempt_count": 1,
                "retry_count": 1,
                "max_attempts": merchant["max_auto_retries"],
                "max_retries": merchant["max_auto_retries"],
                "started_at": now,
                "completed_at": now,
                "resolved_at": now,
                "stop_reason": "MAX_RETRIES_EXCEEDED",
                "estimated_recovery_rate": 0.0,
                "recovered_amount_inr": Decimal("0.00"),
            }
        else:
            raise ValueError(f"Unknown scenario preset: '{scenario_name}'. Supported: 'healthy-transient', 'degraded-route', 'customer-action', 'repeated-failure', 'fraud-stop', 'max-retries'.")

        payment_data = {
            "id": payment_id,
            "merchant_id": merchant["id"],
            "customer_id": customer["id"],
            "amount_inr": amount,
            "currency": "INR",
            "payment_method": payment_method,
            "preferred_route_id": route["id"],
            "status": lifecycle_state,
            "final_error_code": failures[-1]["error_code"],
            "idempotency_key": f"pay_syn_scen_{payment_id.hex[:14]}",
            "metadata_json": {
                "scenario": scenario_name,
                "synthetic_index": index,
                "source": "IRO_SCENARIO_PRESET",
            },
            "created_at": now,
            "updated_at": now,
        }

        return SyntheticRecord(
            merchant_data=merchant,
            customer_data=customer,
            route_data=route,
            payment_data=payment_data,
            attempts_data=attempts,
            failures_data=failures,
            recovery_case_data=recovery_case,
        )
