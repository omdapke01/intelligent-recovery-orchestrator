"""Redis Distributed Lock implementation using SET NX + TTL and atomic Lua release."""

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("iro.execution.lock")

# Canonical atomic release script: checks token ownership before deleting key
RELEASE_LUA_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
""".strip()


class LockAcquisitionError(Exception):
    """Raised when distributed lock acquisition fails due to contention."""
    pass


class RedisDistributedLock:
    """Atomic distributed lock for short-lived payment recovery coordination.

    Guarantees:
    1. Lock acquisition is atomic (SET NX PX).
    2. Lock has explicit TTL preventing permanent deadlocks if worker crashes.
    3. Lock ownership is verified before release (atomic Lua script).
    4. Another worker cannot delete or release an unowned or expired lock.
    """

    def __init__(
        self,
        redis_client: Any,
        key: str,
        ttl_ms: int = 10000,
        owner_token: Optional[str] = None,
    ):
        self.redis = redis_client
        self.key = key
        self.ttl_ms = ttl_ms
        self.owner_token = owner_token or f"token_{uuid.uuid4().hex}"
        self._acquired = False

    @classmethod
    def for_payment(
        cls,
        redis_client: Any,
        payment_id: uuid.UUID,
        ttl_ms: int = 10000,
        worker_id: Optional[str] = None,
    ) -> "RedisDistributedLock":
        """Convenience factory for payment-level recovery locks."""
        key = f"lock:recovery:payment:{payment_id}"
        token = f"worker_{worker_id or 'default'}_{uuid.uuid4().hex}"
        return cls(redis_client=redis_client, key=key, ttl_ms=ttl_ms, owner_token=token)

    async def acquire(self) -> bool:
        """Attempt atomic lock acquisition.

        Returns True if acquired, False if already held by another worker.
        """
        res = await self.redis.set(
            self.key,
            self.owner_token,
            nx=True,
            px=self.ttl_ms,
        )
        self._acquired = bool(res)
        if self._acquired:
            logger.debug(f"[LOCK] Acquired lock '{self.key}' with token '{self.owner_token}' (TTL: {self.ttl_ms}ms)")
        else:
            logger.warning(f"[LOCK CONTENTION] Failed to acquire lock '{self.key}'. Key held by another process.")
        return self._acquired

    async def release(self) -> bool:
        """Atomically release lock ONLY if token matches current owner.

        Returns True if successfully deleted by owner, False if expired or owned by another.
        """
        try:
            res = await self.redis.eval(
                RELEASE_LUA_SCRIPT,
                1,
                self.key,
                self.owner_token,
            )
            released = bool(res == 1)
            if released:
                logger.debug(f"[LOCK] Safely released lock '{self.key}' by token '{self.owner_token}'")
            else:
                logger.warning(
                    f"[LOCK MISMATCH/EXPIRED] Could not release lock '{self.key}'. "
                    f"Lock already expired or owned by another worker token."
                )
            self._acquired = False
            return released
        except Exception as e:
            logger.error(f"[LOCK ERROR] Failed during release of '{self.key}': {e}")
            self._acquired = False
            return False

    async def is_locked(self) -> bool:
        """Check if lock key is currently active in Redis."""
        val = await self.redis.get(self.key)
        return val is not None

    async def is_owned(self) -> bool:
        """Check if lock key is currently active and owned by this instance."""
        val = await self.redis.get(self.key)
        return val == self.owner_token

    async def get_remaining_ttl_ms(self) -> int:
        """Return remaining TTL in milliseconds."""
        return await self.redis.pttl(self.key)

    async def __aenter__(self) -> "RedisDistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise LockAcquisitionError(f"Contention: Unable to acquire lock for '{self.key}'")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.release()
