"""
Unit tests — Replay protection for evidence bindings (Phase 5).

The V2 signing message binds a ``nonce``, and Phase 5 actually ENFORCES it:
a nonce may be consumed exactly once. Reusing a signed packet (same nonce,
same binding) is rejected, and the consumed nonces persist in a SQLite
``evidence_nonces`` replay table so protection survives restarts.
"""

from datetime import datetime, timedelta

import pytest

from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.crypto import (
    get_replay_guard,
    reset_replay_guard,
    set_replay_guard,
    sign_evidence_message,
    verify_evidence_signature_crypto,
)
from lcm_core.provenance import validate_and_stamp
from lcm_core.replay import InMemoryReplayGuard, nonce_fingerprint
from lcm_service.storage import SQLiteReplayGuard, SQLiteStorage

REF = datetime(2026, 7, 14, 10, 0, 0)


@pytest.fixture(autouse=True)
def _dev_key_and_clean_guard(monkeypatch):
    """Opt into the dev key AND give every test a clean in-memory replay guard."""
    monkeypatch.setenv("LCM_ALLOW_DEV_EVIDENCE_KEY", "1")
    reset_replay_guard()
    yield
    reset_replay_guard()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Fingerprint & in-memory guard
# ---------------------------------------------------------------------------


class TestNonceFingerprint:
    def test_deterministic(self):
        a = nonce_fingerprint("n1", provider_id="p", key_id="k",
                              evidence_type="database", source_id="s", assertion_hash="h")
        b = nonce_fingerprint("n1", provider_id="p", key_id="k",
                              evidence_type="database", source_id="s", assertion_hash="h")
        assert a == b

    def test_differs_on_any_field(self):
        base = dict(nonce="n1", provider_id="p", key_id="k",
                    evidence_type="database", source_id="s", assertion_hash="h")
        for field in ("nonce", "provider_id", "key_id", "evidence_type", "source_id", "assertion_hash"):
            variant = dict(base)
            variant[field] = variant[field] + "-X"
            assert nonce_fingerprint(**variant) != nonce_fingerprint(**base), field


class TestInMemoryReplayGuard:
    def test_first_seen_accepted_second_rejected(self):
        g = InMemoryReplayGuard()
        assert g.check_and_record("fp-1") is True
        assert g.check_and_record("fp-1") is False
        assert g.check_and_record("fp-2") is True

    def test_is_seen(self):
        g = InMemoryReplayGuard()
        assert g.is_seen("fp") is False
        g.record("fp")
        assert g.is_seen("fp") is True


# ---------------------------------------------------------------------------
# SQLite replay table
# ---------------------------------------------------------------------------


class TestSQLiteReplayTable:
    def test_record_and_check(self):
        s = SQLiteStorage(":memory:")
        assert s.check_and_record_nonce("fp", provider_id="p", key_id="k", nonce="n") is True
        assert s.check_and_record_nonce("fp", provider_id="p", key_id="k", nonce="n") is False
        assert s.check_and_record_nonce("fp2", provider_id="p", key_id="k", nonce="n2") is True

    def test_durable_across_connections(self, tmp_path):
        """Consumed nonces survive in a file-backed DB across instances."""
        db = tmp_path / "lcm.db"
        s1 = SQLiteStorage(str(db))
        s1.record_nonce("fp", provider_id="p", key_id="k", nonce="n")
        s2 = SQLiteStorage(str(db))
        assert s2.nonce_exists("fp") is True
        assert s2.check_and_record_nonce("fp", provider_id="p", key_id="k", nonce="n") is False

    def test_sqlite_replay_guard_adapter(self, tmp_path):
        db = tmp_path / "lcm2.db"
        storage = SQLiteStorage(str(db))
        guard = SQLiteReplayGuard(storage)
        assert guard.check_and_record("fp") is True
        assert guard.check_and_record("fp") is False


# ---------------------------------------------------------------------------
# Crypto-level nonce enforcement
# ---------------------------------------------------------------------------


class TestCryptoNonceEnforcement:
    def _signed(self, nonce: str):
        return sign_evidence_message(
            EvidenceType.DATABASE, "db://x", nonce=nonce,
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
        )

    def test_same_nonce_rejected_on_replay(self):
        sig = self._signed("nonce-1")
        kw = dict(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://x",
            evidence_signature=sig,
            nonce="nonce-1",
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
            reference_time=REF,
        )
        assert verify_evidence_signature_crypto(**kw) is True
        # Same nonce, same binding → replay → rejected.
        assert verify_evidence_signature_crypto(**kw) is False

    def test_fresh_nonce_accepted(self):
        for nonce in ("n-a", "n-b", "n-c"):
            sig = self._signed(nonce)
            assert verify_evidence_signature_crypto(
                EvidenceType.DATABASE, "db://x", sig, nonce=nonce,
                issued_at=_iso(REF - timedelta(hours=1)),
                expires_at=_iso(REF + timedelta(hours=1)),
                reference_time=REF,
            ) is True

    def test_guard_switch_affects_verifier(self):
        """Installing a fresh guard clears replay state (used by reset flows)."""
        sig = self._signed("nonce-rep")
        kw = dict(
            evidence_type=EvidenceType.DATABASE, source_id="db://x",
            evidence_signature=sig, nonce="nonce-rep",
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
            reference_time=REF,
        )
        assert verify_evidence_signature_crypto(**kw) is True
        assert verify_evidence_signature_crypto(**kw) is False
        set_replay_guard(InMemoryReplayGuard())
        assert verify_evidence_signature_crypto(**kw) is True

    def test_no_nonce_no_replay_check(self):
        """Signatures without a nonce are not replay-checked (compat)."""
        sig = sign_evidence_message(EvidenceType.DATABASE, "db://x")
        assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", sig) is True
        assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", sig) is True

    def test_guard_is_exposed(self):
        assert isinstance(get_replay_guard(), InMemoryReplayGuard)


# ---------------------------------------------------------------------------
# Provenance-level: replayed evidence degrades verified confidence
# ---------------------------------------------------------------------------


class TestProvenanceReplay:
    def _raw(self):
        return {
            "agent_id": "a",
            "session_id": "s",
            "timestamp": REF,
            "confidence_score": 0.9,
            "assertion_payload": {"k": "v"},
        }

    def _record(self, nonce: str):
        return EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            relevance_score=1.0,
            nonce=nonce,
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
        )

    def test_first_binding_elevates_replay_degrades(self):
        rec = self._record("prov-nonce-1")
        sig = sign_evidence_message(
            EvidenceType.DATABASE, None, nonce=rec.nonce,
            issued_at=rec.issued_at, expires_at=rec.expires_at,
        )
        first = validate_and_stamp(
            self._raw(), evidence_records=[rec], evidence_signature=sig,
            reference_time=REF,
        )
        assert first.provenance_info.verified_confidence > 0.3

        replayed = validate_and_stamp(
            self._raw(), evidence_records=[rec], evidence_signature=sig,
            reference_time=REF,
        )
        # Replayed signature → fail closed.
        assert replayed.provenance_info.verified_confidence <= 0.1

    def test_distinct_nonce_still_elevates(self):
        for i in range(2):
            rec = self._record(f"prov-nonce-{i}")
            sig = sign_evidence_message(
                EvidenceType.DATABASE, None, nonce=rec.nonce,
                issued_at=rec.issued_at, expires_at=rec.expires_at,
            )
            result = validate_and_stamp(
                self._raw(), evidence_records=[rec], evidence_signature=sig,
                reference_time=REF,
            )
            assert result.provenance_info.verified_confidence > 0.3
