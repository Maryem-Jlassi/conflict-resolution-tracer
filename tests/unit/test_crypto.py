"""
Unit tests for the cryptographic evidence-binding layer (lcm_core.crypto).

These tests confirm that evidence signatures are now genuine Ed25519
signatures and that the legacy forgeable ``sig_*`` / >=16-char token
placeholders are rejected -- the core of the signature-verification fix.
"""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from lcm_core.crypto import (
    EvidenceKeyConfigurationError,
    build_evidence_message,
    sign_evidence_for_records,
    sign_evidence_message,
    verify_evidence_signature_crypto,
)
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.provenance import verify_evidence_signature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _allow_dev_evidence_key(monkeypatch):
    """Opt into the development fallback key for all tests in this module.

    The production code path now fails closed unless LCM_ALLOW_DEV_EVIDENCE_KEY=1
    is explicitly set, so tests that sign/verify with the dev key must opt in.
    """
    monkeypatch.setenv("LCM_ALLOW_DEV_EVIDENCE_KEY", "1")


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ev_type", list(EvidenceType))
def test_sign_verify_round_trip(ev_type):
    """Every non-bypass evidence type must round-trip sign/verify.

    USER_INPUT is included since Phase 9 — it no longer bypasses the crypto
    gate (the provenance wrapper verifies user_input like any elevated source).
    Only pure agent_claim / agent_claim_default bypass the gate.
    """
    if ev_type in (EvidenceType.AGENT_CLAIM, EvidenceType.AGENT_CLAIM_DEFAULT):
        pytest.skip("AGENT_CLAIM bypasses the crypto gate")
    sig = sign_evidence_message(ev_type, "provider://src")
    assert verify_evidence_signature_crypto(ev_type, "provider://src", sig) is True


def test_valid_database_signature_accepted():
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://verified")
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://verified", sig)


# ---------------------------------------------------------------------------
# Forged placeholder tokens are now REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fake_sig",
    [
        "sig_abc123",                       # starts with sig_  (old rule)
        "sig_benchmark_e_verification",     # benchmark_e old token
        "1" * 16,                           # >= 16 chars       (old rule)
        "1" * 32,                           # >= 16 chars
        "sig_" + "1" * 32,                  # inspector_backend old token
    ],
)
def test_forged_placeholder_token_rejected(fake_sig):
    assert (
        verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://verified", fake_sig)
        is False
    )


def test_none_and_empty_rejected():
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://verified", None) is False
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://verified", "") is False


def test_tampered_source_rejected():
    """Signature over one source_id must NOT verify for a different source_id."""
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://verified")
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://evil", sig) is False


def test_tampered_type_rejected():
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://verified")
    assert verify_evidence_signature_crypto(EvidenceType.DOCUMENT, "db://verified", sig) is False


# ---------------------------------------------------------------------------
# content_hash binding
# ---------------------------------------------------------------------------

def test_content_hash_binding():
    """Signature is bound to (type, source, content_hash)."""
    no_hash = sign_evidence_message(EvidenceType.DATABASE, "db://x", None)
    with_hash = sign_evidence_message(EvidenceType.DATABASE, "db://x", "sha_deadbeef")

    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", no_hash, None)
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", with_hash, "sha_deadbeef")
    # Cross: no-hash sig must fail when a content_hash is present
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", no_hash, "sha_deadbeef") is False
    # Cross: with-hash sig must fail when content_hash is absent
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://x", with_hash, None) is False


# ---------------------------------------------------------------------------
# Wrapper bypass / contract
# ---------------------------------------------------------------------------

def test_wrapper_bypasses_signature_for_agent_claim():
    # No signature supplied, but agent_claim evidence is allowed through.
    assert verify_evidence_signature(EvidenceType.AGENT_CLAIM, "anything", None) is True


def test_wrapper_requires_real_sig_for_database():
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://x")
    assert verify_evidence_signature(EvidenceType.DATABASE, "db://x", sig) is True
    assert verify_evidence_signature(EvidenceType.DATABASE, "db://x", "sig_fake") is False


def test_wrapper_accepts_content_hash_param():
    """The wrapper forwards content_hash to the crypto verifier."""
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://x", "h")
    assert verify_evidence_signature(EvidenceType.DATABASE, "db://x", sig, content_hash="h") is True
    assert verify_evidence_signature(EvidenceType.DATABASE, "db://x", sig, content_hash="other") is False


# ---------------------------------------------------------------------------
# sign_evidence_for_records selects the best record
# ---------------------------------------------------------------------------

def test_sign_for_records_single_best():
    recs = [EvidenceRecord(evidence_type=EvidenceType.DATABASE, source_id="db://p", relevance_score=1.0)]
    sig = sign_evidence_for_records(recs)
    assert sig is not None
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://p", sig)


def test_sign_for_records_empty_returns_none():
    assert sign_evidence_for_records([]) is None


def test_sign_for_records_picks_highest_authority():
    """When records have mixed types, sign over the best one (authority x relevance)."""
    weak = EvidenceRecord(evidence_type=EvidenceType.AGENT_CLAIM, relevance_score=1.0)
    strong = EvidenceRecord(evidence_type=EvidenceType.DATABASE, source_id="db://strong", relevance_score=1.0)
    other = EvidenceRecord(evidence_type=EvidenceType.DOCUMENT, source_id="doc://other", relevance_score=1.0)
    recs = [weak, strong, other]
    sig = sign_evidence_for_records(recs)
    # authority*relevance: database(0.9) > document(0.75) > agent_claim(0.3)
    assert sig is not None
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://strong", sig)
    assert verify_evidence_signature_crypto(EvidenceType.DOCUMENT, "doc://other", sig) is False


# ---------------------------------------------------------------------------
# Canonical message stability
# ---------------------------------------------------------------------------

def test_message_is_deterministic_and_prefixed():
    msg = build_evidence_message(EvidenceType.DATABASE, "db://x", "ch", None, "")
    assert msg == b"lcm-evidence-binding/2|||database|db://x|ch||||"
    assert build_evidence_message(EvidenceType.DATABASE, "db://x", "ch", None, "") == msg


def test_message_normalises_none_and_empty():
    empty = build_evidence_message(EvidenceType.DATABASE, None, None, None, "")
    assert empty == b"lcm-evidence-binding/2|||database||||||"
    empty2 = build_evidence_message(EvidenceType.DATABASE, "", "", None, "")
    assert empty2 == b"lcm-evidence-binding/2|||database||||||"
    assert empty == empty2


# ---------------------------------------------------------------------------
# Environment override (production key path)
# ---------------------------------------------------------------------------

def test_env_var_public_key_overrides_dev_key(monkeypatch):
    """A custom provider keypair validates its own signature and rejects the
    dev key's signatures once LCM_EVIDENCE_PUBLIC_KEY is set."""
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    monkeypatch.setenv("LCM_EVIDENCE_PUBLIC_KEY", pub_hex)

    msg = build_evidence_message(EvidenceType.DATABASE, "db://external", None, None, "")
    custom_sig = base64.b64encode(priv.sign(msg)).decode()

    assert verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db://external", custom_sig, None
    ) is True
    # A dev-key signature must now be INVALID under the custom public key
    dev_sig = sign_evidence_message(EvidenceType.DATABASE, "db://external")
    assert verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db://external", dev_sig, None
    ) is False


def test_invalid_public_key_fails_closed(monkeypatch):
    """A malformed env value must NOT silently fall back to the dev key —
    a misconfigured provider key should fail closed (authority degrades to
    unverified) rather than accept dev-key signatures in a deployment that
    explicitly configured a provider key."""
    monkeypatch.setenv("LCM_EVIDENCE_PUBLIC_KEY", "not-a-real-key")
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://verified")
    assert verify_evidence_signature_crypto(EvidenceType.DATABASE, "db://verified", sig) is False


# ---------------------------------------------------------------------------
# Signature shape
# ---------------------------------------------------------------------------

def test_signature_is_base64_of_64_bytes():
    sig = sign_evidence_message(EvidenceType.DATABASE, "db://x")
    raw = base64.b64decode(sig, validate=True)
    assert len(raw) == 64  # Ed25519 signatures are 64 bytes


def test_evidence_type_is_exported():
    """Sanity: crypto module public API surface."""
    import lcm_core.crypto as crypto
    for name in [
        "build_evidence_message",
        "verify_evidence_signature_crypto",
        "sign_evidence_message",
        "sign_evidence_for_records",
    ]:
        assert hasattr(crypto, name)
