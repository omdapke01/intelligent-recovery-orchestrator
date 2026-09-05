"""Tests for domain model persistence, constraints, and relationships."""

import uuid
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttemptStatus,
    Customer,
    FailureCategory,
    Merchant,
    MerchantTier,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentLifecycleState,
    PaymentMethod,
    PaymentRoute,
    RecoveryCase,
    RecoveryStrategy,
    RouteStatus,
)


@pytest.mark.asyncio
async def test_create_merchant_and_customer(db_session: AsyncSession):
    merchant = Merchant(
        name="Test Merchant",
        mcc="5411",
        tier=MerchantTier.GROWTH,
        recovery_enabled=True,
        max_auto_retries=3,
        max_recovery_amount_inr=Decimal("50000.00"),
        auto_escalate_threshold_inr=Decimal("20000.00"),
    )
    customer = Customer(
        external_id="cust_test_001",
        email_masked="t***@example.com",
        phone_masked="+91-98****1234",
        historical_success_rate=0.95,
        total_transactions=15,
        risk_score=0.04,
    )
    db_session.add_all([merchant, customer])
    await db_session.commit()

    assert merchant.id is not None
    assert customer.id is not None

    # Query back
    res = await db_session.execute(select(Merchant).where(Merchant.id == merchant.id))
    fetched_merchant = res.scalar_one()
    assert fetched_merchant.name == "Test Merchant"
    assert fetched_merchant.max_auto_retries == 3


@pytest.mark.asyncio
async def test_payment_route_and_payment_creation(db_session: AsyncSession):
    merchant = Merchant(name="Route Test Merchant", mcc="5812", tier=MerchantTier.STARTUP)
    customer = Customer(external_id="cust_test_002", email_masked="c@test.com", phone_masked="+91-999")
    route = PaymentRoute(
        id="ROUTE_TEST_UPI",
        name="Test UPI Route",
        payment_method=PaymentMethod.UPI,
        provider="TEST_GW",
        health_score=0.99,
        avg_latency_ms=100.0,
        status=RouteStatus.HEALTHY,
    )
    db_session.add_all([merchant, customer, route])
    await db_session.commit()

    payment = Payment(
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("1250.00"),
        payment_method=PaymentMethod.UPI,
        preferred_route_id=route.id,
        status=PaymentLifecycleState.CREATED,
        idempotency_key="idemp_key_001",
    )
    db_session.add(payment)
    await db_session.commit()

    assert payment.id is not None
    assert payment.amount_inr == Decimal("1250.00")
    assert payment.preferred_route_id == "ROUTE_TEST_UPI"


@pytest.mark.asyncio
async def test_payment_attempt_and_failure_cascade(db_session: AsyncSession):
    merchant = Merchant(name="Cascade Merchant", mcc="5812")
    customer = Customer(external_id="cust_test_003", email_masked="c@test.com", phone_masked="+91-999")
    route = PaymentRoute(
        id="ROUTE_TEST_CARDS",
        name="Test Card Route",
        payment_method=PaymentMethod.CREDIT_CARD,
        provider="TEST_CARD_GW",
    )
    db_session.add_all([merchant, customer, route])
    await db_session.commit()

    payment = Payment(
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("5000.00"),
        payment_method=PaymentMethod.CREDIT_CARD,
        preferred_route_id=route.id,
        status=PaymentLifecycleState.FAILED,
        idempotency_key="idemp_cascade_01",
    )
    db_session.add(payment)
    await db_session.commit()

    attempt = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        route_id=route.id,
        payment_method=PaymentMethod.CREDIT_CARD,
        status=AttemptStatus.FAILED,
        latency_ms=350,
    )
    db_session.add(attempt)
    await db_session.commit()

    failure = PaymentFailure(
        attempt_id=attempt.id,
        payment_id=payment.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Upstream bank did not respond",
        recoverable=True,
        suggested_backoff_sec=30,
    )
    recovery_case = RecoveryCase(
        payment_id=payment.id,
        status=PaymentLifecycleState.RECOVERY_PENDING,
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        attempt_count=0,
        max_attempts=2,
        estimated_recovery_rate=0.85,
    )
    db_session.add_all([failure, recovery_case])
    await db_session.commit()

    # Verify relationships
    res = await db_session.execute(select(Payment).where(Payment.id == payment.id))
    loaded_payment = res.scalar_one()
    assert len(loaded_payment.attempts) == 1
    assert len(loaded_payment.failures) == 1
    assert loaded_payment.recovery_case is not None
    assert loaded_payment.recovery_case.strategy == RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF
    assert loaded_payment.recovery_case.attempt_count == 0
    assert loaded_payment.failures[0].recoverable is True
    assert loaded_payment.failures[0].reason == "Upstream bank did not respond"


@pytest.mark.asyncio
async def test_unique_attempt_number_constraint(db_session: AsyncSession):
    merchant = Merchant(name="Unique Merchant", mcc="5812")
    customer = Customer(external_id="cust_unique_01", email_masked="u@test.com", phone_masked="+91-111")
    route = PaymentRoute(id="ROUTE_UQ", name="UQ Route", payment_method=PaymentMethod.UPI)
    db_session.add_all([merchant, customer, route])
    await db_session.commit()

    payment = Payment(
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.PROCESSING,
        idempotency_key="idemp_unique_attempt",
    )
    db_session.add(payment)
    await db_session.commit()

    # Attempt 1
    att1 = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        route_id=route.id,
        payment_method=PaymentMethod.UPI,
        status=AttemptStatus.FAILED,
    )
    db_session.add(att1)
    await db_session.commit()

    # Attempt 1 duplicate should fail
    att1_dup = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        route_id=route.id,
        payment_method=PaymentMethod.UPI,
        status=AttemptStatus.FAILED,
    )
    db_session.add(att1_dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()
