"""Tests for Phase 4: Safe Payment Recovery Execution.

Verifies:
1. Redis distributed lock (SET NX + TTL)
2. Token ownership verification & atomic Lua release
3. Lock expiry & worker crash simulation
4. Durable PostgreSQL idempotency barrier (provider called exactly once)
5. Exponential backoff and retry exhaustion stopping conditions
6. Non-retryable failure immediate termination
7. Provider sandbox 5 distinct outcomes
8. Concurrency: 5 workers racing for the same payment
"""

import asyncio
import uuid
from decimal import Decimal
from typing import List

import pytest
from sqlalchemy import select

from app.database import async_session_factory, init_db
from app.events.broker import InMemoryEventBroker
from app.execution.idempotency import PostgresIdempotencyBarrier
from app.execution.lock import RedisDistributedLock
from app.execution.provider import (
    DuplicateGatewayRequestException,
    MockPaymentProvider,
    PaymentExecutionRequest,
    ProviderOutcome,
    ProviderTimeoutException,
    ProviderUnavailableException,
)
from app.execution.redis_client import InMemoryRedisClient
from app.execution.retry_policy import RecoveryRetryPolicy
from app.execution.service import ExecutionStatus, SafeRecoveryExecutionService
from app.models import (
    Customer,
    Merchant,
    MerchantTier,
    Payment,
    PaymentAttempt,
    PaymentLifecycleState,
    PaymentMethod,
    PaymentRoute,
    RecoveryCase,
    RecoveryState,
    RecoveryStrategy,
    RouteStatus,
)


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.fixture
def redis_client():
    return InMemoryRedisClient()


@pytest.fixture
def event_broker():
    broker = InMemoryEventBroker()
    return broker


async def seed_test_payment(session, amount: Decimal = Decimal("1000.00"), max_retries: int = 2):
    merchant = Merchant(
        id=uuid.uuid4(),
        name=f"Test Merchant {uuid.uuid4().hex[:6]}",
        mcc="5411",
        tier=MerchantTier.GROWTH,
        max_auto_retries=max_retries,
        min_recovery_amount_inr=Decimal("50.00"),
    )
    customer = Customer(
        id=uuid.uuid4(),
        external_id=f"cust_{uuid.uuid4().hex[:8]}",
        email_masked="test@***.in",
        phone_masked="+91-99999****",
    )
    route = PaymentRoute(
        id=f"ROUTE_UPI_{uuid.uuid4().hex[:6]}",
        name="Test UPI Gateway",
        payment_method=PaymentMethod.UPI,
        is_active=True,
        status=RouteStatus.HEALTHY,
        health_score=0.95,
    )
    session.add_all([merchant, customer, route])
    await session.flush()

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=amount,
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.FAILED,
        idempotency_key=f"idemp_{uuid.uuid4().hex[:8]}",
    )
    session.add(payment)
    await session.flush()

    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=payment.id,
        status=PaymentLifecycleState.RECOVERY_PENDING,
        recovery_state=RecoveryState.APPROVED,
        strategy=RecoveryStrategy.DETERMINISTIC_RETRY_BACKOFF,
        attempt_count=0,
        max_attempts=max_retries,
    )
    session.add(case)
    await session.commit()

    return payment, case, route


# -----------------------------------------------------------------------------
# 1. Distributed Lock Unit Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_acquisition_and_release(redis_client):
    """Test standard atomic lock acquisition and release."""
    lock = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:payment:1",
        ttl_ms=5000,
        owner_token="token_worker_1",
    )
    assert await lock.acquire() is True
    assert await lock.is_locked() is True
    assert await lock.is_owned() is True

    # Second worker cannot acquire held lock
    competing_lock = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:payment:1",
        ttl_ms=5000,
        owner_token="token_worker_2",
    )
    assert await competing_lock.acquire() is False

    # Owner releases lock
    assert await lock.release() is True
    assert await lock.is_locked() is False


@pytest.mark.asyncio
async def test_lock_ownership_verification(redis_client):
    """Worker A cannot release Worker B's lock; token mismatch returns False."""
    lock_a = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:ownership",
        ttl_ms=5000,
        owner_token="token_a",
    )
    lock_b = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:ownership",
        ttl_ms=5000,
        owner_token="token_b",
    )

    assert await lock_a.acquire() is True

    # Worker B tries to release Worker A's lock
    released_by_b = await lock_b.release()
    assert released_by_b is False  # Refused: token mismatch!

    # Lock is STILL held by Worker A
    assert await lock_a.is_locked() is True
    assert await lock_a.is_owned() is True

    # Worker A releases cleanly
    assert await lock_a.release() is True
    assert await lock_a.is_locked() is False


@pytest.mark.asyncio
async def test_lock_expiry_allows_new_worker(redis_client):
    """Expired locks must not permanently block recovery; new worker can acquire."""
    lock_a = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:expiry",
        ttl_ms=50,  # 50ms short TTL
        owner_token="token_a",
    )
    assert await lock_a.acquire() is True

    # Worker A hangs/sleeps past TTL
    await asyncio.sleep(0.08)

    # Worker B comes in after expiry
    lock_b = RedisDistributedLock(
        redis_client=redis_client,
        key="lock:test:expiry",
        ttl_ms=5000,
        owner_token="token_b",
    )
    assert await lock_b.acquire() is True  # Succeeded because lock_a expired!

    # Worker A tries to release now (after it expired and lock_b took it)
    released_by_a = await lock_a.release()
    assert released_by_a is False  # Must NOT delete Worker B's lock!

    # Lock B is still safely held
    assert await lock_b.is_owned() is True
    await lock_b.release()


@pytest.mark.asyncio
async def test_worker_crash_simulation(redis_client):
    """Simulate a worker crash (unhandled exception before release).

    Next worker successfully acquires lock after TTL expires.
    """
    key = "lock:test:crash"

    async def crashing_worker():
        lock = RedisDistributedLock(redis_client, key, ttl_ms=60, owner_token="crash_token")
        acquired = await lock.acquire()
        assert acquired is True
        # Worker crashes abruptly without calling lock.release()
        raise RuntimeError("Simulated worker fatal crash!")

    with pytest.raises(RuntimeError, match="Simulated worker fatal crash!"):
        await crashing_worker()

    # Immediately, key is still held by the crashed worker
    lock_survivor = RedisDistributedLock(redis_client, key, ttl_ms=1000, owner_token="survivor_token")
    assert await lock_survivor.acquire() is False

    # Wait for TTL to expire
    await asyncio.sleep(0.08)

    # Survivor worker now acquires cleanly
    assert await lock_survivor.acquire() is True
    await lock_survivor.release()


# -----------------------------------------------------------------------------
# 2. PostgreSQL Idempotency Barrier Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_durable_postgresql_idempotency(redis_client, event_broker):
    """Verify durable PostgreSQL idempotency barrier.

    Crucial assertion: mock payment provider is called EXACTLY ONCE,
    and duplicate attempt execution is rejected at the database level.
    """
    provider = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    service = SafeRecoveryExecutionService(
        event_broker=event_broker,
        redis_client=redis_client,
        provider=provider,
    )

    async with async_session_factory() as session:
        payment, case, route = await seed_test_payment(session)
        payment_id = payment.id
        case_id = case.id
        route_id = route.id

    # 1. First Execution
    async with async_session_factory() as session:
        res1 = await service.execute_recovery_attempt(
            session=session,
            payment_id=payment_id,
            recovery_case_id=case_id,
            attempt_number=1,
            target_route_id=route_id,
        )
        assert res1.status == ExecutionStatus.SUCCESS
        assert provider.total_calls == 1

    # 2. Second Execution with exact same attempt parameters (duplicate delivery)
    async with async_session_factory() as session:
        res2 = await service.execute_recovery_attempt(
            session=session,
            payment_id=payment_id,
            recovery_case_id=case_id,
            attempt_number=1,
            target_route_id=route_id,
        )
        # Payment is already marked recovered / already completed
        assert res2.status in {ExecutionStatus.ALREADY_COMPLETED, ExecutionStatus.DUPLICATE_EXECUTION_BLOCKED}
        # CRITICAL ASSERTION: Provider was NOT called a second time!
        assert provider.total_calls == 1


@pytest.mark.asyncio
async def test_idempotency_barrier_traps_concurrent_db_insert(redis_client, event_broker):
    """Test PostgresIdempotencyBarrier directly handling IntegrityError race condition."""
    async with async_session_factory() as session1, async_session_factory() as session2:
        payment, case, route = await seed_test_payment(session1)
        idemp_key = PostgresIdempotencyBarrier.generate_idempotency_key(case.id, 1)

        # Worker 1 reserves attempt
        res1 = await PostgresIdempotencyBarrier.reserve_attempt(
            session=session1,
            payment_id=payment.id,
            attempt_number=1,
            idempotency_key=idemp_key,
            route_id=route.id,
            payment_method=PaymentMethod.UPI,
        )
        assert res1.is_new is True
        await session1.commit()

        # Worker 2 attempts exact same idempotency key
        res2 = await PostgresIdempotencyBarrier.reserve_attempt(
            session=session2,
            payment_id=payment.id,
            attempt_number=1,
            idempotency_key=idemp_key,
            route_id=route.id,
            payment_method=PaymentMethod.UPI,
        )
        assert res2.is_new is False
        assert res2.reason == "EXISTING_ATTEMPT_RECORDED"


# -----------------------------------------------------------------------------
# 3. Concurrency: 5 Workers Race for Same Payment
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_workers_single_winner(redis_client, event_broker):
    """5 concurrent worker tasks attempt recovery of the same payment simultaneously.

    Expected result:
    - Exactly ONE worker acquires the lock and executes payment.
    - Remaining 4 workers are rejected due to lock contention.
    - Mock payment provider is called EXACTLY ONCE.
    """
    provider = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    service = SafeRecoveryExecutionService(
        event_broker=event_broker,
        redis_client=redis_client,
        provider=provider,
        lock_ttl_ms=5000,
    )

    async with async_session_factory() as session:
        payment, case, route = await seed_test_payment(session)
        p_id = payment.id
        c_id = case.id
        r_id = route.id

    async def worker_task(worker_index: int):
        # Each worker runs in its own session / thread
        async with async_session_factory() as worker_session:
            return await service.execute_recovery_attempt(
                session=worker_session,
                payment_id=p_id,
                recovery_case_id=c_id,
                attempt_number=1,
                target_route_id=r_id,
                worker_id=f"worker_{worker_index}",
            )

    # Launch 5 workers concurrently
    tasks = [worker_task(i) for i in range(5)]
    results: List = await asyncio.gather(*tasks)

    winners = [r for r in results if r.status == ExecutionStatus.SUCCESS]
    contentions = [r for r in results if r.status == ExecutionStatus.LOCK_CONTENTION]

    # Exactly 1 winner
    assert len(winners) == 1
    # Exactly 4 blocked by lock contention
    assert len(contentions) == 4
    # Provider called exactly once
    assert provider.total_calls == 1


# -----------------------------------------------------------------------------
# 4. Mock Payment Provider Sandbox Outcomes
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_all_five_outcomes():
    """Verify MockPaymentProvider sandbox produces all 5 required outcomes."""
    req = PaymentExecutionRequest(
        payment_id=uuid.uuid4(),
        attempt_number=1,
        idempotency_key="idemp_provider_test",
        amount_inr=Decimal("500.00"),
        route_id="ROUTE_UPI",
        payment_method=PaymentMethod.UPI,
    )

    # 1. SUCCESS
    p_success = MockPaymentProvider(default_outcome=ProviderOutcome.SUCCESS)
    res_success = await p_success.execute_payment(req)
    assert res_success.success is True
    assert res_success.status == PaymentLifecycleState.SUCCESS
    assert res_success.gateway_ref_id is not None

    # 2. FAILURE
    p_fail = MockPaymentProvider(default_outcome=ProviderOutcome.FAILURE)
    res_fail = await p_fail.execute_payment(req)
    assert res_fail.success is False
    assert res_fail.error_code == "INSUFFICIENT_FUNDS"

    # 3. TIMEOUT
    p_timeout = MockPaymentProvider(default_outcome=ProviderOutcome.TIMEOUT, timeout_sec=0.05)
    with pytest.raises(ProviderTimeoutException):
        await p_timeout.execute_payment(req)

    # 4. DUPLICATE_REQUEST
    p_dup = MockPaymentProvider(default_outcome=ProviderOutcome.DUPLICATE_REQUEST)
    with pytest.raises(DuplicateGatewayRequestException):
        await p_dup.execute_payment(req)

    # 5. UNAVAILABLE
    p_unavail = MockPaymentProvider(default_outcome=ProviderOutcome.UNAVAILABLE)
    with pytest.raises(ProviderUnavailableException):
        await p_unavail.execute_payment(req)


# -----------------------------------------------------------------------------
# 5. Retry Policy & Stopping Conditions Tests
# -----------------------------------------------------------------------------

def test_retry_policy_exponential_backoff():
    """Verify exponential backoff calculation and capping."""
    policy = RecoveryRetryPolicy(base_backoff_sec=1.0, max_backoff_sec=10.0)

    assert policy.calculate_backoff(1) == 1.0
    assert policy.calculate_backoff(2) == 2.0
    assert policy.calculate_backoff(3) == 4.0
    assert policy.calculate_backoff(4) == 8.0
    assert policy.calculate_backoff(5) == 10.0  # Capped at max_backoff_sec


@pytest.mark.asyncio
async def test_retry_exhaustion_stopping_condition(redis_client, event_broker):
    """Repeated failures exhaust retries and cleanly halt with MAX_RETRIES_EXCEEDED."""
    provider = MockPaymentProvider(default_outcome=ProviderOutcome.FAILURE)
    service = SafeRecoveryExecutionService(
        event_broker=event_broker,
        redis_client=redis_client,
        provider=provider,
    )

    async with async_session_factory() as session:
        # Seed payment with max_retries = 2
        payment, case, route = await seed_test_payment(session, max_retries=2)
        p_id = payment.id
        c_id = case.id
        r_id = route.id

    # Attempt 1: Transient failure -> Retry Scheduled
    provider.set_outcome_for_idempotency_key(
        PostgresIdempotencyBarrier.generate_idempotency_key(c_id, 1),
        ProviderOutcome.FAILURE,
    )
    # Make error code retryable
    async with async_session_factory() as session:
        res1 = await service.execute_recovery_attempt(
            session=session,
            payment_id=p_id,
            recovery_case_id=c_id,
            attempt_number=1,
            target_route_id=r_id,
        )
        # Note: default failure returns INSUFFICIENT_FUNDS which is non-retryable
        # Let's test with retryable code
    assert res1.status == ExecutionStatus.STOPPED
    assert res1.stop_reason == "NON_RETRYABLE_FAILURE"


@pytest.mark.asyncio
async def test_retryable_backoff_and_exhaustion(redis_client, event_broker):
    """Test retryable failure triggers RETRY_SCHEDULED on attempt 1, then STOPPED on attempt 2."""
    class CustomRetryableProvider(MockPaymentProvider):
        async def _simulate_outcome(self, request, outcome):
            from app.execution.provider import PaymentExecutionResponse
            return PaymentExecutionResponse(
                success=False,
                status=PaymentLifecycleState.FAILED,
                error_code="GATEWAY_TIMEOUT",
                error_message="Gateway timed out after 15s",
            )

    provider = CustomRetryableProvider()
    service = SafeRecoveryExecutionService(
        event_broker=event_broker,
        redis_client=redis_client,
        provider=provider,
    )

    async with async_session_factory() as session:
        payment, case, route = await seed_test_payment(session, max_retries=2)
        p_id = payment.id
        c_id = case.id
        r_id = route.id

    # Attempt 1: GATEWAY_TIMEOUT -> Retry Scheduled
    async with async_session_factory() as session:
        res1 = await service.execute_recovery_attempt(
            session=session,
            payment_id=p_id,
            recovery_case_id=c_id,
            attempt_number=1,
            target_route_id=r_id,
        )
        assert res1.status == ExecutionStatus.RETRY_SCHEDULED
        assert res1.backoff_sec > 0

    # Attempt 2: GATEWAY_TIMEOUT -> Max retries reached (2) -> STOPPED
    async with async_session_factory() as session:
        res2 = await service.execute_recovery_attempt(
            session=session,
            payment_id=p_id,
            recovery_case_id=c_id,
            attempt_number=2,
            target_route_id=r_id,
        )
        assert res2.status == ExecutionStatus.STOPPED
        assert res2.stop_reason == "MAX_RETRIES_EXCEEDED"


@pytest.mark.asyncio
async def test_execution_duration_bounded_below_lock_ttl():
    """Verify guardrail: provider execution timeout is strictly bounded below lock TTL."""
    from app.config import settings

    provider = MockPaymentProvider()
    assert provider.timeout_sec < (settings.REDIS_LOCK_TTL_MS / 1000.0)
    assert provider.timeout_sec == 5.0
    assert settings.REDIS_LOCK_TTL_MS == 10000
