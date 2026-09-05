"""Payment Attempt entity definition."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utc_now
from app.models.enums import AttemptStatus, PaymentMethod

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.payment_failure import PaymentFailure
    from app.models.payment_route import PaymentRoute


class PaymentAttempt(Base, TimestampMixin):
    """An individual execution attempt against a payment route/gateway."""
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("payment_routes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False),
        nullable=False,
    )
    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, native_enum=False),
        nullable=False,
        default=AttemptStatus.INITIATED,
        index=True,
    )
    gateway_ref_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    initiated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "payment_id",
            "attempt_number",
            name="uq_payment_attempt_number",
        ),
    )

    # Relationships
    payment: Mapped["Payment"] = relationship(
        "Payment",
        back_populates="attempts",
    )
    route: Mapped["PaymentRoute"] = relationship(
        "PaymentRoute",
        back_populates="attempts",
    )
    failure: Mapped[Optional["PaymentFailure"]] = relationship(
        "PaymentFailure",
        back_populates="attempt",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PaymentAttempt id={self.id} payment_id={self.payment_id} num={self.attempt_number} status={self.status}>"
