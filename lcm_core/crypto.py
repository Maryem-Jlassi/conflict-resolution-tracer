"""
Cryptographic evidence-binding utilities.

A trusted Data Provider (database, tool service, document store) signs a
canonical message describing an evidence record with an Ed25519 private key.
The LCM middleware verifies that signature against the provider's Ed25519
public key before it will elevate the record's ``authority_score``.

This replaces the previous placeholder token check (any string starting with
``sig_`` or >= 16 chars was accepted), which could be trivially forged by an
agent. An agent does **not** possess the provider's private key, so a real
signature cannot be fabricated client-side -- only the trusted Data Provider
can produce one.

Signing message (canonical, V2)
-------------------------------
::

    lcm-evidence-binding/2|provider_id|key_id|evidence_type|source_id|evidence_hash|assertion_hash|issued_at|expires_at|nonce

Binding the signature to the assertion hash and a unique nonce prevents
signature reuse across unrelated claims that cite the same source.

Public-key configuration
------------------------
* Set ``LCM_EVIDENCE_PUBLIC_KEY`` to a hex/base64 Ed25519 public key for production.
* Set ``LCM_ALLOW_DEV_EVIDENCE_KEY=1`` to permit the built-in development fallback key.
* Without either, ``_load_public_key()`` raises ``EvidenceKeyConfigurationError`` and
  the middleware rejects external evidence (authority degrades to 0.1).
"""

from __future__ import annotations

import base64
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .confidence_engine import EvidenceRecord, EvidenceType, EVIDENCE_AUTHORITY
from .canonical import canonical_json
from .replay import InMemoryReplayGuard, ReplayGuard, nonce_fingerprint
from .metrics import get_metrics_registry


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EvidenceKeyConfigurationError(Exception):
    """Raised when evidence public-key configuration is missing or invalid in production."""
    pass


# ---------------------------------------------------------------------------
# Development fallback keypair (NOT for production use)
# ---------------------------------------------------------------------------

_DEV_SEED = hashlib.sha256(
    b"LCM development evidence-provider Ed25519 seed v1 (dev-only, NOT for production)"
).digest()


def _dev_private_key() -> Ed25519PrivateKey:
    """Return the development Ed25519 private key (tests/benchmarks only)."""
    return Ed25519PrivateKey.from_private_bytes(_DEV_SEED)


def dev_public_key_bytes() -> bytes:
    """Raw 32 bytes of the development public key (tests/benchmarks only)."""
    return _dev_private_key().public_key().public_bytes_raw()


def _parse_hex_or_b64(raw: str) -> Optional[bytes]:
    """Parse a hex- or base64-encoded 32-byte value, else None."""
    raw = raw.strip()
    try:
        data = bytes.fromhex(raw)
        if len(data) == 32:
            return data
    except ValueError:
        pass
    try:
        data = base64.b64decode(raw, validate=True)
        if len(data) == 32:
            return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Provider registry (multi-key support)
# ---------------------------------------------------------------------------

class EvidenceProviderRegistry:
    """
    Registry of trusted evidence providers and their Ed25519 public keys.

    Supports multiple providers, key rotation via key_id, key revocation,
    and auditability.
    """

    def __init__(self):
        self._providers: dict[str, dict[str, Ed25519PublicKey]] = {}

    def register_provider(self, provider_id: str, key_id: str, public_key_bytes: bytes) -> None:
        """Register a provider's public key."""
        if provider_id not in self._providers:
            self._providers[provider_id] = {}
        self._providers[provider_id][key_id] = Ed25519PublicKey.from_public_bytes(public_key_bytes)

    def get_public_key(self, provider_id: str, key_id: str) -> Optional[Ed25519PublicKey]:
        """Get a provider's public key by provider_id and key_id."""
        return self._providers.get(provider_id, {}).get(key_id)

    def revoke_key(self, provider_id: str, key_id: str) -> None:
        """Revoke a specific key for a provider."""
        if provider_id in self._providers and key_id in self._providers[provider_id]:
            del self._providers[provider_id][key_id]

    def list_providers(self) -> List[str]:
        """List all registered provider IDs."""
        return list(self._providers.keys())


# Global provider registry instance
_provider_registry = EvidenceProviderRegistry()


def get_provider_registry():
    """Return the active evidence provider registry."""
    return _provider_registry


def set_provider_registry(registry) -> None:
    """Install a different provider registry (e.g. SQLite-backed, persisted).

    The registry must expose ``register_provider``, ``get_public_key``,
    ``revoke_key`` and ``list_providers`` (the same interface as
    :class:`EvidenceProviderRegistry`).
    """
    global _provider_registry
    _provider_registry = registry


# ---------------------------------------------------------------------------
# Replay guard (Phase 5)
# ---------------------------------------------------------------------------

# Module-level replay guard. Defaults to in-memory; deployments should install a
# SQLite-backed guard (lcm_service.storage.SQLiteReplayGuard) for durability.
_replay_guard: ReplayGuard = InMemoryReplayGuard()


def get_replay_guard() -> ReplayGuard:
    """Return the active nonce replay guard."""
    return _replay_guard


def set_replay_guard(guard: ReplayGuard) -> None:
    """Install a different replay guard (e.g. a SQLite-backed one)."""
    global _replay_guard
    _replay_guard = guard


def reset_replay_guard() -> None:
    """Restore the process-local in-memory replay guard (tests)."""
    global _replay_guard
    _replay_guard = InMemoryReplayGuard()


def check_evidence_nonce(
    nonce: str,
    provider_id: Optional[str] = None,
    key_id: str = "",
    evidence_type: str = "",
    source_id: Optional[str] = None,
    assertion_hash: Optional[str] = None,
) -> bool:
    """
    Check-and-record one evidence nonce against the active replay guard.

    Returns:
        True when the nonce is new (accepted), False when it was already seen
        (a replayed signature → reject).
    """
    fp = nonce_fingerprint(
        nonce,
        provider_id=provider_id,
        key_id=key_id,
        evidence_type=evidence_type,
        source_id=source_id,
        assertion_hash=assertion_hash,
    )
    return _replay_guard.check_and_record(fp)


# ---------------------------------------------------------------------------
# Public-key resolution (production-safe, fail-closed)
# ---------------------------------------------------------------------------

def _load_public_key(provider_id: Optional[str] = None, key_id: str = "current") -> Ed25519PublicKey:
    """
    Resolve the Ed25519 public key used to verify evidence signatures.

    Priority:
      1. Provider registry (if provider_id is given and registered)
      2. ``LCM_EVIDENCE_PUBLIC_KEY`` env var (hex or base64, 32 raw bytes)
      3. Dev fallback key (ONLY if LCM_ALLOW_DEV_EVIDENCE_KEY=1)

    Raises EvidenceKeyConfigurationError if no valid key is available.
    """
    # 1. Try provider registry first
    if provider_id:
        key = get_provider_registry().get_public_key(provider_id, key_id)
        if key:
            return key

    # 2. Try environment variable
    raw = os.environ.get("LCM_EVIDENCE_PUBLIC_KEY")
    if raw:
        data = _parse_hex_or_b64(raw)
        if data is None:
            # Configured key is malformed -> fail closed, do NOT silently fall back.
            raise EvidenceKeyConfigurationError(
                "LCM_EVIDENCE_PUBLIC_KEY is set but is not a valid Ed25519 public key."
            )
        try:
            return Ed25519PublicKey.from_public_bytes(data)
        except Exception:
            raise EvidenceKeyConfigurationError(
                "LCM_EVIDENCE_PUBLIC_KEY is set but is not a valid Ed25519 public key."
            )

    # 3. Dev fallback is NOT automatic. It requires an explicit opt-in switch.
    if os.environ.get("LCM_ALLOW_DEV_EVIDENCE_KEY") == "1":
        return _dev_private_key().public_key()

    # No valid key available - fail closed
    raise EvidenceKeyConfigurationError(
        "No valid evidence public key configured. "
        "Set LCM_EVIDENCE_PUBLIC_KEY, or LCM_ALLOW_DEV_EVIDENCE_KEY=1 for dev/test."
    )


# ---------------------------------------------------------------------------
# Temporal validity (Phase 4)
# ---------------------------------------------------------------------------

def parse_evidence_timestamp(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO-8601 timestamp used in an evidence binding, tolerating a
    trailing 'Z' (UTC) as well as explicit timezone offsets. Returns None for
    missing/unparseable values so callers can treat them as unconstrained.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.tzinfo is None:
        # Naive datetimes are interpreted as UTC to keep comparisons safe.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def evidence_temporal_status(
    issued_at: Optional[str],
    expires_at: Optional[str],
    reference_time: Optional[datetime] = None,
    clock_skew_tolerance_seconds: float = 0.0,
) -> str:
    """
    Classify an evidence binding's temporal validity.

    Returns one of:
      * ``"valid"``           — within [issued_at, expires_at] (or unconstrained).
      * ``"expired"``         — ``expires_at`` is in the past.
      * ``"not_yet_valid"``   — ``issued_at`` is in the future beyond tolerance.
      * ``"unconstrained"``   — neither bound is set.

    ``reference_time`` defaults to ``datetime.utcnow()``. A small positive
    ``clock_skew_tolerance_seconds`` tolerates signer/verifier clock skew for
    the ``issued_at`` (future) check; expiration is enforced strictly.
    """
    ref = reference_time or datetime.utcnow()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    parsed_exp = parse_evidence_timestamp(expires_at)
    if parsed_exp is not None and parsed_exp < ref:
        return "expired"

    parsed_iss = parse_evidence_timestamp(issued_at)
    if parsed_iss is not None and parsed_iss > ref:
        skew = timedelta(seconds=clock_skew_tolerance_seconds)
        if parsed_iss - ref > skew:
            return "not_yet_valid"

    if parsed_exp is None and parsed_iss is None:
        return "unconstrained"
    return "valid"


def evidence_is_expired(expires_at: Optional[str], reference_time: Optional[datetime] = None) -> bool:
    """True when ``expires_at`` is set and earlier than the reference time."""
    return evidence_temporal_status(None, expires_at, reference_time=reference_time) == "expired"


# ---------------------------------------------------------------------------
# Canonical signing message (V2)
# ---------------------------------------------------------------------------

_MESSAGE_PREFIX = "lcm-evidence-binding/2"


def _compute_assertion_hash(agent_id: str, timestamp: str, assertion_payload: dict) -> str:
    """Compute a stable SHA-256 hash over the assertion (claim) content.

    Uses the canonical JSON encoding (Phase 7) so the hash is independent of
    dict insertion order and Python runtime repr quirks.
    """
    parts = [agent_id, timestamp, canonical_json(assertion_payload)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def build_evidence_message(
    evidence_type: EvidenceType,
    source_id: Optional[str],
    evidence_hash: Optional[str],
    assertion_hash: Optional[str],
    issued_at: str,
    expires_at: str = "",
    nonce: str = "",
    provider_id: str = "",
    key_id: str = "",
) -> bytes:
    """
    Build the canonical byte message signed/verified for evidence binding (V2).

    Format::

        lcm-evidence-binding/2|provider_id|key_id|evidence_type|source_id|evidence_hash|assertion_hash|issued_at|expires_at|nonce

    Binding the signature to the assertion hash and a unique nonce prevents
    signature reuse across unrelated claims that cite the same source.
    """

    def _f(v: Optional[str]) -> str:
        return v if (v is not None and v != "") else ""

    msg = "|".join([
        _MESSAGE_PREFIX,
        _f(provider_id),
        _f(key_id),
        evidence_type.value,
        _f(source_id),
        _f(evidence_hash),
        _f(assertion_hash),
        _f(issued_at),
        _f(expires_at),
        _f(nonce),
    ])
    return msg.encode("utf-8")


# ---------------------------------------------------------------------------
# Public verification
# ---------------------------------------------------------------------------

def verify_evidence_signature_crypto(
    evidence_type: EvidenceType,
    source_id: Optional[str],
    evidence_signature: Optional[str],
    content_hash: Optional[str] = None,
    assertion_hash: Optional[str] = None,
    provider_id: Optional[str] = None,
    key_id: str = "",
    issued_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    nonce: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    clock_skew_tolerance_seconds: float = 0.0,
) -> bool:
    """
    Cryptographically verify an Ed25519 evidence signature (base64-encoded).

    Returns ``True`` only when ``evidence_signature`` is a valid base64-encoded
    Ed25519 signature over the canonical V2 evidence message, verifiable against
    the configured public key AND temporally valid at ``reference_time``.

    Temporal enforcement (Phase 4): the V2 message binds ``issued_at`` and
    ``expires_at`` into the signature, and verification now also enforces them.
    Expired evidence (``expires_at`` in the past) or evidence issued in the
    future beyond the clock-skew tolerance is treated as unverified.

    Any malformed token returns False, which causes the provenance layer to
    degrade authority_score to 0.1 (unverified claim). If no valid public key
    is configured, this also returns False (fail-closed).
    """
    if not evidence_signature or not isinstance(evidence_signature, str):
        return False
    _reg = get_metrics_registry()
    _reg.incr("evidence.verification.total")
    try:
        sig_bytes = base64.b64decode(evidence_signature, validate=True)
    except Exception:
        _reg.incr("evidence.signature_invalid")
        return False
    # Ed25519 signatures are exactly 64 bytes
    if len(sig_bytes) != 64:
        _reg.incr("evidence.signature_invalid")
        return False
    message = build_evidence_message(
        evidence_type=evidence_type,
        source_id=source_id,
        evidence_hash=content_hash,
        assertion_hash=assertion_hash,
        issued_at=issued_at or "",
        expires_at=expires_at or "",
        nonce=nonce or "",
        provider_id=provider_id or "",
        key_id=key_id,
    )
    try:
        _load_public_key(provider_id=provider_id, key_id=key_id).verify(sig_bytes, message)
    except InvalidSignature:
        _reg.incr("evidence.signature_invalid")
        return False
    except EvidenceKeyConfigurationError:
        # No valid key configured -> fail closed, treat as unverified
        _reg.incr("evidence.signature_invalid")
        return False
    except Exception:
        # Never let a crypto error crash the pipeline -- treat as unverified
        _reg.incr("evidence.signature_invalid")
        return False

    # Temporal enforcement: expired / not-yet-valid evidence fails closed.
    status = evidence_temporal_status(
        issued_at,
        expires_at,
        reference_time=reference_time,
        clock_skew_tolerance_seconds=clock_skew_tolerance_seconds,
    )
    if status in ("expired", "not_yet_valid"):
        _reg.incr("evidence.temporal_rejected")
        return False

    # Replay protection (Phase 5): a nonce that has already been consumed is a
    # replayed signature → reject.
    if nonce:
        if not check_evidence_nonce(
            nonce,
            provider_id=provider_id,
            key_id=key_id,
            evidence_type=evidence_type.value,
            source_id=source_id,
            assertion_hash=assertion_hash,
        ):
            _reg.incr("evidence.replay_rejected")
            return False

    return True


# ---------------------------------------------------------------------------
# Dev / test signing helpers
# ---------------------------------------------------------------------------

def sign_evidence_message(
    evidence_type: EvidenceType,
    source_id: Optional[str],
    content_hash: Optional[str] = None,
    assertion_hash: Optional[str] = None,
    provider_id: str = "",
    key_id: str = "",
    issued_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    nonce: Optional[str] = None,
) -> str:
    """
    Produce a base64 Ed25519 signature over the canonical V2 evidence message,
    using the development private key.

    Intended for tests, benchmarks and the trusted integration layer that acts
    as a Data Provider in the absence of a real provider key.

    ``issued_at`` / ``nonce`` default to empty strings so this helper stays
    symmetric with :func:`verify_evidence_signature_crypto`, which also supplies
    empty strings when those fields are not passed. The signature is still bound
    to the specific claim via ``assertion_hash``, which prevents reuse across
    unrelated claims that cite the same source.
    """
    return sign_evidence_message_with_key(
        _dev_private_key(),
        evidence_type, source_id,
        content_hash=content_hash,
        assertion_hash=assertion_hash,
        provider_id=provider_id,
        key_id=key_id,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
    )


def sign_evidence_message_with_key(
    private_key: Ed25519PrivateKey,
    evidence_type: EvidenceType,
    source_id: Optional[str],
    content_hash: Optional[str] = None,
    assertion_hash: Optional[str] = None,
    provider_id: str = "",
    key_id: str = "",
    issued_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    nonce: Optional[str] = None,
) -> str:
    """
    Sign the canonical V2 evidence message with a SPECIFIC Ed25519 private key.

    Used by production Data Providers holding real keys and by tests exercising
    key lifecycle (rotation/revocation). The message format is identical to
    :func:`sign_evidence_message`; verification uses the same canonical message.
    """
    message = build_evidence_message(
        evidence_type=evidence_type,
        source_id=source_id,
        evidence_hash=content_hash,
        assertion_hash=assertion_hash,
        issued_at=issued_at or "",
        expires_at=expires_at or "",
        nonce=nonce or "",
        provider_id=provider_id,
        key_id=key_id,
    )
    sig = private_key.sign(message)
    return base64.b64encode(sig).decode("ascii")


def sign_evidence_for_records(records: List[EvidenceRecord], assertion_hash: Optional[str] = None) -> Optional[str]:
    """
    Sign over the "best" evidence record (highest ``authority x relevance``),
    replicating the record selection performed by
    :func:`lcm_core.provenance.validate_and_stamp`.

    Returns ``None`` when there are no records (no signature required
    for agent_claim-only / unsigned evidence).
    """
    if not records:
        return None
    best = max(
        records,
        key=lambda r: EVIDENCE_AUTHORITY[r.evidence_type] * r.relevance_score,
    )
    return sign_evidence_message(
        best.evidence_type,
        best.source_id,
        content_hash=best.content_hash,
        assertion_hash=assertion_hash,
    )


@contextmanager
def benchmark_dev_evidence_key():
    """
    Context manager enabling the development evidence key ONLY for benchmark
    and test runs when no real production key is configured.

    Sets ``LCM_ALLOW_DEV_EVIDENCE_KEY=1`` only when ``LCM_EVIDENCE_PUBLIC_KEY``
    is not set, so a real deployment never silently falls back to the dev key.
    Restores (or removes) the prior environment value on exit.

    Example::

        from lcm_core.crypto import benchmark_dev_evidence_key
        with benchmark_dev_evidence_key():
            results = run_benchmark(...)
    """
    env_key = "LCM_ALLOW_DEV_EVIDENCE_KEY"
    had_real = bool(os.environ.get("LCM_EVIDENCE_PUBLIC_KEY"))
    prior = os.environ.get(env_key)
    try:
        if not had_real:
            os.environ[env_key] = "1"
        yield
    finally:
        if not had_real:
            if prior is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prior