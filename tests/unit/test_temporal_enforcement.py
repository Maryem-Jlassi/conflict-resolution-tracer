"""
Unit tests — Temporal enforcement of evidence bindings (Phase 4).

The V1 signing message binds ``issued_at`` / ``expires_at`` into the signature,
but historically those fields were never enforced. Phase 4 adds:

* ``evidence_temporal_status`` classification (unconstrained/valid/expired/not_yet_valid).
* Enforcement inside ``verify_evidence_signature_crypto``: expired evidence or
  evidence issued in the future fails verification → the provenance layer
  degrades authority to 0.1 (fail-closed).
* Expiry-aware verified confidence: a signed, high-authority evidence record
  whose binding has expired no longer elevates the claim.
"""

from datetime import datetime, timedelta

import pytest

from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import (
    evidence_is_expired,
    evidence_temporal_status,
    parse_evidence_timestamp,
    sign_evidence_message,
    verify_evidence_signature_crypto,
)
from crt_core.provenance import validate_and_stamp

REF = datetime(2026, 7, 14, 10, 0, 0)


@pytest.fixture(autouse=True)
def _allow_dev_evidence_key(monkeypatch):
    monkeypatch.setenv("CRT_ALLOW_DEV_EVIDENCE_KEY", "1")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestTemporalStatus:
    def test_unconstrained_when_no_bounds(self):
        assert evidence_temporal_status(None, None, reference_time=REF) == "unconstrained"
        assert evidence_temporal_status("", "", reference_time=REF) == "unconstrained"

    def test_expired_when_expires_at_in_past(self):
        status = evidence_temporal_status(None, _iso(REF - timedelta(seconds=1)), reference_time=REF)
        assert status == "expired"

    def test_not_yet_valid_when_issued_at_in_future(self):
        status = evidence_temporal_status(_iso(REF + timedelta(hours=1)), None, reference_time=REF)
        assert status == "not_yet_valid"

    def test_valid_within_window(self):
        status = evidence_temporal_status(
            _iso(REF - timedelta(hours=1)),
            _iso(REF + timedelta(hours=1)),
            reference_time=REF,
        )
        assert status == "valid"

    def test_clock_skew_tolerance_for_issued_at(self):
        """A tiny future skew (e.g. signer clock ahead) is tolerated."""
        status = evidence_temporal_status(
            _iso(REF + timedelta(seconds=10)),
            None,
            reference_time=REF,
            clock_skew_tolerance_seconds=60,
        )
        assert status == "valid"

    def test_expiration_is_strict_regardless_of_tolerance(self):
        """Clock-skew tolerance must NEVER resurrect expired evidence."""
        status = evidence_temporal_status(
            None,
            _iso(REF - timedelta(seconds=1)),
            reference_time=REF,
            clock_skew_tolerance_seconds=60,
        )
        assert status == "expired"

    def test_parse_handles_z_suffix(self):
        dt = parse_evidence_timestamp("2026-07-14T10:00:00Z")
        assert dt is not None and dt.hour == 10
        assert parse_evidence_timestamp(None) is None
        assert parse_evidence_timestamp("not-a-date") is None

    def test_evidence_is_expired_helper(self):
        assert evidence_is_expired(_iso(REF - timedelta(days=1)), reference_time=REF)
        assert not evidence_is_expired(_iso(REF + timedelta(days=1)), reference_time=REF)
        assert not evidence_is_expired(None, reference_time=REF)


class TestTemporalEnforcementInVerify:
    def test_expired_signature_rejected(self):
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db://x",
            issued_at=_iso(REF - timedelta(hours=2)),
            expires_at=_iso(REF - timedelta(hours=1)),
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            issued_at=_iso(REF - timedelta(hours=2)),
            expires_at=_iso(REF - timedelta(hours=1)),
            reference_time=REF,
        ) is False

    def test_future_issued_signature_rejected(self):
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db://x",
            issued_at=_iso(REF + timedelta(days=1)),
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            issued_at=_iso(REF + timedelta(days=1)),
            reference_time=REF,
        ) is False

    def test_valid_window_signature_accepted(self):
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db://x",
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
            reference_time=REF,
        ) is True

    def test_signature_bound_to_expiry_fields(self):
        """A signature minted with one expiry window must not verify for another."""
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db://x",
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=2)),  # tampered window
            reference_time=REF,
        ) is False


class TestExpiryAwareVerifiedConfidence:
    def _signature(self,ev):
        from crt_core.crypto import sign_assertion_evidence
        packet=self._raw()
        return sign_assertion_evidence(EvidenceType.DATABASE,None,
            agent_id=packet["agent_id"],timestamp=packet["timestamp"],assertion_payload=packet["assertion_payload"],
            issued_at=ev.issued_at,expires_at=ev.expires_at)
    def _raw(self):
        return {
            "agent_id": "a",
            "session_id": "s",
            "timestamp": REF,
            "confidence_score": 0.9,
            "assertion_payload": {"k": "v"},
        }

    def test_valid_signed_evidence_elevates(self):
        ev = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            relevance_score=1.0,
            issued_at=_iso(REF - timedelta(hours=1)),
            expires_at=_iso(REF + timedelta(hours=1)),
        )
        result = validate_and_stamp(
            self._raw(),
            evidence_records=[ev],
            evidence_signature=self._signature(ev),
            reference_time=REF,
        )
        assert result.provenance_info.verified_confidence > 0.3
        assert result.provenance_info.authority_score == pytest.approx(0.9)

    def test_expired_signed_evidence_is_degraded(self):
        """Even with a cryptographically valid signature, an expired binding
        must fail closed and NOT elevate the claim."""
        ev = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            relevance_score=1.0,
            issued_at=_iso(REF - timedelta(hours=2)),
            expires_at=_iso(REF - timedelta(hours=1)),
        )
        from crt_core.provenance import RejectionError
        with pytest.raises(RejectionError):
            validate_and_stamp(self._raw(),evidence_records=[ev],
                evidence_signature=self._signature(ev),reference_time=REF)
