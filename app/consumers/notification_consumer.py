"""Notification Consumer for processing customer communication requests."""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.broker import EventBroker, EventMessage
from app.events.schemas import EventEnvelope, NotificationRequestedPayload
from app.models.base import utc_now
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from app.models.processed_event import ProcessedEvent

logger = logging.getLogger(__name__)


class NotificationConsumer:
    """
    Decoupled consumer in group 'iro-notification-group'.
    Listens to 'notification.requested' events, enforces atomic idempotency,
    and records customer outbound notifications in PostgreSQL.
    """

    CONSUMER_GROUP = "iro-notification-group"
    TOPIC = "payment.events"

    def __init__(self, broker: EventBroker):
        self.broker = broker
        if hasattr(self.broker, "register_consumer_group"):
            self.broker.register_consumer_group(self.TOPIC, self.CONSUMER_GROUP)

    async def process_next(self, session: AsyncSession, timeout: float = 1.0) -> Optional[str]:
        """
        Poll and process next notification event.
        Skips irrelevant event types, committing them.
        Returns 'PROCESSED', 'SKIPPED_DUPLICATE', or None.
        """
        while True:
            msg = await self.broker.get_message(self.TOPIC, self.CONSUMER_GROUP, timeout=timeout)
            if not msg:
                return None

            try:
                data_dict = msg.value_dict
            except Exception as e:
                raise ValueError(f"Malformed event JSON on {msg.topic}: {e}") from e

            if data_dict.get("event_type") != "notification.requested":
                await self.broker.commit(self.CONSUMER_GROUP, msg)
                continue

            try:
                envelope = EventEnvelope[NotificationRequestedPayload].model_validate(data_dict)
            except Exception as e:
                raise ValueError(f"Malformed notification event on {msg.topic}: {e}") from e

            action = await self.handle_notification_requested(session, envelope)
            await self.broker.commit(self.CONSUMER_GROUP, msg)
            return action

    async def handle_notification_requested(
        self,
        session: AsyncSession,
        event: EventEnvelope[NotificationRequestedPayload],
    ) -> str:
        """
        Atomically records event idempotency and writes customer Notification record.
        """
        payload = event.data

        # --- ATOMIC DATABASE TRANSACTION BOUNDARY ---
        existing_pe = await session.get(ProcessedEvent, (event.event_id, self.CONSUMER_GROUP))
        if existing_pe:
            logger.info(f"Duplicate notification event {event.event_id} skipped for {self.CONSUMER_GROUP}")
            return "SKIPPED_DUPLICATE"

        # 1. Mark event processed
        pe = ProcessedEvent(
            event_id=event.event_id,
            consumer_name=self.CONSUMER_GROUP,
            event_type=event.event_type,
        )
        session.add(pe)

        # 2. Map channel
        channel_enum = NotificationChannel(payload.channel) if payload.channel in NotificationChannel.__members__ else NotificationChannel.SMS

        # 3. Create Notification entity
        title = f"Alert: Payment {payload.payment_id} Status"
        body = f"Template: {payload.template}. Context: {payload.payload}"

        notif = Notification(
            id=payload.notification_id,
            payment_id=payload.payment_id,
            customer_id=payload.customer_id,
            channel=channel_enum,
            status=NotificationStatus.SENT,  # Simulated successful outbound dispatch
            title=title,
            body=body,
            metadata_json={
                "correlation_id": event.correlation_id,
                "causation_id": event.causation_id,
                "template": payload.template,
                **payload.payload,
            },
            sent_at=utc_now(),
        )
        session.add(notif)

        # Commit both idempotency lock and notification in a single atomic transaction
        await session.commit()
        return "PROCESSED"
