"""
Adapter fail-closed evidence labeling tests.

Elevated source types (database/tool_output/document/user_input) are honored
only when backed by a valid Ed25519 evidence signature; otherwise they are
recorded as agent_claim_default.
"""

from datetime import datetime

import pytest

from agents.lcm_adapter import (
    AutoGenLCMAdapter,
    MemoryWrite,
    memory_write_needs_signature,
    sign_memory_write,
)

NOW = datetime(2026, 7, 14, 10, 0, 0)


def test_agent_claim_needs_no_signature():
    assert memory_write_needs_signature("agent_claim") is False
    assert memory_write_needs_signature("agent_claim_default") is False


def test_elevated_types_require_signature():
    for src in ["database", "db", "tool_output", "tool", "document", "doc", "user_input"]:
        assert memory_write_needs_signature(src) is True, src


def test_unknown_source_type_treated_as_agent_claim():
    assert memory_write_needs_signature("made_up_source") is False


def test_signed_elevated_write_keeps_authority():
    adapter = AutoGenLCMAdapter()
    w = MemoryWrite(agent_id="db", key="age", value=30,
                    source_type="database", timestamp=NOW)
    w.evidence_signature = sign_memory_write(w)
    adapter.write(w)
    prov = adapter.memory["age"].provenance_info
    assert prov.source_type == "database"
    assert prov.authority_score == 0.9


def test_unsigned_elevated_write_is_degraded():
    adapter = AutoGenLCMAdapter()
    with pytest.warns(UserWarning):
        adapter.write(MemoryWrite(agent_id="db", key="age", value=30,
                                  source_type="database", timestamp=NOW))
    prov = adapter.memory["age"].provenance_info
    assert prov.source_type == "agent_claim_default"
    assert prov.authority_score == 0.3
    assert prov.verified_confidence == 0.3


def test_adapter_ignores_reported_confidence_for_unsupported_claims():
    """Entry-point parity (adapter): an unsupported claim's self-reported
    confidence is audit-only. 0.01 and 0.99 must yield the same
    verified_confidence (0.3), exactly like the core pipeline path."""
    lo = AutoGenLCMAdapter()
    hi = AutoGenLCMAdapter()
    lo.write(MemoryWrite(agent_id="a", key="k", value=1,
                         source_type="agent_claim", timestamp=NOW, confidence_score=0.01))
    hi.write(MemoryWrite(agent_id="a", key="k", value=1,
                         source_type="agent_claim", timestamp=NOW, confidence_score=0.99))
    assert (lo.memory["k"].provenance_info.verified_confidence
            == hi.memory["k"].provenance_info.verified_confidence == 0.3)


def test_signature_is_bound_to_content():
    """A signature for one value must not verify for a different value."""
    w = MemoryWrite(agent_id="db", key="age", value=30,
                    source_type="database", timestamp=NOW)
    sig = sign_memory_write(w)

    tampered = MemoryWrite(agent_id="db", key="age", value=31,
                           source_type="database", timestamp=NOW)
    tampered.evidence_signature = sig

    adapter = AutoGenLCMAdapter()
    with pytest.warns(UserWarning):
        adapter.write(tampered)
    assert adapter.memory["age"].provenance_info.source_type == "agent_claim_default"


def test_signature_is_bound_to_key():
    w = MemoryWrite(agent_id="db", key="age", value=30,
                    source_type="database", timestamp=NOW)
    sig = sign_memory_write(w)

    moved = MemoryWrite(agent_id="db", key="other_path", value=30,
                        source_type="database", timestamp=NOW)
    moved.evidence_signature = sig

    adapter = AutoGenLCMAdapter()
    with pytest.warns(UserWarning):
        adapter.write(moved)
    assert adapter.memory["other_path"].provenance_info.source_type == "agent_claim_default"


def test_low_authority_write_needs_no_signature_roundtrip():
    adapter = AutoGenLCMAdapter()
    adapter.write(MemoryWrite(agent_id="llm", key="k", value="v",
                              source_type="agent_claim", timestamp=NOW))
    prov = adapter.memory["k"].provenance_info
    assert prov.source_type == "agent_claim"
    assert prov.authority_score == 0.3
