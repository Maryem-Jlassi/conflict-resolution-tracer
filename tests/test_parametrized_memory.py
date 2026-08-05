"""
Parametrized memory management and multi-agent adapter tests.

Covers eviction thresholds, history preservation across multiple versions,
and adapter conflict resolution across evidence source pairs.
"""

import pytest
from datetime import datetime, timedelta

from lcm_core.memory_mgmt import MemoryManager
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.schema import StampedUMF, ProvenanceInfo
from agents.lcm_adapter import (
    AutoGenLCMAdapter,
    MemoryWrite,
    memory_write_needs_signature,
    sign_memory_write,
)

NOW = datetime(2026, 7, 14, 10, 0, 0)


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


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def make_umf(agent_id, path, value, age_seconds=0, confidence=0.7, authority=0.7):
    ts = NOW - timedelta(seconds=age_seconds)
    return StampedUMF(
        agent_id=agent_id,
        session_id="mem_test",
        timestamp=ts,
        confidence_score=confidence,
        assertion_payload={path: value},
        provenance_id=f"prov_{agent_id}_{path}_{age_seconds}",
        ingested_at=ts,
        provenance_info=ProvenanceInfo(
            verified_confidence=confidence,
            authority_score=authority,
        ),
    )


# ---------------------------------------------------------------------------
# 1. Eviction threshold — parametrized over threshold values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "threshold, old_conf, old_trust, should_evict",
    [
        # Low threshold: even mediocre facts survive
        (0.10, 0.5, 0.5, False),
        # Medium threshold: old low-confidence fact evicted
        (0.40, 0.3, 0.3, True),
        # High threshold: most facts evicted
        (0.90, 0.5, 0.5, True),
        # At exact boundary: fact with high conf/trust survives even high threshold
        (0.40, 0.9, 0.8, False),
    ],
)
def test_eviction_threshold(threshold, old_conf, old_trust, should_evict):
    manager = MemoryManager(eviction_threshold=threshold)
    engine = ConflictResolutionEngine()

    umf = make_umf(
        "agent", "test.evict", "val",
        age_seconds=86400 * 365,  # 1 year old → low recency
        confidence=old_conf, authority=old_conf,
    )
    manager.commit_fact("test.evict", umf)

    trust_table = {"agent": old_trust}
    evicted = manager.evict_to_cold(engine, trust_table, current_time=NOW)

    if should_evict:
        assert evicted >= 1, f"threshold={threshold}: expected eviction"
        assert "test.evict" not in manager.committed
    else:
        assert evicted == 0, f"threshold={threshold}: expected no eviction"
        assert "test.evict" in manager.committed


# ---------------------------------------------------------------------------
# 2. History is lossless — parametrized over version counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_versions", [1, 2, 5, 10, 20])
def test_history_lossless_across_versions(n_versions):
    manager = MemoryManager()
    path = "versioned.fact"
    values = [f"v{i}" for i in range(n_versions)]

    for i, val in enumerate(values):
        ts = NOW + timedelta(minutes=i)
        umf = make_umf(f"agent_{i}", path, val, age_seconds=0)
        # Manually set correct timestamp
        umf = StampedUMF(
            agent_id=f"agent_{i}",
            session_id="s",
            timestamp=ts,
            confidence_score=0.7,
            assertion_payload={path: val},
            provenance_id=f"prov_{i}",
            ingested_at=ts,
            provenance_info=ProvenanceInfo(verified_confidence=0.7, authority_score=0.7),
        )
        manager.commit_fact(path, umf)

    history = manager.get_full_history(path)
    assert len(history) == n_versions, f"Expected {n_versions} versions, got {len(history)}"

    # Every value must be present in order
    for i, umf in enumerate(history):
        assert umf.assertion_payload[path] == values[i]


# ---------------------------------------------------------------------------
# 3. Staging buffer — parametrized over batch sizes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_facts", [1, 5, 10, 50])
def test_staging_buffer_tracks_count(n_facts):
    manager = MemoryManager()
    now = datetime.utcnow()

    for i in range(n_facts):
        umf = make_umf("a", f"p.{i}", f"v{i}")
        manager.stage_fact(f"p.{i}", umf)

    assert manager.get_stats()["staging_count"] == n_facts
    manager.clear_staging()
    assert manager.get_stats()["staging_count"] == 0


# ---------------------------------------------------------------------------
# 4. Multi-agent adapter — evidence source pairs
# ---------------------------------------------------------------------------

# (existing_source, incoming_source, existing_conf, incoming_conf, expected_winner)
EVIDENCE_CONFLICT_CASES = [
    ("user_input",  "agent_claim", 0.7, 0.95, "existing"),   # user_input always wins
    ("database",    "agent_claim", 0.7, 0.95, "existing"),   # DB beats LLM
    ("tool_output", "agent_claim", 0.7, 0.95, "existing"),   # tool beats LLM
    ("document",    "agent_claim", 0.7, 0.95, "existing"),   # doc beats LLM
    ("database",    "user_input",  0.9, 0.7,  "incoming"),   # user_input beats DB
    ("agent_claim", "database",    0.9, 0.7,  "incoming"),   # DB beats LLM even with lower conf
]


@pytest.mark.parametrize(
    "ex_src, in_src, ex_conf, in_conf, expected",
    EVIDENCE_CONFLICT_CASES,
)
def test_adapter_evidence_priority(ex_src, in_src, ex_conf, in_conf, expected):
    adapter = AutoGenLCMAdapter()
    now = NOW

    existing_write = _write(
        agent_id="existing_agent",
        key="test.fact",
        value="existing_value",
        source_type=ex_src,
        timestamp=now - timedelta(seconds=10),
        confidence_score=ex_conf,
    )
    incoming_write = _write(
        agent_id="incoming_agent",
        key="test.fact",
        value="incoming_value",
        source_type=in_src,
        timestamp=now,
        confidence_score=in_conf,
    )

    adapter.write(existing_write)
    conflict = adapter.write(incoming_write)

    final_value = adapter.read("test.fact")

    if expected == "existing":
        assert final_value == "existing_value", (
            f"{ex_src} vs {in_src}: expected existing to win, "
            f"got {conflict.resolution if conflict else 'no conflict'}"
        )
        if conflict:
            assert conflict.resolution == "existing_won"
    else:
        assert final_value == "incoming_value", (
            f"{ex_src} vs {in_src}: expected incoming to win, "
            f"got {conflict.resolution if conflict else 'no conflict'}"
        )
        if conflict:
            assert conflict.resolution == "incoming_won"


# ---------------------------------------------------------------------------
# 5. Trust accumulation in adapter affects resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reliable_outcomes, unreliable_outcomes",
    [
        (10, 10),   # both moderate
        (20,  5),   # reliable much better
        ( 5, 20),   # unreliable much worse
    ],
)
def test_adapter_trust_affects_resolution(reliable_outcomes, unreliable_outcomes):
    adapter = AutoGenLCMAdapter()
    now = NOW

    # Build trust history
    for _ in range(reliable_outcomes):
        adapter.record_outcome("reliable", was_correct=True)
    for _ in range(unreliable_outcomes):
        adapter.record_outcome("unreliable", was_correct=False)

    reliable_trust = adapter.trust_mgr.get_trust("reliable")
    unreliable_trust = adapter.trust_mgr.get_trust("unreliable")

    if reliable_outcomes > unreliable_outcomes:
        assert reliable_trust > unreliable_trust
    elif reliable_outcomes < unreliable_outcomes:
        # unreliable_outcomes are all False → trust = 0
        assert unreliable_trust <= reliable_trust


# ---------------------------------------------------------------------------
# 6. Independent paths never interfere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_paths", [2, 5, 10])
def test_independent_paths_no_cross_contamination(n_paths):
    adapter = AutoGenLCMAdapter()
    now = NOW

    # Each agent writes to its own unique path
    for i in range(n_paths):
        w = _write(
            agent_id=f"agent_{i}",
            key=f"path.{i}",
            value=f"value_{i}",
            source_type="database",
            timestamp=now,
        )
        conflict = adapter.write(w)
        assert conflict is None, f"First write to path.{i} should not conflict"

    # Verify each path has its own value
    for i in range(n_paths):
        assert adapter.read(f"path.{i}") == f"value_{i}"
