"""Strongly typed, versioned event schemas for the payment and recovery pipeline."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FailureCategory, PaymentMethod, RecoveryStrategy


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    """Standardized event envelope wrapping all domain events."""
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    version: str = "v1"
    producer: str = "payment-service"
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str
    causation_id: Optional[str] = None
    data: T

    model_config = ConfigDict(extra="forbid")


# --- Domain Event Payloads ---

class PaymentCreatedPayload(BaseModel):
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    customer_id: uuid.UUID
    amount_inr: Decimal
    currency: str = "INR"
    payment_method: PaymentMethod
    idempotency_key: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaymentFailedPayload(BaseModel):
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    customer_id: uuid.UUID
    amount_inr: Decimal
    payment_method: PaymentMethod
    route_id: str
    failure_category: FailureCategory
    error_code: str
    reason: str
    attempt_number: int
    recoverable: bool
    suggested_backoff_sec: int = 0


class PaymentRetryRequestedPayload(BaseModel):
    payment_id: uuid.UUID
    recovery_case_id: uuid.UUID
    attempt_number: int
    target_route_id: str
    strategy: RecoveryStrategy
    scheduled_at: datetime = Field(default_factory=utc_now)


class PaymentSucceededPayload(BaseModel):
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    amount_inr: Decimal
    attempt_number: int
    route_id: str
    gateway_ref_id: Optional[str] = None


class RecoveryStartedPayload(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    strategy: RecoveryStrategy
    attempt_count: int = 0


class RecoveryCompletedPayload(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    recovered_amount_inr: Decimal
    total_attempts: int


class RecoveryFailedPayload(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    attempt_number: int
    error_code: str
    reason: str


class RecoveryEscalatedPayload(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    escalation_reason: str
    amount_inr: Decimal


class RecoveryStoppedPayload(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    stop_reason: str


class NotificationRequestedPayload(BaseModel):
    notification_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    customer_id: Optional[uuid.UUID] = None
    payment_id: Optional[uuid.UUID] = None
    channel: str = "SMS"  # EMAIL, SMS, WHATSAPP, PUSH
    template: str
    payload: Dict[str, Any] = Field(default_factory=dict)


# Mapping of event_type string to payload model for validation
EVENT_PAYLOAD_MAP: Dict[str, type] = {
    "payment.created": PaymentCreatedPayload,
    "payment.failed": PaymentFailedPayload,
    "payment.retry_requested": PaymentRetryRequestedPayload,
    "payment.succeeded": PaymentSucceededPayload,
    "recovery.started": RecoveryStartedPayload,
    "recovery.completed": RecoveryCompletedPayload,
    "recovery.failed": RecoveryFailedPayload,
    "recovery.escalated": RecoveryEscalatedPayload,
    "recovery.stopped": RecoveryStoppedPayload,
    "notification.requested": NotificationRequestedPayload,
    "agent.investigation.completed": dict,  # dynamically typed to avoid circular import with app.agent
}

