"""Payment Failure diagnostic entity definition."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utc_now
from app.models.enums import FailureCategory

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.payment_attempt import PaymentAttempt


class PaymentFailure(Base, TimestampMixin):
    """Granular failure diagnostic record attached to failed payment attempts."""
    __tablename__ = "payment_failures"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("payment_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    failure_category: Mapped[FailureCategory] = mapped_column(
        Enum(FailureCategory, native_enum=False),
        nullable=False,
        index=True,
    )
    error_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    recoverable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    suggested_backoff_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    attempt: Mapped["PaymentAttempt"] = relationship(
        "PaymentAttempt",
        back_populates="failure",
    )
    payment: Mapped["Payment"] = relationship(
        "Payment",
        back_populates="failures",
    )

    @property
    def is_recoverable(self) -> bool:
        return self.recoverable

    @property
    def error_message(self) -> str:
        return self.reason

    def __repr__(self) -> str:
        return f"<PaymentFailure code='{self.error_code}' cat={self.failure_category} recoverable={self.recoverable}>"
