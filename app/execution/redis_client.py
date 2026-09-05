"""Async Redis Client interface with high-fidelity in-memory implementation."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger("iro.redis")


class InMemoryRedisClient:
    """Async in-memory Redis simulator implementing exact Redis SET NX + TTL

    and atomic Lua release semantics for zero-dependency distributed locking.
    """

    def __init__(self):
        self._store: Dict[str, str] = {}
        self._expires_at: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _is_expired(self, key: str) -> bool:
        if key in self._expires_at:
            if time.time() >= self._expires_at[key]:
                self._store.pop(key, None)
                self._expires_at.pop(key, None)
                return True
        return False

    async def set(
        self,
        name: str,
        value: Any,
        ex: Optional[Union[int, float]] = None,
        px: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> Optional[bool]:
        """Set key to value with optional TTL and conditional checks.

        Returns True if set, None/False if condition (nx/xx) not met.
        """
        async with self._lock:
            val_str = str(value)
            self._is_expired(name)
            key_exists = name in self._store

            if nx and key_exists:
                return None
            if xx and not key_exists:
                return None

            self._store[name] = val_str

            if px is not None:
                self._expires_at[name] = time.time() + (px / 1000.0)
            elif ex is not None:
                self._expires_at[name] = time.time() + float(ex)
            else:
                self._expires_at.pop(name, None)

            return True

    async def get(self, name: str) -> Optional[str]:
        """Get value of key, returning None if expired or non-existent."""
        async with self._lock:
            if self._is_expired(name):
                return None
            return self._store.get(name)

    async def delete(self, *names: str) -> int:
        """Delete one or more keys, returning count deleted."""
        async with self._lock:
            count = 0
            for name in names:
                if not self._is_expired(name) and name in self._store:
                    self._store.pop(name, None)
                    self._expires_at.pop(name, None)
                    count += 1
            return count

    async def exists(self, *names: str) -> int:
        """Check how many keys exist."""
        async with self._lock:
            count = 0
            for name in names:
                if not self._is_expired(name) and name in self._store:
                    count += 1
            return count

    async def pttl(self, name: str) -> int:
        """Get remaining TTL in milliseconds (-2 if non-existent, -1 if no TTL)."""
        async with self._lock:
            if self._is_expired(name) or name not in self._store:
                return -2
            if name not in self._expires_at:
                return -1
            remaining_ms = int((self._expires_at[name] - time.time()) * 1000)
            return max(0, remaining_ms)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        """Execute atomic Lua script.

        Specifically implements the canonical distributed lock release script:
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        async with self._lock:
            keys = [str(k) for k in keys_and_args[:numkeys]]
            args = [str(a) for a in keys_and_args[numkeys:]]

            if not keys:
                return 0

            target_key = keys[0]
            expected_token = args[0] if args else None

            # Handle lock release script
            if "KEYS[1]" in script and "ARGV[1]" in script and "del" in script:
                if self._is_expired(target_key):
                    return 0
                current_val = self._store.get(target_key)
                if current_val == expected_token:
                    self._store.pop(target_key, None)
                    self._expires_at.pop(target_key, None)
                    return 1
                return 0

            return 0

    async def flushdb(self) -> None:
        """Clear all stored keys."""
        async with self._lock:
            self._store.clear()
            self._expires_at.clear()


# Shared in-memory instance for testing and zero-dependency mode
_shared_in_memory_client = InMemoryRedisClient()


def get_redis_client(use_in_memory: bool = True) -> Any:
    """Return an async Redis client.

    Defaults to InMemoryRedisClient for zero-dependency reliability across
    local environments, CI, and test suites.
    """
    if use_in_memory:
        return _shared_in_memory_client

    try:
        import redis.asyncio as aioredis
        from app.config import settings
        return aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning(f"Could not connect to Redis server ({e}), falling back to InMemoryRedisClient")
        return _shared_in_memory_client
