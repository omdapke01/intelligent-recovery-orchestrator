"""Recovery Execution Consumer listening to payment.retry_requested events."""

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.events.broker import EventBroker
from app.events.schemas import EventEnvelope, PaymentRetryRequestedPayload
from app.execution.service import SafeRecoveryExecutionService
from app.models.processed_event import ProcessedEvent

logger = logging.getLogger("iro.execution.consumer")


class RecoveryExecutionConsumer:
    """Consumer group for executing recovery attempts (iro-execution-group)."""

    CONSUMER_NAME = "iro-execution-group"

    def __init__(
        self,
        broker: EventBroker,
        execution_service: SafeRecoveryExecutionService,
    ):
        self.broker = broker
        self.service = execution_service

    async def start(self) -> None:
        """Subscribe to payment.events topic."""
        await self.broker.subscribe(
            topic="payment.events",
            consumer_group=self.CONSUMER_NAME,
            handler=self.handle_event,
        )
        logger.info(f"RecoveryExecutionConsumer registered on 'payment.events' ({self.CONSUMER_NAME})")

    async def handle_event(self, event: EventEnvelope[Any]) -> None:
        """Handle incoming event if it is payment.retry_requested."""
        if event.event_type != "payment.retry_requested":
            return

        async with async_session_factory() as session:
            # Check consumer group idempotency
            stmt = select(ProcessedEvent).where(
                ProcessedEvent.event_id == event.event_id,
                ProcessedEvent.consumer_name == self.CONSUMER_NAME,
            )
            already_processed = (await session.execute(stmt)).scalar_one_or_none()
            if already_processed:
                logger.info(f"Skipping duplicate retry_requested event {event.event_id}")
                return

            payload: PaymentRetryRequestedPayload
            if isinstance(event.payload, dict):
                payload = PaymentRetryRequestedPayload(**event.payload)
            else:
                payload = event.payload

            # Record event as processed by this consumer
            audit_record = ProcessedEvent(
                event_id=event.event_id,
                consumer_name=self.CONSUMER_NAME,
                event_type=event.event_type,
            )
            session.add(audit_record)
            await session.commit()

            # Execute recovery under lock and idempotency barrier
            await self.service.execute_recovery_attempt(
                session=session,
                payment_id=payload.payment_id,
                recovery_case_id=payload.recovery_case_id,
                attempt_number=payload.attempt_number,
                target_route_id=payload.target_route_id,
                strategy=payload.strategy,
                correlation_id=event.correlation_id,
            )
