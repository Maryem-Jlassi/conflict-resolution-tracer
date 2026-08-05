"""Concurrency control and coherence monitoring."""

import asyncio
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass


class StateCode(Enum):
    """Named state codes per spec Ch 7."""
    WRITE_LOCK_CONTENTION = 0x01
    SEMANTIC_CONTRADICTION = 0x02
    STALE_STATE_DRIFT = 0x03


class LockContentionError(Exception):
    """Raised when write lock contention occurs (0x01)."""
    def __init__(self, path: str):
        self.path = path
        self.code = StateCode.WRITE_LOCK_CONTENTION
        super().__init__(f"Write lock contention on path: {path}")


class SemanticContradictionError(Exception):
    """Raised when semantic contradiction detected (0x02)."""
    def __init__(self, path: str, existing_value, new_value):
        self.path = path
        self.existing_value = existing_value
        self.new_value = new_value
        self.code = StateCode.SEMANTIC_CONTRADICTION
        super().__init__(f"Semantic contradiction on {path}: {existing_value} vs {new_value}")


class StaleStateDriftError(Exception):
    """Raised when stale state drift detected (0x03)."""
    def __init__(self, path: str):
        self.path = path
        self.code = StateCode.STALE_STATE_DRIFT
        super().__init__(f"Stale state drift on path: {path}")


@dataclass
class LockResult:
    """Result of attempting to acquire a lock."""
    acquired: bool
    state_code: StateCode = None
    message: str = ""


@dataclass
class LockConfig:
    """Configurable parameters for the lock manager."""
    default_timeout: float = 5.0
    default_max_retries: int = 3
    base_backoff: float = 0.01   # seconds; doubles per retry (exponential)
    max_jitter: float = 0.01     # seconds added randomly to backoff


class AsyncLockManager:
    """
    Path-based lock manager for concurrency control.

    Implements exponential backoff with jitter for contention (0x01).
    Ensures read-committed semantics - no partial writes visible.

    Lock granularity is currently path-based.  The LockConfig dataclass
    is designed so that adaptive granularity can be introduced later
    without changing the public API.
    """

    def __init__(self, config: LockConfig = None):
        self._config = config or LockConfig()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_creation_lock = asyncio.Lock()
    
    async def _get_lock(self, path: str) -> asyncio.Lock:
        """Get or create a lock for the given path."""
        if path in self._locks:
            return self._locks[path]
        
        async with self._lock_creation_lock:
            # Double-check after acquiring creation lock
            if path not in self._locks:
                self._locks[path] = asyncio.Lock()
            return self._locks[path]
    
    async def acquire_write_lock(
        self,
        path: str,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> LockResult:
        """
        Acquire write lock with exponential backoff on contention.

        Per spec Ch 7: On 0x01 (contention), queue the second writer with
        exponential backoff + jitter.  Do not error unless timeout exhausted.

        Args:
            path: The assertion path to lock.
            timeout: Maximum time to wait for lock (defaults to config value).
            max_retries: Maximum retry attempts (defaults to config value).

        Returns:
            LockResult indicating success or failure.
        """
        import random

        lock = await self._get_lock(path)

        effective_timeout = timeout if timeout is not None else self._config.default_timeout
        effective_retries = max_retries if max_retries is not None else self._config.default_max_retries

        for attempt in range(effective_retries):
            try:
                acquired = await asyncio.wait_for(
                    lock.acquire(),
                    timeout=effective_timeout / effective_retries,
                )
                if acquired or lock.locked():
                    return LockResult(acquired=True)
            except asyncio.TimeoutError:
                if attempt < effective_retries - 1:
                    jitter = random.uniform(0, self._config.max_jitter)
                    backoff = (self._config.base_backoff * (2 ** attempt)) + jitter
                    await asyncio.sleep(backoff)
                    continue
                return LockResult(
                    acquired=False,
                    state_code=StateCode.WRITE_LOCK_CONTENTION,
                    message=f"Failed to acquire lock on {path} after {effective_retries} attempts",
                )

        return LockResult(acquired=True)
    
    async def release_write_lock(self, path: str):
        """Release write lock for the given path."""
        if path in self._locks:
            lock = self._locks[path]
            if lock.locked():
                lock.release()
    
    def is_locked(self, path: str) -> bool:
        """Check if a path is currently locked."""
        if path not in self._locks:
            return False
        return self._locks[path].locked()


# Global lock manager instance
_lock_manager = AsyncLockManager()


def get_lock_manager() -> AsyncLockManager:
    """Get the global lock manager instance."""
    return _lock_manager
