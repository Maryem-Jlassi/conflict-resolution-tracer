"""
Unit tests — UMF schema and provenance stamping

Tests validation rules, rejection cases, and provenance stamping in isolation.
"""

import pytest
from datetime import datetime

from lcm_core.provenance import validate_and_stamp, RejectionError
from lcm_core.schema import StampedUMF
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.crypto import sign_evidence_message


# ---------------------------------------------------------------------------
# Acceptance — well-formed packets
# ---------------------------------------------------------------------------


def test_valid_packet_returns_stamped_umf():
    raw = {
        "agent_id": "triage_bot",
        "session_id": "s1",
        "timestamp": datetime(2026, 7, 14, 10, 0, 0),
        "confidence_score": 0.85,
        "assertion_payload": {"patient.temperature_c": 38.5},
    }
    result = validate_and_stamp(raw)
    assert isinstance(result, StampedUMF)
    assert result.agent_id == "triage_bot"
    assert result.provenance_id is not None
    assert result.ingested_at is not None


def test_optional_media_fields_preserved():
    raw = {
        "agent_id": "img_agent",
        "session_id": "s2",
        "timestamp": datetime(2026, 7, 14, 10, 0, 0),
        "confidence_score": 0.9,
        "assertion_payload": {"scan.result": "clear"},
        "media_uri": "s3://bucket/scan.dcm",
        "media_hash": "a" * 64,
    }
    result = validate_and_stamp(raw)
    assert result.media_uri == "s3://bucket/scan.dcm"
    assert result.media_hash == "a" * 64


def test_stamped_umf_is_frozen():
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.7,
        "assertion_payload": {"k": "v"},
    }
    result = validate_and_stamp(raw)
    with pytest.raises(Exception):
        result.provenance_id = "hacked"


# ---------------------------------------------------------------------------
# Rejection — missing or invalid fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["agent_id", "session_id", "timestamp", "confidence_score", "assertion_payload"],
)
def test_missing_required_field_rejected(missing_field):
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.8,
        "assertion_payload": {"k": "v"},
    }
    del raw[missing_field]
    with pytest.raises(RejectionError):
        validate_and_stamp(raw)


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.1, 2.0])
def test_confidence_out_of_range_rejected(bad_confidence):
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": bad_confidence,
        "assertion_payload": {"k": "v"},
    }
    with pytest.raises(RejectionError):
        validate_and_stamp(raw)


def test_empty_payload_rejected():
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.8,
        "assertion_payload": {},
    }
    with pytest.raises(RejectionError, match="payload"):
        validate_and_stamp(raw)


def test_non_dict_payload_rejected():
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.8,
        "assertion_payload": "a string is not a dict",
    }
    with pytest.raises(RejectionError):
        validate_and_stamp(raw)


def test_plain_string_rejected():
    with pytest.raises(RejectionError):
        validate_and_stamp("just a string")


# ---------------------------------------------------------------------------
# Provenance stamping — evidence changes verified_confidence
# ---------------------------------------------------------------------------


def test_no_evidence_uses_agent_claim_score():
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.77,
        "assertion_payload": {"k": "v"},
    }
    result = validate_and_stamp(raw)
    assert result.provenance_info.verified_confidence == 0.3


def test_database_evidence_raises_verified_confidence():
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.3,
        "assertion_payload": {"k": "v"},
    }
    # A valid evidence_signature is required for database/document/tool_output
    # evidence; without one the middleware degrades authority to 0.1.
    ev = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=1.0)
    result = validate_and_stamp(
        raw,
        evidence_records=[ev],
        evidence_signature=sign_evidence_message(EvidenceType.DATABASE, None),
    )
    assert result.provenance_info.verified_confidence > 0.3


@pytest.mark.parametrize("reported", [0.01, 0.99])
def test_reported_confidence_never_drives_verified_confidence(reported):
    """Phase 2: an unsupported claim's self-reported confidence is audit-only.

    Two otherwise-identical packets that differ ONLY in confidence_score must
    produce the same verified_confidence — the raw LLM self-report is trivially
    forgeable and must not elevate evidence or influence admission decisions.
    """
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": reported,
        "assertion_payload": {"k": "v"},
    }
    low = validate_and_stamp({**raw, "confidence_score": 0.01})
    high = validate_and_stamp({**raw, "confidence_score": 0.99})
    assert low.provenance_info.verified_confidence == high.provenance_info.verified_confidence
    assert low.provenance_info.verified_confidence == 0.3
    # The alias reports the raw value untouched (for audit/display), never used
    # for resolution.
    assert low.reported_confidence == 0.01
    assert high.reported_confidence == 0.99


def test_unsigned_elevated_evidence_is_degraded():
    """Phase 2: external evidence without a valid signature must fail closed.

    A 'database' record with no evidence_signature must NOT elevate the claim;
    verified_confidence is forced down to the unverified fallback (0.1) and
    never above it — an agent cannot self-elevate by attaching evidence tags.
    """
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.99,
        "assertion_payload": {"k": "v"},
    }
    ev = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=1.0)
    result = validate_and_stamp(raw, evidence_records=[ev], evidence_signature=None)
    assert result.provenance_info.verified_confidence <= 0.1
    assert result.provenance_info.authority_score <= 0.1
    # The raw self-report is preserved for audit but never elevates anything.
    assert result.confidence_score == 0.99


@pytest.mark.parametrize("source_type", ["user_input", "database", "tool_output", "agent_claim"])
def test_authority_score_set_from_source(source_type):
    ev = EvidenceRecord(evidence_type=EvidenceType(source_type), relevance_score=1.0)
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.7,
        "assertion_payload": {"k": "v"},
    }
    result = validate_and_stamp(raw, evidence_records=[ev])
    assert result.provenance_info.authority_score is not None
    assert 0.0 < result.provenance_info.authority_score <= 1.0


# ---------------------------------------------------------------------------
# agent_claim_default provenance stamping (audit semantics)
# ---------------------------------------------------------------------------


def test_no_evidence_stamps_agent_claim_default():
    """When no evidence records are supplied, the stamped provenance must
    report source_type='agent_claim_default' (not 'agent_claim') so that
    experiment logs and the actual ProvenanceInfo agree."""
    raw = {
        "agent_id": "a",
        "session_id": "s",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.77,
        "assertion_payload": {"k": "v"},
    }
    stamped = validate_and_stamp(raw, evidence_records=None)
    assert stamped.provenance_info.source_type == "agent_claim_default"
    assert stamped.provenance_info.authority_score == 0.3
    assert stamped.provenance_info.verified_confidence == 0.3
