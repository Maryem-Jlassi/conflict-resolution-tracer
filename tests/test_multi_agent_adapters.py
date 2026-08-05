"""
Multi-agent adapter tests — essential integration points only.

Full parametrized evidence-priority coverage is in test_parametrized_memory.py.
Full cross-framework scenarios are in scenarios/test_cross_framework.py.
These tests verify the adapter layer itself: conflict logging, same-agent
overwrite, and trust tracking.
"""

import pytest
from datetime import datetime, timedelta

from agents.lcm_adapter import (
    AutoGenLCMAdapter,
    LangGraphLCMAdapter,
    MemoryWrite,
    memory_write_needs_signature,
    sign_memory_write,
)

NOW = datetime.utcnow()


@pytest.fixture(autouse=True)
def _dev_evidence_key():
    """Enable the dev evidence key so signed elevated writes verify (fail-closed otherwise)."""
    from lcm_core.crypto import benchmark_dev_evidence_key
    with benchmark_dev_evidence_key():
        yield


def _write(timestamp=NOW, **kwargs):
    """Build a MemoryWrite, auto-signing elevated source types."""
    w = MemoryWrite(timestamp=timestamp, **kwargs)
    if memory_write_needs_signature(w.source_type):
        w.evidence_signature = sign_memory_write(w)
    return w


@pytest.fixture
def adapter():
    return AutoGenLCMAdapter()


def test_first_write_no_conflict(adapter):
    conflict = adapter.write(_write(
        agent_id="a", key="fact", value="value",
        source_type="database",
    ))
    assert conflict is None
    assert adapter.read("fact") == "value"


def test_same_agent_overwrite_no_conflict(adapter):
    adapter.write(_write(agent_id="a", key="k", value="v1",
                         source_type="agent_claim"))
    conflict = adapter.write(_write(agent_id="a", key="k", value="v2",
                                    source_type="agent_claim"))
    assert conflict is None
    assert adapter.read("k") == "v2"


def test_database_beats_agent_claim_regardless_of_confidence(adapter):
    adapter.write(_write(agent_id="db_agent", key="age", value=30,
                         source_type="database", confidence_score=0.7))
    conflict = adapter.write(_write(agent_id="llm_agent", key="age", value=35,
                                    source_type="agent_claim", confidence_score=0.95))
    assert conflict is not None
    assert conflict.resolution == "existing_won"
    assert adapter.read("age") == 30


def test_unsigned_elevated_source_type_is_degraded(adapter):
    """An agent cannot self-assign elevated authority without a valid signature."""
    adapter.write(MemoryWrite(agent_id="db_agent", key="age", value=30,
                              source_type="database", timestamp=NOW,
                              confidence_score=0.7))
    prov = adapter.memory["age"].provenance_info
    assert prov.source_type == "agent_claim_default"
    assert prov.authority_score == 0.3
    assert prov.verified_confidence == 0.3


def test_conflict_logged(adapter):
    adapter.write(_write(agent_id="a", key="status", value="good",
                         source_type="agent_claim"))
    adapter.write(_write(agent_id="b", key="status", value="bad",
                         source_type="agent_claim"))
    conflicts = adapter.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].existing_agent == "a"
    assert conflicts[0].incoming_agent == "b"


def test_trust_tracking(adapter):
    adapter.record_outcome("reliable", was_correct=True)
    adapter.record_outcome("reliable", was_correct=True)
    adapter.record_outcome("unreliable", was_correct=False)
    assert adapter.trust_mgr.get_trust("reliable") > 0.5
    assert adapter.trust_mgr.get_trust("unreliable") <= 0.5


def test_langgraph_sync_state():
    lg = LangGraphLCMAdapter()
    lg.sync_graph_state("node_a", {"status": "done", "count": 3})
    assert lg.read("status") == "done"
    assert lg.read("count") == 3
