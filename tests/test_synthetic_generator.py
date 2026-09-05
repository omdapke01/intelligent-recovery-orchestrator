"""Tests for synthetic data generation, distributions, streaming, and export."""

import json
from pathlib import Path
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, PaymentAttempt, PaymentFailure, RecoveryCase
from app.synthetic import (
    FAILURE_TAXONOMY,
    SyntheticPaymentGenerator,
    bulk_persist_records,
    calculate_dataset_statistics,
    export_to_ndjson,
)


def test_generate_small_batches():
    gen = SyntheticPaymentGenerator(seed=123)

    for size in [10, 100]:
        batch = gen.generate_batch(size)
        assert len(batch) == size

        for rec in batch:
            p = rec.payment_data
            assert p["id"] is not None
            assert p["amount_inr"] > 0
            assert p["status"] is not None
            assert len(rec.attempts_data) >= 1

            if p["status"].value == "SUCCESS":
                assert len(rec.failures_data) == 0
                assert rec.recovery_case_data is None
            else:
                assert len(rec.failures_data) >= 1
                assert rec.recovery_case_data is not None


def test_generator_statistical_distributions():
    # 1,000 payments should produce statistically reliable distributions
    gen = SyntheticPaymentGenerator(seed=42, base_success_rate=0.78)
    batch = gen.generate_batch(1000)
    stats = calculate_dataset_statistics(batch)

    assert stats["total_payments"] == 1000
    assert stats["total_volume_inr"] > 0

    # Initial success rate should hover around 68% - 84% given route health & risk variations
    assert 0.68 <= stats["initial_success_rate"] <= 0.84
    assert stats["failed_payment_count"] == 1000 - stats["initial_success_count"]

    # Both recoverable and non-recoverable failures must be present
    assert stats["recoverable_failure_instances"] > 0
    assert stats["non_recoverable_failure_instances"] > 0

    # Check that multiple failure categories exist
    cats = stats["failure_category_distribution"]
    assert "TRANSIENT" in cats
    assert "ROUTE_DEGRADATION" in cats
    assert "CUSTOMER_ACTION_REQUIRED" in cats
    assert "PERMANENT" in cats
    assert "FRAUD" in cats

    # Check state distribution
    states = stats["state_distribution"]
    assert "SUCCESS" in states
    assert "RECOVERY_PENDING" in states or "ESCALATED" in states or "STOPPED" in states


def test_deterministic_scenarios():
    gen = SyntheticPaymentGenerator(seed=101)

    # 1. Healthy Transient
    rec_transient = gen.generate_scenario("healthy-transient")
    assert rec_transient.payment_data["status"].value == "RECOVERY_PENDING"
    assert rec_transient.failures_data[0]["error_code"] == "GATEWAY_TIMEOUT"
    assert rec_transient.failures_data[0]["failure_category"].value == "TRANSIENT"
    assert rec_transient.failures_data[0]["recoverable"] is True
    assert rec_transient.recovery_case_data["strategy"].value == "DETERMINISTIC_RETRY_BACKOFF"

    # 2. Degraded Route
    rec_degraded = gen.generate_scenario("degraded-route")
    assert rec_degraded.payment_data["status"].value == "RECOVERY_PENDING"
    assert rec_degraded.failures_data[0]["error_code"] == "BANK_DOWNTIME"
    assert rec_degraded.failures_data[0]["failure_category"].value == "ROUTE_DEGRADATION"
    assert rec_degraded.recovery_case_data["strategy"].value == "ROUTE_FAILOVER"

    # 3. Customer Action Required
    rec_cust = gen.generate_scenario("customer-action")
    assert rec_cust.payment_data["status"].value == "RECOVERY_PENDING"
    assert rec_cust.failures_data[0]["error_code"] == "INSUFFICIENT_FUNDS"
    assert rec_cust.failures_data[0]["failure_category"].value == "CUSTOMER_ACTION_REQUIRED"
    assert rec_cust.recovery_case_data["strategy"].value == "NOTIFY_CUSTOMER_LINK"

    # 4. Repeated Failure (High value)
    rec_repeated = gen.generate_scenario("repeated-failure")
    assert rec_repeated.payment_data["status"].value == "ESCALATED"
    assert len(rec_repeated.attempts_data) == 2
    assert rec_repeated.recovery_case_data["strategy"].value == "MANUAL_REVIEW"
    assert rec_repeated.recovery_case_data["stop_reason"] == "HIGH_VALUE_THRESHOLD_EXCEEDED"

    # 5. Fraud Stop
    rec_fraud = gen.generate_scenario("fraud-stop")
    assert rec_fraud.payment_data["status"].value == "STOPPED"
    assert rec_fraud.failures_data[0]["failure_category"].value == "FRAUD"
    assert rec_fraud.failures_data[0]["recoverable"] is False
    assert rec_fraud.recovery_case_data["stop_reason"] == "FRAUD_DETECTED"

    # 6. Max Retries Reached
    rec_max = gen.generate_scenario("max-retries")
    assert rec_max.payment_data["status"].value == "STOPPED"
    assert rec_max.recovery_case_data["stop_reason"] == "MAX_RETRIES_EXCEEDED"


def test_streaming_generator_scalability():
    # Stream 5,000 records without loading all into memory
    gen = SyntheticPaymentGenerator(seed=999)
    count = 0
    for rec in gen.generate_stream(5000):
        count += 1
        if count % 1000 == 0:
            assert rec.payment_data["id"] is not None
    assert count == 5000


def test_export_to_ndjson(tmp_path: Path):
    gen = SyntheticPaymentGenerator(seed=777)
    out_file = tmp_path / "test_payments.jsonl"

    stream = gen.generate_stream(150)
    exported_count = export_to_ndjson(stream, out_file)

    assert exported_count == 150
    assert out_file.exists()

    # Read back and verify each line is valid JSON
    with open(out_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 150
        first_obj = json.loads(lines[0])
        assert "payment" in first_obj
        assert "merchant" in first_obj
        assert "customer" in first_obj
        assert "attempts" in first_obj


@pytest.mark.asyncio
async def test_bulk_persist_records(db_session: AsyncSession):
    gen = SyntheticPaymentGenerator(seed=888)
    records = gen.generate_batch(50)

    persisted_count = await bulk_persist_records(db_session, records, commit_batch_size=20)
    assert persisted_count == 50

    # Verify rows in database
    p_res = await db_session.execute(select(func.count()).select_from(Payment))
    assert p_res.scalar() == 50

    att_res = await db_session.execute(select(func.count()).select_from(PaymentAttempt))
    assert att_res.scalar() >= 50

    fail_res = await db_session.execute(select(func.count()).select_from(PaymentFailure))
    assert fail_res.scalar() >= 0
