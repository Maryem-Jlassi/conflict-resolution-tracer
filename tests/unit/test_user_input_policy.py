"""
User-input policy tests (Phase 9).

Verifies that ``user_input`` — the highest-authority evidence tier — is treated
as *delegated* authority:
- unsigned user_input degrades to agent_claim_default (no silent authority 1.0)
- gateway-attested user_input keeps authority 1.0
- an explicit relay allowlist rejects (or degrades) non-delegated agents
- the wrapper no longer bypasses the crypto gate for user_input
"""

from datetime import datetime

import pytest

from tests.conftest import make_memory, make_evidence, REFERENCE_TIME
from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import sign_assertion_evidence, sign_evidence_message
from crt_core.provenance import RejectionError, validate_and_stamp, verify_evidence_signature
from crt_core.user_input_policy import (
    DEFAULT_USER_INPUT_POLICY,
    UserInputDecision,
    UserInputPolicy,
    get_user_input_policy,
    reset_user_input_policy,
    set_user_input_policy,
)


@pytest.fixture(autouse=True)
def _reset_policy():
    """Ensure the active user-input policy never leaks between tests."""
    yield
    reset_user_input_policy()


def _raw(agent="alice", payload=None):
    return {
        "agent_id": agent,
        "session_id": "sess",
        "timestamp": REFERENCE_TIME,
        "confidence_score": 0.8,
        "assertion_payload": payload or {"k": "v"},
    }


class TestPolicyObject:
    def test_default_requires_attestation(self):
        policy = DEFAULT_USER_INPUT_POLICY
        assert policy.require_attestation is True
        assert policy.allowed_relayers is None

    def test_attested_decision(self):
        d = DEFAULT_USER_INPUT_POLICY.evaluate("alice", signature_valid=True)
        assert d.accepted
        assert d.source_type == "user_input"
        assert d.authority_score == 1.0
        assert d.verified_confidence_cap is None

    def test_unattested_degrades_to_agent_claim_default(self):
        d = DEFAULT_USER_INPUT_POLICY.evaluate("alice", signature_valid=False)
        assert d.accepted
        assert d.source_type == "agent_claim_default"
        assert d.authority_score == 0.1
        assert d.verified_confidence_cap == 0.1
        assert "attestation" in d.reason

    def test_allowlist_rejects_unknown_relayer(self):
        policy = UserInputPolicy(allowed_relayers=("delegated-agent",))
        d = policy.evaluate("mallory", signature_valid=True)
        assert not d.accepted
        assert "delegation allowlist" in d.reason

    def test_allowlist_accepts_delegated_relayer(self):
        policy = UserInputPolicy(allowed_relayers=("delegated-agent",))
        d = policy.evaluate("delegated-agent", signature_valid=True)
        assert d.accepted
        assert d.source_type == "user_input"

    def test_allowlist_degrade_action(self):
        policy = UserInputPolicy(
            allowed_relayers=("delegated-agent",),
            unauthorized_relay_action="degrade",
        )
        d = policy.evaluate("mallory", signature_valid=True)
        assert d.accepted
        assert d.source_type == "agent_claim_default"
        assert d.authority_score == 0.1

    def test_policy_opt_out_allows_unattested(self):
        policy = UserInputPolicy(require_attestation=False)
        d = policy.evaluate("alice", signature_valid=False)
        assert d.accepted
        assert d.source_type == "user_input"


class TestValidateAndStampIntegration:
    @staticmethod
    def _sig(packet, source="user://x"):
        return sign_assertion_evidence(EvidenceType.USER_INPUT,source,
            agent_id=packet["agent_id"],timestamp=packet["timestamp"],
            assertion_payload=packet["assertion_payload"])
    def test_unsigned_user_input_degrades(self):
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        result = validate_and_stamp(_raw(), evidence_records=[ev], evidence_signature=None)
        assert result.provenance_info.source_type == "agent_claim_default"
        assert result.provenance_info.authority_score == 0.1
        assert result.provenance_info.verified_confidence <= 0.1
        # The raw self-report is preserved but never elevates anything.
        assert result.confidence_score == 0.8

    def test_signed_user_input_keeps_full_authority(self):
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        packet=_raw(); sig = self._sig(packet)
        result = validate_and_stamp(packet, evidence_records=[ev], evidence_signature=sig)
        assert result.provenance_info.source_type == "user_input"
        assert result.provenance_info.authority_score == 1.0
        assert result.provenance_info.verified_confidence == 0.75

    def test_wrong_signature_degrades(self):
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        packet=_raw(); sig = self._sig(packet,"user://other")
        with pytest.raises(RejectionError):
            validate_and_stamp(packet, evidence_records=[ev], evidence_signature=sig)

    def test_allowlist_rejects_unknown_relayer(self):
        set_user_input_policy(UserInputPolicy(allowed_relayers=("delegated-agent",)))
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        packet=_raw(agent="mallory"); sig = self._sig(packet)
        with pytest.raises(RejectionError):
            validate_and_stamp(packet, evidence_records=[ev], evidence_signature=sig)

    def test_allowlist_accepts_delegated_relayer(self):
        set_user_input_policy(UserInputPolicy(allowed_relayers=("delegated-agent",)))
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        packet=_raw(agent="delegated-agent"); sig = self._sig(packet)
        result = validate_and_stamp(packet, evidence_records=[ev], evidence_signature=sig)
        assert result.provenance_info.source_type == "user_input"
        assert result.provenance_info.authority_score == 1.0

    def test_allowlist_degrades_when_configured(self):
        set_user_input_policy(UserInputPolicy(
            allowed_relayers=("delegated-agent",),
            unauthorized_relay_action="degrade",
        ))
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        packet=_raw(agent="mallory"); sig = self._sig(packet)
        result = validate_and_stamp(packet, evidence_records=[ev], evidence_signature=sig)
        assert result.provenance_info.source_type == "agent_claim_default"
        assert result.provenance_info.authority_score == 0.1

    def test_opt_out_policy_accepts_unattested(self):
        set_user_input_policy(UserInputPolicy(require_attestation=False))
        ev = EvidenceRecord(evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0)
        result = validate_and_stamp(_raw(), evidence_records=[ev], evidence_signature=None)
        assert result.provenance_info.source_type == "user_input"
        assert result.provenance_info.authority_score == 1.0


class TestWrapperGate:
    def test_user_input_no_longer_bypasses_crypto_gate(self):
        assert verify_evidence_signature(EvidenceType.USER_INPUT, "user://x", None) is False

    def test_user_input_with_valid_signature_passes(self):
        sig = sign_evidence_message(EvidenceType.USER_INPUT, "user://x")
        assert verify_evidence_signature(EvidenceType.USER_INPUT, "user://x", sig) is True

    def test_agent_claim_still_bypasses(self):
        assert verify_evidence_signature(EvidenceType.AGENT_CLAIM, "anything", None) is True
