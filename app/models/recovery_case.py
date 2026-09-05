"""Recovery Case entity definition."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    PaymentLifecycleState,
    RecoveryState,
    RecoveryStrategy,
)

if TYPE_CHECKING:
    from app.models.payment import Payment


class RecoveryCase(Base, TimestampMixin):
    """Orchestration tracking record for payments undergoing revenue recovery."""
    __tablename__ = "recovery_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[PaymentLifecycleState] = mapped_column(
        Enum(PaymentLifecycleState, native_enum=False),
        nullable=False,
        default=PaymentLifecycleState.RECOVERY_PENDING,
        index=True,
    )
    recovery_state: Mapped[RecoveryState] = mapped_column(
        Enum(RecoveryState, native_enum=False),
        nullable=False,
        default=RecoveryState.FAILED,
        index=True,
    )
    strategy: Mapped[RecoveryStrategy] = mapped_column(
        Enum(RecoveryStrategy, native_enum=False),
        nullable=False,
        default=RecoveryStrategy.NONE,
    )
    plan_details: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    stop_reason: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    estimated_recovery_rate: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    recovered_amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    # Aliases for compatibility
    @property
    def retry_count(self) -> int:
        return self.attempt_count

    @retry_count.setter
    def retry_count(self, val: int) -> None:
        self.attempt_count = val

    @property
    def max_retries(self) -> int:
        return self.max_attempts

    @max_retries.setter
    def max_retries(self, val: int) -> None:
        self.max_attempts = val

    @property
    def recommended_strategy(self) -> RecoveryStrategy:
        return self.strategy

    @recommended_strategy.setter
    def recommended_strategy(self, val: RecoveryStrategy) -> None:
        self.strategy = val

    @property
    def resolved_at(self) -> Optional[datetime]:
        return self.completed_at

    @resolved_at.setter
    def resolved_at(self, val: Optional[datetime]) -> None:
        self.completed_at = val

    # Relationships
    payment: Mapped["Payment"] = relationship(
        "Payment",
        back_populates="recovery_case",
    )

    def __repr__(self) -> str:
        return (
            f"<RecoveryCase id={self.id} payment_id={self.payment_id} "
            f"status={self.status} retries={self.retry_count}/{self.max_retries}>"
        )
