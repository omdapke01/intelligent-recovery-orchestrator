"""Dataset exporters and statistical summary calculators."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.enums import RecoveryStrategy
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.payment_attempt import PaymentAttempt
from app.models.payment_failure import PaymentFailure
from app.models.payment_route import PaymentRoute
from app.models.recovery_case import RecoveryCase
from app.synthetic.generator import SyntheticRecord


def export_to_ndjson(records: Iterable[SyntheticRecord], output_path: str | Path) -> int:
    """Stream records into line-delimited JSON file with O(1) memory overhead."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            line = json.dumps(rec.to_dict())
            f.write(line + "\n")
            count += 1
    return count


def calculate_dataset_statistics(records: Iterable[SyntheticRecord]) -> Dict[str, Any]:
    """Calculate aggregate distributions and metrics for a synthetic payment batch."""
    total = 0
    total_volume_inr = Decimal("0.00")
    states = Counter()
    methods = Counter()
    failure_categories = Counter()
    error_codes = Counter()
    recoverable_count = 0
    non_recoverable_count = 0
    attempts_count = Counter()

    for rec in records:
        total += 1
        p = rec.payment_data
        total_volume_inr += p["amount_inr"]
        states[p["status"].value] += 1
        methods[p["payment_method"].value] += 1
        attempts_count[len(rec.attempts_data)] += 1

        for fail in rec.failures_data:
            failure_categories[fail["failure_category"].value] += 1
            error_codes[fail["error_code"]] += 1
            if fail["is_recoverable"]:
                recoverable_count += 1
            else:
                non_recoverable_count += 1

    success_count = states.get("SUCCESS", 0)
    failed_count = total - success_count

    return {
        "total_payments": total,
        "total_volume_inr": float(total_volume_inr),
        "initial_success_count": success_count,
        "initial_success_rate": round(success_count / total, 4) if total else 0.0,
        "failed_payment_count": failed_count,
        "failure_rate": round(failed_count / total, 4) if total else 0.0,
        "recoverable_failure_instances": recoverable_count,
        "non_recoverable_failure_instances": non_recoverable_count,
        "state_distribution": dict(states),
        "method_distribution": dict(methods),
        "failure_category_distribution": dict(failure_categories),
        "error_code_distribution": dict(error_codes),
        "attempts_per_payment_distribution": dict(attempts_count),
    }


async def bulk_persist_records(
    session: AsyncSession,
    records: Iterable[SyntheticRecord],
    commit_batch_size: int = 500,
) -> int:
    """
    Persist generated synthetic records into database in atomic chunks.
    Ensures merchants, customers, and routes are deduplicated.
    """
    persisted_merchants = set()
    persisted_customers = set()
    persisted_routes = set()

    batch_count = 0
    total_persisted = 0

    for rec in records:
        # 1. Merchant
        m_data = rec.merchant_data
        if m_data["id"] not in persisted_merchants:
            merchant = Merchant(
                id=m_data["id"],
                name=m_data["name"],
                mcc=m_data["mcc"],
                tier=m_data["tier"],
                recovery_enabled=m_data["recovery_enabled"],
                max_auto_retries=m_data["max_auto_retries"],
                max_recovery_amount_inr=m_data["max_recovery_amount_inr"],
                auto_escalate_threshold_inr=m_data["auto_escalate_threshold_inr"],
            )
            await session.merge(merchant)
            persisted_merchants.add(m_data["id"])

        # 2. Customer
        c_data = rec.customer_data
        if c_data["id"] not in persisted_customers:
            customer = Customer(
                id=c_data["id"],
                external_id=c_data["external_id"],
                email_masked=c_data["email_masked"],
                phone_masked=c_data["phone_masked"],
                historical_success_rate=c_data["historical_success_rate"],
                total_transactions=c_data["total_transactions"],
                risk_score=c_data["risk_score"],
            )
            await session.merge(customer)
            persisted_customers.add(c_data["id"])

        # 3. Route
        r_data = rec.route_data
        if r_data["id"] not in persisted_routes:
            route = PaymentRoute(
                id=r_data["id"],
                name=r_data["name"],
                payment_method=r_data["payment_method"],
                provider=r_data["provider"],
                health_score=r_data["health_score"],
                avg_latency_ms=r_data["avg_latency_ms"],
                is_active=r_data.get("is_active", True),
                status=r_data["status"],
            )
            await session.merge(route)
            persisted_routes.add(r_data["id"])

        # 4. Payment
        p_data = rec.payment_data
        payment = Payment(
            id=p_data["id"],
            merchant_id=p_data["merchant_id"],
            customer_id=p_data["customer_id"],
            amount_inr=p_data["amount_inr"],
            currency=p_data["currency"],
            payment_method=p_data["payment_method"],
            preferred_route_id=p_data["preferred_route_id"],
            status=p_data["status"],
            final_error_code=p_data["final_error_code"],
            idempotency_key=p_data["idempotency_key"],
            metadata_json=p_data["metadata_json"],
            created_at=p_data["created_at"],
            updated_at=p_data["updated_at"],
        )
        session.add(payment)

        # 5. Attempts & Failures
        for att_data in rec.attempts_data:
            attempt = PaymentAttempt(
                id=att_data["id"],
                payment_id=att_data["payment_id"],
                attempt_number=att_data["attempt_number"],
                route_id=att_data["route_id"],
                payment_method=att_data["payment_method"],
                status=att_data["status"],
                gateway_ref_id=att_data.get("gateway_ref_id"),
                latency_ms=att_data.get("latency_ms"),
                initiated_at=att_data["initiated_at"],
                completed_at=att_data.get("completed_at"),
            )
            session.add(attempt)

        for fail_data in rec.failures_data:
            failure = PaymentFailure(
                id=fail_data["id"],
                attempt_id=fail_data["attempt_id"],
                payment_id=fail_data["payment_id"],
                failure_category=fail_data["failure_category"],
                error_code=fail_data["error_code"],
                reason=fail_data.get("reason", fail_data.get("error_message", "")),
                recoverable=fail_data.get("recoverable", fail_data.get("is_recoverable", False)),
                suggested_backoff_sec=fail_data.get("suggested_backoff_sec", 0),
                detected_at=fail_data["detected_at"],
            )
            session.add(failure)

        # 6. Recovery Case
        if rec.recovery_case_data:
            rc_data = rec.recovery_case_data
            rc = RecoveryCase(
                id=rc_data["id"],
                payment_id=rc_data["payment_id"],
                status=rc_data["status"],
                strategy=rc_data.get("strategy", rc_data.get("recommended_strategy", RecoveryStrategy.NONE)),
                attempt_count=rc_data.get("attempt_count", rc_data.get("retry_count", 0)),
                max_attempts=rc_data.get("max_attempts", rc_data.get("max_retries", 2)),
                started_at=rc_data.get("started_at"),
                completed_at=rc_data.get("completed_at", rc_data.get("resolved_at")),
                stop_reason=rc_data.get("stop_reason"),
                estimated_recovery_rate=rc_data.get("estimated_recovery_rate"),
                recovered_amount_inr=rc_data["recovered_amount_inr"],
            )
            session.add(rc)

        batch_count += 1
        total_persisted += 1

        if batch_count >= commit_batch_size:
            await session.commit()
            batch_count = 0

    if batch_count > 0:
        await session.commit()

    return total_persisted
