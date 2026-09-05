"""Customer entity definition."""

import uuid
from typing import TYPE_CHECKING, List
from sqlalchemy import CheckConstraint, Float, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.payment import Payment


class Customer(Base, TimestampMixin):
    """Customer entity with risk and historical payment behavioral metrics."""
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    external_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    email_masked: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    phone_masked: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    historical_success_rate: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )
    total_transactions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    risk_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "historical_success_rate >= 0.0 AND historical_success_rate <= 1.0",
            name="check_customer_success_rate_range",
        ),
        CheckConstraint(
            "risk_score >= 0.0 AND risk_score <= 1.0",
            name="check_customer_risk_score_range",
        ),
    )

    # Relationships
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="customer",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} ext_id='{self.external_id}' success_rate={self.historical_success_rate:.2f}>"
