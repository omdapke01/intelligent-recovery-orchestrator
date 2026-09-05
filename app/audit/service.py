"""Immutable Audit Service and Cryptographic Hash-Chain Verification.

Invariants:
1. IMMUTABILITY ENFORCEMENT: Event listeners block UPDATE and DELETE queries on ImmutableAuditRecord.
2. CRYPTOGRAPHIC HASH CHAIN: Every record's hash derives from canonical JSON + parent_hash.
3. TAMPER EVIDENCE: Any alteration to historical records breaks the verification chain.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import ImmutableAuditRecord

logger = logging.getLogger("iro.audit.service")


class ImmutableAuditViolationError(Exception):
    """Raised when an attempt is made to mutate or delete an ImmutableAuditRecord."""
    pass


# ---------------------------------------------------------------------
# SQLAlchemy Event Listeners for Application-Layer Immutability
# ---------------------------------------------------------------------

@event.listens_for(ImmutableAuditRecord, "before_update")
def _prevent_audit_update(mapper, connection, target):
    raise ImmutableAuditViolationError(
        f"Immutability violation: ImmutableAuditRecord '{target.id}' cannot be modified. "
        f"The audit trail is strictly append-only."
    )


@event.listens_for(ImmutableAuditRecord, "before_delete")
def _prevent_audit_delete(mapper, connection, target):
    raise ImmutableAuditViolationError(
        f"Immutability violation: ImmutableAuditRecord '{target.id}' cannot be deleted. "
        f"The audit trail is strictly append-only."
    )


# ---------------------------------------------------------------------
# Canonical Serialization and Cryptographic Hashing
# ---------------------------------------------------------------------

def canonical_json_serialize(data: Dict[str, Any]) -> str:
    """Deterministically serialize a dictionary to JSON (sorted keys, compact separators)."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    )


def normalize_timestamp_str(dt: datetime) -> str:
    """Normalize datetime to consistent ISO 8601 UTC string ending in 'Z'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_audit_payload_hash(canonical_payload: str, parent_hash: Optional[str]) -> str:
    """Compute SHA-256 hash chaining the canonical payload to the parent hash."""
    seed = f"{canonical_payload}:{parent_hash or 'GENESIS'}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class ImmutableAuditLogger:
    """Service managing the creation, persistence, and cryptographic verification of audit records."""

    @classmethod
    async def append_record(
        cls,
        session: AsyncSession,
        payment_id: uuid.UUID,
        action: str,
        policy_decision: str = "PERMITTED",
        result: str = "COMPLETED",
        recovery_case_id: Optional[uuid.UUID] = None,
        policy_evaluation_id: Optional[uuid.UUID] = None,
        policy_version: str = "financial_safety_v1.0",
        strategy: Optional[str] = None,
        model_used: Optional[str] = None,
        model_confidence: Optional[float] = None,
        tools_called: Optional[List[str]] = None,
        policy_violations: Optional[List[str]] = None,
        actor_source: str = "ORCHESTRATOR",
        escalation_reason: Optional[str] = None,
        evidence_snapshot: Optional[Dict[str, Any]] = None,
        event_id: Optional[uuid.UUID] = None,
        timestamp: Optional[datetime] = None,
    ) -> ImmutableAuditRecord:
        """Append a new, cryptographically chained immutable audit record."""
        record_id = uuid.uuid4()
        evt_id = event_id or uuid.uuid4()
        ts = timestamp or datetime.now(timezone.utc)

        # 1. Fetch latest audit record for this payment (or global) to determine parent_hash
        stmt = (
            select(ImmutableAuditRecord.payload_hash)
            .where(ImmutableAuditRecord.payment_id == payment_id)
            .order_by(ImmutableAuditRecord.timestamp.desc(), ImmutableAuditRecord.id.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        parent_hash = res.scalar_one_or_none()

        # 2. Build canonical payload representation
        canonical_dict = {
            "id": str(record_id),
            "event_id": str(evt_id),
            "payment_id": str(payment_id),
            "recovery_case_id": str(recovery_case_id) if recovery_case_id else None,
            "policy_evaluation_id": str(policy_evaluation_id) if policy_evaluation_id else None,
            "policy_version": policy_version,
            "action": action,
            "strategy": strategy,
            "model_used": model_used,
            "model_confidence": float(model_confidence) if model_confidence is not None else None,
            "tools_called": sorted(tools_called or []),
            "policy_decision": policy_decision,
            "policy_violations": sorted(policy_violations or []),
            "result": result,
            "actor_source": actor_source,
            "timestamp": normalize_timestamp_str(ts),
        }

        canonical_str = canonical_json_serialize(canonical_dict)
        payload_hash = compute_audit_payload_hash(canonical_str, parent_hash)

        # 3. Create persistent record
        record = ImmutableAuditRecord(
            id=record_id,
            event_id=evt_id,
            payment_id=payment_id,
            recovery_case_id=recovery_case_id,
            policy_evaluation_id=policy_evaluation_id,
            policy_version=policy_version,
            action=action,
            strategy=strategy,
            model_used=model_used,
            model_confidence=model_confidence,
            tools_called=tools_called or [],
            policy_decision=policy_decision,
            policy_violations=policy_violations or [],
            result=result,
            actor_source=actor_source,
            escalation_reason=escalation_reason,
            evidence_snapshot=evidence_snapshot or {},
            parent_hash=parent_hash,
            payload_hash=payload_hash,
            timestamp=ts,
        )

        session.add(record)
        logger.info(
            f"[AUDIT RECORD APPENDED] ID={record_id} Payment={payment_id} Action={action} "
            f"Decision={policy_decision} ParentHash={(parent_hash or 'GENESIS')[:8]} Hash={payload_hash[:8]}"
        )
        return record

    @classmethod
    async def verify_chain_integrity(
        cls,
        session: AsyncSession,
        payment_id: Optional[uuid.UUID] = None,
    ) -> Tuple[bool, List[str]]:
        """Verify the cryptographic hash chain of audit records.

        Returns (True, []) if valid, or (False, [errors]) if tampered.
        """
        stmt = select(ImmutableAuditRecord)
        if payment_id:
            stmt = stmt.where(ImmutableAuditRecord.payment_id == payment_id)
        stmt = stmt.order_by(ImmutableAuditRecord.timestamp.asc(), ImmutableAuditRecord.id.asc())

        res = await session.execute(stmt)
        records = res.scalars().all()

        errors: List[str] = []
        expected_parent_hash: Optional[str] = None

        for rec in records:
            # 1. Check parent hash linkage
            if rec.parent_hash != expected_parent_hash:
                errors.append(
                    f"Broken link at record '{rec.id}': expected parent_hash '{expected_parent_hash}', "
                    f"found '{rec.parent_hash}'"
                )

            # 2. Recompute payload hash from canonical content
            canonical_dict = {
                "id": str(rec.id),
                "event_id": str(rec.event_id),
                "payment_id": str(rec.payment_id),
                "recovery_case_id": str(rec.recovery_case_id) if rec.recovery_case_id else None,
                "policy_evaluation_id": str(rec.policy_evaluation_id) if rec.policy_evaluation_id else None,
                "policy_version": rec.policy_version,
                "action": rec.action,
                "strategy": rec.strategy,
                "model_used": rec.model_used,
                "model_confidence": float(rec.model_confidence) if rec.model_confidence is not None else None,
                "tools_called": sorted(rec.tools_called or []),
                "policy_decision": rec.policy_decision,
                "policy_violations": sorted(rec.policy_violations or []),
                "result": rec.result,
                "actor_source": rec.actor_source,
                "timestamp": normalize_timestamp_str(rec.timestamp),
            }
            canonical_str = canonical_json_serialize(canonical_dict)
            recomputed_hash = compute_audit_payload_hash(canonical_str, rec.parent_hash)

            if recomputed_hash != rec.payload_hash:
                errors.append(
                    f"Hash mismatch at record '{rec.id}': stored hash '{rec.payload_hash}', "
                    f"recomputed '{recomputed_hash}'"
                )

            expected_parent_hash = rec.payload_hash

        is_valid = len(errors) == 0
        return is_valid, errors


verify_chain_integrity = ImmutableAuditLogger.verify_chain_integrity
