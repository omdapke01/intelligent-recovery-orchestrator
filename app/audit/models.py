"""Immutable Audit Record entity definition."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy import (
    DateTime,
    Float,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class ImmutableAuditRecord(Base):
    """Immutable, append-only, cryptographically chained audit record.

    Invariants:
    1. IMMUTABILITY: Application-layer updates and deletes are prohibited by SQLAlchemy event listeners.
    2. HASH CHAINING: Every record contains the SHA-256 payload_hash of itself and links to parent_hash.
    3. DETERMINISTIC SERIALIZATION: Hashes derive from canonical, key-sorted JSON.
    """
    __tablename__ = "immutable_audit_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        default=uuid.uuid4,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
        index=True,
    )
    recovery_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    policy_evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="financial_safety_v1.0",
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    strategy: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    model_used: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    model_confidence: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    tools_called: Mapped[List[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    policy_decision: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    policy_violations: Mapped[List[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    result: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    actor_source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="ORCHESTRATOR",
    )
    escalation_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    evidence_snapshot: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    parent_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<ImmutableAuditRecord id={self.id} payment={self.payment_id} "
            f"action='{self.action}' decision='{self.policy_decision}' hash='{self.payload_hash[:8]}...'>"
        )
