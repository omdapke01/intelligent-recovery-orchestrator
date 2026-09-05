"""Processed Event entity for atomic consumer idempotency."""

import uuid
from datetime import datetime
from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class ProcessedEvent(Base):
    """
    Durable record of events processed by individual consumer groups.
    Enforces exactly-once execution semantics at the database transaction layer.
    """
    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        comment="Unique event identifier from event envelope",
    )
    consumer_name: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        comment="Consumer group or worker identifier (e.g. iro-recovery-group)",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    def __repr__(self) -> str:
        return f"<ProcessedEvent event_id={self.event_id} consumer='{self.consumer_name}'>"
