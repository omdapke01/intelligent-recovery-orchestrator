"""Events package exposing contracts, broker abstractions, and retry processor."""

from app.events.broker import (
    EventBroker,
    EventMessage,
    InMemoryEventBroker,
    KafkaEventBroker,
)
from app.events.retry_processor import RetryProcessor
from app.events.schemas import (
    EVENT_PAYLOAD_MAP,
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

__all__ = [
    "EventEnvelope",
    "EVENT_PAYLOAD_MAP",
    "PaymentCreatedPayload",
    "PaymentFailedPayload",
    "PaymentRetryRequestedPayload",
    "PaymentSucceededPayload",
    "RecoveryStartedPayload",
    "RecoveryCompletedPayload",
    "RecoveryFailedPayload",
    "RecoveryEscalatedPayload",
    "RecoveryStoppedPayload",
    "NotificationRequestedPayload",
    "EventBroker",
    "EventMessage",
    "InMemoryEventBroker",
    "KafkaEventBroker",
    "RetryProcessor",
]
