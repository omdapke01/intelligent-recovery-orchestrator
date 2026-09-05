"""Payment Service managing payment events and publishing to Kafka."""

import uuid
from decimal import Decimal
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.broker import EventBroker
from app.events.schemas import (
    EventEnvelope,
    PaymentCreatedPayload,
    PaymentFailedPayload,
    PaymentSucceededPayload,
)
from app.models.enums import (
    AttemptStatus,
    FailureCategory,
    PaymentLifecycleState,
    PaymentMethod,
)
from app.models.payment import Payment
from app.models.payment_attempt import PaymentAttempt
from app.models.payment_failure import PaymentFailure


class PaymentService:
    """Core payment service producing domain events to the Kafka event backbone."""

    TOPIC = "payment.events"
    PRODUCER_NAME = "payment-service"

    def __init__(self, broker: EventBroker):
        self.broker = broker

    async def create_payment(
        self,
        session: AsyncSession,
        merchant_id: uuid.UUID,
        customer_id: uuid.UUID,
        amount_inr: Decimal,
        payment_method: PaymentMethod,
        preferred_route_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Payment:
        """Create a new payment and publish 'payment.created' event."""
        payment_id = uuid.uuid4()
        corr_id = correlation_id or f"corr_{payment_id.hex[:16]}"
        idempotency_key = f"pay_live_{payment_id.hex[:14]}"

        payment = Payment(
            id=payment_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            amount_inr=amount_inr,
            currency="INR",
            payment_method=payment_method,
            preferred_route_id=preferred_route_id,
            status=PaymentLifecycleState.CREATED,
            idempotency_key=idempotency_key,
            metadata_json={"correlation_id": corr_id},
        )
        session.add(payment)
        await session.commit()

        # Publish payment.created event
        event = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="payment.created",
            producer=self.PRODUCER_NAME,
            correlation_id=corr_id,
            data=PaymentCreatedPayload(
                payment_id=payment.id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                amount_inr=amount_inr,
                currency="INR",
                payment_method=payment_method,
                idempotency_key=idempotency_key,
            ).model_dump(mode="json"),
        )
        await self.broker.publish(
            self.TOPIC,
            event.model_dump(mode="json"),
            key=str(merchant_id),
        )
        return payment

    async def record_payment_failure(
        self,
        session: AsyncSession,
        payment: Payment,
        route_id: str,
        failure_category: FailureCategory,
        error_code: str,
        reason: str,
        recoverable: bool,
        attempt_number: int = 1,
        suggested_backoff_sec: int = 0,
        correlation_id: Optional[str] = None,
    ) -> PaymentFailure:
        """
        Record a failed attempt in PostgreSQL and emit a 'payment.failed' event to Kafka.
        Preserves the correlation_id across downstream recovery and notification pipelines.
        """
        corr_id = correlation_id or payment.metadata_json.get("correlation_id", f"corr_{payment.id.hex[:16]}")

        # Update payment status
        payment.status = PaymentLifecycleState.FAILED
        payment.final_error_code = error_code

        # Create attempt
        attempt = PaymentAttempt(
            id=uuid.uuid4(),
            payment_id=payment.id,
            attempt_number=attempt_number,
            route_id=route_id,
            payment_method=payment.payment_method,
            status=AttemptStatus.FAILED,
        )
        session.add(attempt)

        # Create failure diagnostic
        failure = PaymentFailure(
            id=uuid.uuid4(),
            attempt_id=attempt.id,
            payment_id=payment.id,
            failure_category=failure_category,
            error_code=error_code,
            reason=reason,
            recoverable=recoverable,
            suggested_backoff_sec=suggested_backoff_sec,
        )
        session.add(failure)
        await session.commit()

        # Publish payment.failed event
        event = EventEnvelope(
            event_id=uuid.uuid4(),
            event_type="payment.failed",
            producer=self.PRODUCER_NAME,
            correlation_id=corr_id,
            data=PaymentFailedPayload(
                payment_id=payment.id,
                merchant_id=payment.merchant_id,
                customer_id=payment.customer_id,
                amount_inr=payment.amount_inr,
                payment_method=payment.payment_method,
                route_id=route_id,
                failure_category=failure_category,
                error_code=error_code,
                reason=reason,
                attempt_number=attempt_number,
                recoverable=recoverable,
                suggested_backoff_sec=suggested_backoff_sec,
            ).model_dump(mode="json"),
        )

        await self.broker.publish(
            self.TOPIC,
            event.model_dump(mode="json"),
            key=str(payment.merchant_id),
        )
        return failure
