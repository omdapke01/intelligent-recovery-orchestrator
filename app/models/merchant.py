"""Merchant entity definition."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, List
from sqlalchemy import Boolean, Enum, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import MerchantTier

if TYPE_CHECKING:
    from app.models.payment import Payment


class Merchant(Base, TimestampMixin):
    """Merchant entity registering for automated revenue recovery."""
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        index=True,
    )
    mcc: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="Merchant Category Code",
    )
    tier: Mapped[MerchantTier] = mapped_column(
        Enum(MerchantTier, native_enum=False),
        nullable=False,
        default=MerchantTier.STARTUP,
    )
    recovery_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    max_auto_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
    )
    max_recovery_amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("100000.00"),
    )
    min_recovery_amount_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("50.00"),
    )
    auto_escalate_threshold_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("50000.00"),
    )

    # Relationships
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="merchant",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Merchant id={self.id} name='{self.name}' tier={self.tier}>"
