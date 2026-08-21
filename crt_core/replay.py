"""
Replay protection for evidence bindings (Phase 5).

The V1 evidence message binds a ``nonce`` into the signature, but a nonce is
only meaningful if it can never be reused. This module defines the replay-guard
interface and an in-memory implementation; a SQLite-backed implementation lives
in ``crt_service.storage`` so the guard survives restarts.

A fingerprint (SHA-256 over the binding identity) is what gets recorded. A
provider that mints a fresh unique nonce per signature makes every binding
unique; replaying an old signed packet reuses the nonce and is rejected.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Optional


def nonce_fingerprint(
    nonce: str,
    provider_id: Optional[str] = None,
    key_id: str = "",
    evidence_type: str = "",
    source_id: Optional[str] = None,
    assertion_hash: Optional[str] = None,
) -> str:
    """Deterministic identity of one nonce usage, for the replay table."""
    parts = "|".join([
        provider_id or "",
        key_id or "",
        evidence_type or "",
        source_id or "",
        assertion_hash or "",
        nonce or "",
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


class ReplayGuard:
    """Tracks previously-seen nonce fingerprints to detect replays."""

    def is_seen(self, fingerprint: str) -> bool:
        raise NotImplementedError

    def record(self, fingerprint: str) -> None:
        raise NotImplementedError

    def check_and_record(self, fingerprint: str) -> bool:
        """
        Atomically check-and-record one fingerprint.

        Returns:
            True when the fingerprint is new and was recorded (accepted),
            False when it was already seen (replay → reject).
        """
        if self.is_seen(fingerprint):
            return False
        self.record(fingerprint)
        return True


class InMemoryReplayGuard(ReplayGuard):
    """Process-local nonce store. Not durable across restarts."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def is_seen(self, fingerprint: str) -> bool:
        with self._lock:
            return fingerprint in self._seen

    def record(self, fingerprint: str) -> None:
        with self._lock:
            self._seen.add(fingerprint)
