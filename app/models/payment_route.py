"""Payment Route entity definition."""

from typing import TYPE_CHECKING, List
from sqlalchemy import Boolean, CheckConstraint, Enum, Float, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import PaymentMethod, RouteStatus

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.payment_attempt import PaymentAttempt


class PaymentRoute(Base, TimestampMixin):
    """Payment Gateway / Banking channel route."""
    __tablename__ = "payment_routes"

    id: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        comment="Route identifier e.g. ROUTE_HDFC_UPI, ROUTE_AXIS_CARDS",
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="RAZORPAY",
    )
    health_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )
    avg_latency_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=250.0,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus, native_enum=False),
        nullable=False,
        default=RouteStatus.HEALTHY,
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "health_score >= 0.0 AND health_score <= 1.0",
            name="check_route_health_score_range",
        ),
    )

    # Relationships
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="preferred_route",
    )
    attempts: Mapped[List["PaymentAttempt"]] = relationship(
        "PaymentAttempt",
        back_populates="route",
    )

    def __repr__(self) -> str:
        return f"<PaymentRoute id='{self.id}' method={self.payment_method} health={self.health_score:.2f} status={self.status}>"
