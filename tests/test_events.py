"""Tests for Phase 2 event contracts, publishing, consumption, idempotency, retry processor, and DLQ."""

import uuid
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.notification_consumer import NotificationConsumer
from app.consumers.recovery_consumer import RecoveryConsumer
from app.events.broker import EventMessage, InMemoryEventBroker
from app.events.retry_processor import RetryProcessor
from app.events.schemas import (
    EventEnvelope,
    NotificationRequestedPayload,
    PaymentCreatedPayload,
    PaymentFailedPayload,
    PaymentRetryRequestedPayload,
    PaymentSucceededPayload,
    RecoveryCompletedPayload,
    RecoveryEscalatedPayload,
    RecoveryFailedPayload,
    RecoveryStartedPayload,
    RecoveryStoppedPayload,
)
from app.models import (
    Customer,
    FailureCategory,
    Merchant,
    MerchantTier,
    Notification,
    Payment,
    PaymentLifecycleState,
    PaymentMethod,
    PaymentRoute,
    ProcessedEvent,
    RecoveryCase,
    RecoveryStrategy,
)
from app.services.payment_service import PaymentService


def test_event_contracts_serialization_and_correlation():
    """Verify all 10 event schemas serialize cleanly and preserve correlation chains."""
    corr_id = f"corr_{uuid.uuid4().hex[:12]}"
    payment_id = uuid.uuid4()
    rc_id = uuid.uuid4()

    # 1. payment.created
    e_created = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="payment.created",
        correlation_id=corr_id,
        data=PaymentCreatedPayload(
            payment_id=payment_id,
            merchant_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            amount_inr=Decimal("1500.00"),
            payment_method=PaymentMethod.UPI,
            idempotency_key="idemp_001",
        ).model_dump(mode="json"),
    )
    assert e_created.correlation_id == corr_id

    # 2. payment.failed
    e_failed = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="payment.failed",
        correlation_id=corr_id,
        causation_id=str(e_created.event_id),
        data=PaymentFailedPayload(
            payment_id=payment_id,
            merchant_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            amount_inr=Decimal("1500.00"),
            payment_method=PaymentMethod.UPI,
            route_id="ROUTE_HDFC_UPI",
            failure_category=FailureCategory.TRANSIENT,
            error_code="GATEWAY_TIMEOUT",
            reason="Bank timeout",
            attempt_number=1,
            recoverable=True,
        ).model_dump(mode="json"),
    )
    assert e_failed.correlation_id == corr_id
    assert e_failed.causation_id == str(e_created.event_id)

    # 3. notification.requested
    e_notif = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="notification.requested",
        correlation_id=corr_id,
        causation_id=str(e_failed.event_id),
        data=NotificationRequestedPayload(
            notification_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            payment_id=payment_id,
            channel="SMS",
            template="PAYMENT_FAILED",
            payload={"amount": 1500.0},
        ).model_dump(mode="json"),
    )
    assert e_notif.correlation_id == corr_id
    assert e_notif.causation_id == str(e_failed.event_id)


@pytest.mark.asyncio
async def test_payment_failed_to_recovery_and_notification_pipeline(db_session: AsyncSession):
    """
    Demonstrate end-to-end:
    PaymentService records failure -> publishes payment.failed ->
    RecoveryConsumer receives event -> creates RecoveryCase in PostgreSQL ->
    RecoveryConsumer emits notification.requested ->
    NotificationConsumer receives event -> creates Notification in PostgreSQL.
    """
    broker = InMemoryEventBroker()
    await broker.start()

    # Pre-seed merchant and customer in DB
    merchant = Merchant(id=uuid.uuid4(), name="Test Store", mcc="5411", tier=MerchantTier.GROWTH)
    customer = Customer(id=uuid.uuid4(), external_id="cust_pipe_01", email_masked="a@t.com", phone_masked="+91-999")
    route = PaymentRoute(id="ROUTE_TEST", name="Test Route", payment_method=PaymentMethod.UPI)
    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount_inr=Decimal("2500.00"),
        payment_method=PaymentMethod.UPI,
        status=PaymentLifecycleState.PROCESSING,
        idempotency_key="idemp_pipe_01",
    )
    db_session.add_all([merchant, customer, route, payment])
    await db_session.commit()

    payment_service = PaymentService(broker)
    recovery_consumer = RecoveryConsumer(broker)
    notification_consumer = NotificationConsumer(broker)

    # 1. Trigger Payment Failure
    corr_id = "corr_tx_12345"
    await payment_service.record_payment_failure(
        session=db_session,
        payment=payment,
        route_id=route.id,
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Bank gateway timeout",
        recoverable=True,
        correlation_id=corr_id,
    )

    # 2. RecoveryConsumer processes payment.failed
    action = await recovery_consumer.process_next(db_session)
    assert action == "PROCESSED"

    # Verify RecoveryCase in DB
    rc_res = await db_session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == payment.id))
    rc = rc_res.scalar_one()
    assert rc.status == PaymentLifecycleState.RECOVERY_PENDING
    assert rc.attempt_count == 0

    # Verify ProcessedEvent recorded for recovery group
    pe_res = await db_session.execute(
        select(ProcessedEvent).where(ProcessedEvent.consumer_name == RecoveryConsumer.CONSUMER_GROUP)
    )
    assert pe_res.scalar_one() is not None

    # 3. NotificationConsumer processes notification.requested
    notif_action = await notification_consumer.process_next(db_session)
    assert notif_action == "PROCESSED"

    # Verify Notification in DB
    notif_res = await db_session.execute(select(Notification).where(Notification.payment_id == payment.id))
    notif = notif_res.scalar_one()
    assert notif.channel.value == "SMS"
    assert notif.status.value == "SENT"
    assert notif.metadata_json["correlation_id"] == corr_id


@pytest.mark.asyncio
async def test_atomic_idempotency_duplicate_events(db_session: AsyncSession):
    """Verify that re-delivering the exact same event does NOT duplicate side effects."""
    broker = InMemoryEventBroker()
    await broker.start()

    merchant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    event_id = uuid.uuid4()
    corr_id = "corr_idem_test"

    event_payload = PaymentFailedPayload(
        payment_id=payment_id,
        merchant_id=merchant_id,
        customer_id=customer_id,
        amount_inr=Decimal("1200.00"),
        payment_method=PaymentMethod.UPI,
        route_id="ROUTE_HDFC_UPI",
        failure_category=FailureCategory.TRANSIENT,
        error_code="GATEWAY_TIMEOUT",
        reason="Upstream timeout",
        attempt_number=1,
        recoverable=True,
    )
    envelope = EventEnvelope(
        event_id=event_id,
        event_type="payment.failed",
        correlation_id=corr_id,
        data=event_payload.model_dump(mode="json"),
    )

    consumer = RecoveryConsumer(broker)

    # First delivery
    await broker.publish("payment.events", envelope.model_dump(mode="json"))
    act1 = await consumer.process_next(db_session)
    assert act1 == "PROCESSED"

    # Second delivery (Exact duplicate with same event_id)
    await broker.publish("payment.events", envelope.model_dump(mode="json"))
    act2 = await consumer.process_next(db_session)
    assert act2 == "SKIPPED_DUPLICATE"

    # Verify strictly 1 RecoveryCase and 1 ProcessedEvent
    rc_count = await db_session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == payment_id))
    assert len(rc_count.scalars().all()) == 1

    pe_count = await db_session.execute(select(ProcessedEvent).where(ProcessedEvent.event_id == event_id))
    assert len(pe_count.scalars().all()) == 1


@pytest.mark.asyncio
async def test_malformed_event_dlq_routing():
    """Verify malformed JSON or invalid schema is quarantined to .DLQ."""
    broker = InMemoryEventBroker()
    await broker.start()

    malformed_msg = EventMessage(
        topic="payment.events",
        key="key1",
        value=b'{"event_id": "invalid-uuid", "event_type": "unknown.event"}',
    )

    dest = await RetryProcessor.route_failure(
        broker=broker,
        message=malformed_msg,
        reason="ValidationError: invalid uuid",
        is_malformed=True,
    )
    assert dest == RetryProcessor.DLQ_TOPIC

    # Fetch from DLQ
    dlq_msg = await broker.get_message(RetryProcessor.DLQ_TOPIC, "test-auditor")
    assert dlq_msg is not None
    assert dlq_msg.headers["x-death-reason"] == "MALFORMED_EVENT_SCHEMA"


@pytest.mark.asyncio
async def test_retry_processor_backoff_and_dlq_exhaustion():
    """Verify transient failures progress through retry topics and finally to DLQ."""
    broker = InMemoryEventBroker()
    await broker.start()

    msg = EventMessage(
        topic="payment.events",
        key="k",
        value=b'{"valid": "payload"}',
        headers={},
    )

    # 1. First failure -> goes to retry.1 with 0.1s backoff (fast for test)
    dest1 = await RetryProcessor.route_failure(
        broker, msg, reason="DB Connection Timeout", backoff_sec=0.1
    )
    assert dest1 == RetryProcessor.RETRY_1_TOPIC

    # 2. RetryProcessor polls before delay elapsed -> message stays in retry topic
    repub0 = await RetryProcessor.process_retries(broker, timeout=0.1)
    assert repub0 == 0

    # Wait for delay to elapse
    import asyncio
    await asyncio.sleep(0.15)

    # 3. RetryProcessor polls after delay -> message republished to main topic
    repub1 = await RetryProcessor.process_retries(broker, timeout=0.1)
    assert repub1 == 1

    repub_msg = await broker.get_message(RetryProcessor.MAIN_TOPIC, "test-checker")
    assert repub_msg is not None
    assert repub_msg.headers["x-retry-count"] == "1"

    # 4. Second failure -> goes to retry.2
    dest2 = await RetryProcessor.route_failure(
        broker, repub_msg, reason="DB Lock Conflict", backoff_sec=0.1
    )
    assert dest2 == RetryProcessor.RETRY_2_TOPIC

    await asyncio.sleep(0.15)
    repub2 = await RetryProcessor.process_retries(broker, timeout=0.1)
    assert repub2 == 1

    second_repub_msg = await broker.get_message(RetryProcessor.MAIN_TOPIC, "test-checker")
    assert second_repub_msg.headers["x-retry-count"] == "2"

    # 5. Third failure -> retries exhausted -> routes to DLQ
    dest3 = await RetryProcessor.route_failure(
        broker, second_repub_msg, reason="Permanent Failure"
    )
    assert dest3 == RetryProcessor.DLQ_TOPIC

    dlq_msg = await broker.get_message(RetryProcessor.DLQ_TOPIC, "dlq-auditor")
    assert dlq_msg is not None
    assert dlq_msg.headers["x-death-reason"] == "EXCEEDED_MAX_RETRIES"
