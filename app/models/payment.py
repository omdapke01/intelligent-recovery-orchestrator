"""Payment entity definition with lifecycle states."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    JSON,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import PaymentLifecycleState, PaymentMethod

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.merchant import Merchant
    from app.models.payment_attempt import PaymentAttempt
    from app.models.payment_failure import PaymentFailure
    from app.models.payment_route import PaymentRoute
    from app.models.recovery_case import RecoveryCase


class Payment(Base, TimestampMixin):
    """Primary payment entity tracked across standard transaction and recovery lifecycles."""
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False),
        nullable=False,
        index=True,
    )
    preferred_route_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        ForeignKey("payment_routes.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[PaymentLifecycleState] = mapped_column(
        Enum(PaymentLifecycleState, native_enum=False),
        nullable=False,
        default=PaymentLifecycleState.CREATED,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    final_error_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    __table_args__ = (
        CheckConstraint("amount_inr > 0", name="check_payment_amount_positive"),
    )

    # Relationships
    merchant: Mapped["Merchant"] = relationship(
        "Merchant",
        back_populates="payments",
        lazy="selectin",
    )
    customer: Mapped["Customer"] = relationship(
        "Customer",
        back_populates="payments",
        lazy="selectin",
    )
    preferred_route: Mapped[Optional["PaymentRoute"]] = relationship(
        "PaymentRoute",
        back_populates="payments",
        lazy="selectin",
    )
    attempts: Mapped[List["PaymentAttempt"]] = relationship(
        "PaymentAttempt",
        back_populates="payment",
        cascade="all, delete-orphan",
        order_by="PaymentAttempt.attempt_number",
        lazy="selectin",
    )
    failures: Mapped[List["PaymentFailure"]] = relationship(
        "PaymentFailure",
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    recovery_case: Mapped[Optional["RecoveryCase"]] = relationship(
        "RecoveryCase",
        back_populates="payment",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.id} amount={self.amount_inr} status={self.status} method={self.payment_method}>"
