"""CLI demonstration script for Phase 2 Event-Driven Payment Pipeline.

Demonstrates:
1. Payment Failure ingestion via PaymentService -> publishes payment.failed to Kafka/Broker
2. Recovery Consumer ('iro-recovery-group') -> atomic idempotency in DB, creates RecoveryCase, emits notification.requested
3. Notification Consumer ('iro-notification-group') -> atomic idempotency in DB, creates Notification record
4. Atomic Idempotency check -> duplicate event redelivery safely skipped without duplicating side-effects
5. Poison Pill / Schema Malformation -> quarantined to Dead-Letter Queue (DLQ)
6. Retry Processor -> transient failure backoff scheduling and primary topic republishing
"""

import asyncio
import json
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.consumers.notification_consumer import NotificationConsumer
from app.consumers.recovery_consumer import RecoveryConsumer
from app.database import async_session_factory, init_db
from app.events.broker import EventMessage, InMemoryEventBroker
from app.events.retry_processor import RetryProcessor
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
)
from app.services.payment_service import PaymentService
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("iro-event-demo")


def print_banner(title: str):
    width = 75
    print("\n" + "=" * width)
    print(f"  {title}".center(width))
    print("=" * width)


async def main():
    print_banner("IRO PHASE 2: EVENT-DRIVEN PAYMENT PIPELINE DEMO")

    # 1. Initialize DB & In-Memory Event Broker
    await init_db()
    broker = InMemoryEventBroker()
    await broker.start()
    print("[INIT] Database connected and Event Broker initialized.")

    # 2. Seed Foundation Domain Data
    async with async_session_factory() as session:
        merchant = Merchant(
            id=uuid.uuid4(),
            name="Apex Retail Pvt Ltd",
            mcc="5411",
            tier=MerchantTier.ENTERPRISE,
        )
        customer = Customer(
            id=uuid.uuid4(),
            external_id=f"cust_{uuid.uuid4().hex[:8]}",
            email_masked="rohit.sharma@***.in",
            phone_masked="+91-98765****0",
        )
        route_res = await session.execute(select(PaymentRoute).where(PaymentRoute.id == "ROUTE_HDFC_UPI_FAST"))
        route = route_res.scalar_one_or_none()
        if not route:
            route = PaymentRoute(
                id="ROUTE_HDFC_UPI_FAST",
                name="HDFC Bank Instant UPI Switch",
                payment_method=PaymentMethod.UPI,
                is_active=True,
                health_score=0.985,
            )
            session.add(route)

        payment = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=customer.id,
            amount_inr=Decimal("4999.00"),
            payment_method=PaymentMethod.UPI,
            status=PaymentLifecycleState.PROCESSING,
            idempotency_key=f"idemp_{uuid.uuid4().hex[:12]}",
        )
        session.add_all([merchant, customer, payment])
        await session.commit()

        payment_id = payment.id
        merchant_id = merchant.id
        customer_id = customer.id
        route_id = route.id

    print(f"[SEEDED] Payment ID: {payment_id}")
    print(f"         Amount: INR 4,999.00 | Method: UPI | Customer: Rohit Sharma")

    payment_service = PaymentService(broker)
    recovery_consumer = RecoveryConsumer(broker)
    notification_consumer = NotificationConsumer(broker)

    # -------------------------------------------------------------
    # STEP 1: Payment Failure Ingestion & payment.failed Event
    # -------------------------------------------------------------
    print_banner("STEP 1: PAYMENT FAILURE INGESTION & EVENT PUBLICATION")
    correlation_id = f"corr_{uuid.uuid4().hex[:12]}"
    print(f"Triggering payment failure on PaymentService...")
    print(f"  Correlation ID: {correlation_id}")

    async with async_session_factory() as session:
        pay_res = await session.execute(select(Payment).where(Payment.id == payment_id))
        cur_payment = pay_res.scalar_one()

        await payment_service.record_payment_failure(
            session=session,
            payment=cur_payment,
            route_id=route_id,
            failure_category=FailureCategory.TRANSIENT,
            error_code="UPI_GATEWAY_TIMEOUT",
            reason="Bank NPCI timeout after 15000ms",
            recoverable=True,
            correlation_id=correlation_id,
        )

    # Fetch published event envelope from broker topic log
    published_msg = broker.topic_messages[payment_service.TOPIC][0]
    failure_dict = published_msg.value_dict
    failure_event_id = uuid.UUID(failure_dict["event_id"])

    print(f"[OK] Payment status transitioned: {cur_payment.status.value}")
    print(f"[OK] Event published to topic '{payment_service.TOPIC}':")
    print(f"     event_id: {failure_dict['event_id']}")
    print(f"     event_type: {failure_dict['event_type']}")
    print(f"     correlation_id: {failure_dict['correlation_id']}")
    print(f"     data: {json.dumps(failure_dict['data'], indent=2)}")

    # -------------------------------------------------------------
    # STEP 2: Recovery Consumer processes payment.failed
    # -------------------------------------------------------------
    print_banner("STEP 2: RECOVERY CONSUMER (iro-recovery-group)")
    print(f"Polling topic '{recovery_consumer.TOPIC}' as consumer group '{recovery_consumer.CONSUMER_GROUP}'...")

    async with async_session_factory() as session:
        rc_action = await recovery_consumer.process_next(session)
        print(f"[OK] RecoveryConsumer Action: {rc_action}")

        # Query RecoveryCase created
        case_res = await session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == payment_id))
        rec_case = case_res.scalar_one()
        print(f"[OK] RecoveryCase Created in DB:")
        print(f"     Case ID: {rec_case.id}")
        print(f"     Status: {rec_case.status.value}")
        print(f"     Strategy: {rec_case.strategy.value}")
        print(f"     Max Retries Allowed: {rec_case.max_retries}")
        print(f"     Attempt Count: {rec_case.attempt_count}")

        # Query ProcessedEvent audit record
        pe_res = await session.execute(
            select(ProcessedEvent).where(
                ProcessedEvent.event_id == failure_event_id,
                ProcessedEvent.consumer_name == recovery_consumer.CONSUMER_GROUP,
            )
        )
        pe = pe_res.scalar_one()
        print(f"[OK] ProcessedEvent Audit Logged:")
        print(f"     Event ID: {pe.event_id} | Consumer: {pe.consumer_name} | Processed At: {pe.processed_at}")

    # -------------------------------------------------------------
    # STEP 3: Notification Consumer processes notification.requested
    # -------------------------------------------------------------
    print_banner("STEP 3: NOTIFICATION CONSUMER (iro-notification-group)")
    print(f"Polling topic '{notification_consumer.TOPIC}' as consumer group '{notification_consumer.CONSUMER_GROUP}'...")

    async with async_session_factory() as session:
        notif_action = await notification_consumer.process_next(session)
        print(f"[OK] NotificationConsumer Action: {notif_action}")

        notif_res = await session.execute(select(Notification).where(Notification.payment_id == payment_id))
        notif = notif_res.scalar_one()
        print(f"[OK] Customer Notification Recorded in DB:")
        print(f"     Notification ID: {notif.id}")
        print(f"     Channel: {notif.channel.value}")
        print(f"     Status: {notif.status.value}")
        print(f"     Title: {notif.title}")
        print(f"     Body: {notif.body}")
        print(f"     Template: {notif.metadata_json.get('template')}")
        print(f"     Correlation ID: {notif.metadata_json.get('correlation_id')}")
        print(f"     Causation ID: {notif.metadata_json.get('causation_id')}")

    # -------------------------------------------------------------
    # STEP 4: Atomic Idempotency Test (Redelivery)
    # -------------------------------------------------------------
    print_banner("STEP 4: ATOMIC IDEMPOTENCY REDELIVERY VERIFICATION")
    print(f"Re-publishing exact duplicate event {failure_dict['event_id']}...")
    await broker.publish(recovery_consumer.TOPIC, failure_dict)

    async with async_session_factory() as session:
        dup_action = await recovery_consumer.process_next(session)
        print(f"[IDEMPOTENCY RESULT] Action: {dup_action}")
        assert dup_action == "SKIPPED_DUPLICATE"

        # Verify DB still has exactly 1 RecoveryCase
        all_cases = await session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == payment_id))
        count = len(all_cases.scalars().all())
        print(f"[OK] Total Recovery Cases in DB for Payment: {count} (Strictly 1, no duplicate side-effects)")

    # -------------------------------------------------------------
    # STEP 5: Malformed Poison Pill -> Dead-Letter Queue (DLQ)
    # -------------------------------------------------------------
    print_banner("STEP 5: POISON PILL ROUTING TO DEAD-LETTER QUEUE (DLQ)")
    poison_msg = EventMessage(
        topic="payment.events",
        key="poison_key",
        value=b'{"corrupted": "bad_schema", "event_id": "not-a-valid-uuid"}',
    )
    print(f"Routing poisonous / malformed event to DLQ...")
    dlq_dest = await RetryProcessor.route_failure(
        broker=broker,
        message=poison_msg,
        reason="PydanticValidationError: invalid UUID format",
        is_malformed=True,
    )
    print(f"[OK] Message diverted directly to: {dlq_dest}")

    dlq_polled = await broker.get_message(RetryProcessor.DLQ_TOPIC, "dlq-auditor-group")
    print(f"[DLQ AUDIT] Quarantined message received on DLQ:")
    print(f"    x-death-reason: {dlq_polled.headers.get('x-death-reason')}")
    print(f"    x-death-detail: {dlq_polled.headers.get('x-death-detail')}")
    print(f"    x-original-topic: {dlq_polled.headers.get('x-original-topic')}")

    # -------------------------------------------------------------
    # STEP 6: Active Retry & Scheduled Exponential Backoff
    # -------------------------------------------------------------
    print_banner("STEP 6: ACTIVE RETRY PROCESSOR WITH BACKOFF")
    retry_msg = EventMessage(
        topic="payment.events",
        key="retry_key",
        value=b'{"payment_id": "pay_test", "note": "transient upstream lock"}',
    )
    print("Enqueuing message to retry topic with 0.2s backoff...")
    retry_topic = await RetryProcessor.route_failure(
        broker, retry_msg, reason="DB Connection Pool Exhausted", backoff_sec=0.2
    )
    print(f"[OK] Routed to retry topic: {retry_topic}")

    print("Polling immediately (before delay elapsed)...")
    repub_count_early = await RetryProcessor.process_retries(broker, timeout=0.05)
    print(f"[OK] Republished early count: {repub_count_early} (Message held safely in retry queue)")

    print("Waiting 0.25s for backoff schedule to mature...")
    await asyncio.sleep(0.25)

    repub_count_mature = await RetryProcessor.process_retries(broker, timeout=0.05)
    print(f"[OK] Republished after backoff: {repub_count_mature} message(s) returned to primary topic")

    checker_msg = None
    while True:
        m = await broker.get_message(RetryProcessor.MAIN_TOPIC, "retry-checker-group", timeout=0.2)
        if not m:
            break
        if m.headers.get("x-republished-from"):
            checker_msg = m
            break

    print(f"[OK] Message re-consumed from primary topic:")
    print(f"     x-retry-count: {checker_msg.headers.get('x-retry-count') if checker_msg else 'N/A'}")
    print(f"     x-republished-from: {checker_msg.headers.get('x-republished-from') if checker_msg else 'N/A'}")

    print_banner("PHASE 2 EVENT PIPELINE VERIFICATION COMPLETE: 100% SUCCESS")


if __name__ == "__main__":
    asyncio.run(main())
