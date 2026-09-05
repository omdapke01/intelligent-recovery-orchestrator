"""Recovery Consumer for processing payment failure events and initializing recovery cases."""

import logging
import uuid
from decimal import Decimal
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.broker import EventBroker, EventMessage
from app.events.schemas import (
    EventEnvelope,
    NotificationRequestedPayload,
    PaymentFailedPayload,
)
from app.models.enums import PaymentLifecycleState, RecoveryStrategy
from app.models.payment import Payment
from app.models.processed_event import ProcessedEvent
from app.models.recovery_case import RecoveryCase
from app.orchestrator.orchestrator import IntelligentRecoveryOrchestrator

logger = logging.getLogger(__name__)


class RecoveryConsumer:
    """
    Decoupled consumer in group 'iro-recovery-group'.
    Listens to payment failure events, enforces atomic idempotency in PostgreSQL,
    and delegates to the IntelligentRecoveryOrchestrator for deterministic recovery planning.
    """

    CONSUMER_GROUP = "iro-recovery-group"
    TOPIC = "payment.events"

    def __init__(self, broker: EventBroker):
        self.broker = broker
        if hasattr(self.broker, "register_consumer_group"):
            self.broker.register_consumer_group(self.TOPIC, self.CONSUMER_GROUP)
        self.orchestrator = IntelligentRecoveryOrchestrator(broker)

    async def process_next(self, session: AsyncSession, timeout: float = 1.0) -> Optional[str]:
        """
        Poll and process a single payment failure event from the event broker.
        Skips irrelevant event types, committing them.
        Returns the action taken ('PROCESSED', 'SKIPPED_DUPLICATE', or None).
        """
        while True:
            msg = await self.broker.get_message(self.TOPIC, self.CONSUMER_GROUP, timeout=timeout)
            if not msg:
                return None

            try:
                data_dict = msg.value_dict
            except Exception as e:
                # Let caller/supervisor route malformed messages to DLQ
                raise ValueError(f"Malformed event JSON on {msg.topic}: {e}") from e

            if data_dict.get("event_type") != "payment.failed":
                await self.broker.commit(self.CONSUMER_GROUP, msg)
                continue

            try:
                envelope = EventEnvelope[PaymentFailedPayload].model_validate(data_dict)
            except Exception as e:
                raise ValueError(f"Malformed event payload on {msg.topic}: {e}") from e

            action = await self.handle_payment_failed(session, envelope)
            await self.broker.commit(self.CONSUMER_GROUP, msg)
            return action

    async def handle_payment_failed(
        self,
        session: AsyncSession,
        event: EventEnvelope[PaymentFailedPayload],
    ) -> str:
        """
        Execute atomic idempotency registration and RecoveryCase initialization in a single DB transaction.
        """
        payload = event.data

        # --- ATOMIC DATABASE TRANSACTION BOUNDARY ---
        # 1. Attempt to register event idempotency
        existing_pe = await session.get(ProcessedEvent, (event.event_id, self.CONSUMER_GROUP))
        if existing_pe:
            logger.info(f"Duplicate event {event.event_id} skipped for consumer {self.CONSUMER_GROUP}")
            return "SKIPPED_DUPLICATE"

        # Mark processed
        pe = ProcessedEvent(
            event_id=event.event_id,
            consumer_name=self.CONSUMER_GROUP,
            event_type=event.event_type,
        )
        session.add(pe)
        await session.flush()

        # 2. Delegate to Intelligent Recovery Orchestrator
        await self.orchestrator.orchestrate_failure(
            session=session,
            failure_payload=payload,
            correlation_id=event.correlation_id,
            causation_id=str(event.event_id),
        )

        return "PROCESSED"
