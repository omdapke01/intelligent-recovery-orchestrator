"""PostgreSQL-backed Durable Idempotency Barrier for safe recovery execution.

Guarantees:
1. PostgreSQL remains the durable source of truth (not Redis).
2. Every recovery action has a unique idempotency key: recovery:{recovery_case_id}:attempt:{attempt_number}
3. Race conditions between concurrent workers are trapped by the database unique constraint,
   guaranteeing the payment provider is NEVER called more than once for the same idempotency key.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttemptStatus, PaymentMethod
from app.models.payment_attempt import PaymentAttempt

logger = logging.getLogger("iro.execution.idempotency")


@dataclass
class IdempotencyReservationResult:
    is_new: bool
    attempt: Optional[PaymentAttempt] = None
    reason: Optional[str] = None


class PostgresIdempotencyBarrier:
    """Manages durable database-level idempotency reservations before payment calls."""

    @staticmethod
    def generate_idempotency_key(recovery_case_id: uuid.UUID, attempt_number: int) -> str:
        """Format canonical idempotency key for a recovery attempt."""
        return f"recovery:{recovery_case_id}:attempt:{attempt_number}"

    @classmethod
    async def reserve_attempt(
        cls,
        session: AsyncSession,
        payment_id: uuid.UUID,
        attempt_number: int,
        idempotency_key: str,
        route_id: str,
        payment_method: PaymentMethod,
    ) -> IdempotencyReservationResult:
        """Atomically reserve a payment attempt record in PostgreSQL.

        Uses the database unique constraint as the ultimate race-condition safety net.
        If another worker concurrently attempts the same idempotency key, the resulting
        IntegrityError is handled cleanly and duplicate provider execution is blocked.
        """
        # 1. Quick check for already-completed or already-existing attempt
        stmt = select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            logger.warning(
                f"[IDEMPOTENCY] Attempt with key '{idempotency_key}' already exists "
                f"(status={existing.status.value}). Duplicate execution blocked."
            )
            return IdempotencyReservationResult(
                is_new=False,
                attempt=existing,
                reason="EXISTING_ATTEMPT_RECORDED",
            )

        # 2. Attempt atomic insertion with flush/commit
        attempt = PaymentAttempt(
            id=uuid.uuid4(),
            payment_id=payment_id,
            attempt_number=attempt_number,
            route_id=route_id,
            payment_method=payment_method,
            status=AttemptStatus.INITIATED,
            idempotency_key=idempotency_key,
        )
        session.add(attempt)

        try:
            # Use flush/savepoint to trap IntegrityError immediately
            await session.flush()
            logger.info(
                f"[IDEMPOTENCY RESERVED] Reserved attempt {attempt_number} with key '{idempotency_key}' in DB."
            )
            return IdempotencyReservationResult(is_new=True, attempt=attempt)
        except IntegrityError as err:
            # Atomic safety net triggered: another worker concurrently committed this attempt!
            await session.rollback()
            logger.warning(
                f"[IDEMPOTENCY RACE CAUGHT] Database unique constraint prevented duplicate attempt "
                f"for key '{idempotency_key}': {err}"
            )
            # Retrieve the winning worker's attempt
            winner = (await session.execute(stmt)).scalar_one_or_none()
            return IdempotencyReservationResult(
                is_new=False,
                attempt=winner,
                reason="CONCURRENT_RACE_PREVENTED_BY_DB_CONSTRAINT",
            )
