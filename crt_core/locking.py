"""Concurrency control and coherence monitoring."""

import asyncio
import time
from datetime import datetime
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

    def lock_telemetry(self) -> dict:
        """Observation-only contention stats (Track 3.2).

        Returns:
            dict with contention_detected, contention_timeouts and the raw
            list of lock-wait durations in ms for writers queued behind an
            already-held lock.
        """
        waits = list(self._contention_waits_ms)
        waits.sort()
        n = len(waits)

        def q(p):
            return waits[min(n - 1, int(p * n))] if n else None

        return {
            "contention_detected": self._contention_detected,
            "contention_timeouts": self._contention_timeouts,
            "waits_ms": waits,
            "wait_p50_ms": q(0.50),
            "wait_p95_ms": q(0.95),
            "wait_p99_ms": q(0.99),
            "wait_max_ms": waits[-1] if n else None,
            "wait_count": n,
        }


@dataclass
class LockConfig:
    """Configuration for AsyncLockManager."""
    max_holders: int = 1
    default_timeout_s: float = 30.0


@dataclass
class LockResult:
    """Result of a lock acquisition attempt."""
    acquired: bool
    token: str = ""
    message: str = ""


class AsyncLockManager:
    """Simple in-memory async lock manager for path-level write locks."""

    def __init__(self, config: Optional[LockConfig] = None):
        self._config = config or LockConfig()
        self._locks: dict[str, str] = {}

    async def acquire_write_lock(self, path: str) -> LockResult:
        """Acquire a write lock for the given path."""
        if path in self._locks:
            return LockResult(acquired=False, message=f"Path '{path}' is already locked")
        token = str(id(path))
        self._locks[path] = token
        return LockResult(acquired=True, token=token, message="Lock acquired")

    async def release_write_lock(self, path: str, token: str) -> None:
        """Release a write lock for the given path."""
        if path in self._locks and self._locks[path] == token:
            del self._locks[path]


# Global lock manager instance
_lock_manager = AsyncLockManager()


def get_lock_manager() -> AsyncLockManager:
    """Get the global lock manager instance."""
    return _lock_manager
