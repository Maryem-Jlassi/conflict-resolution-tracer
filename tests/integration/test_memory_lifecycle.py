"""
Integration tests — Memory lifecycle (staging → committed → cold storage)

Tests the MemoryManager eviction, history preservation, and stats
using the ConflictResolutionEngine for Ψ-driven eviction decisions.
"""

import pytest
from datetime import datetime, timedelta

from crt_core.conflict import ConflictResolutionEngine
from crt_core.memory_mgmt import MemoryManager
from tests.conftest import REFERENCE_TIME, make_memory

REF = REFERENCE_TIME


# ---------------------------------------------------------------------------
# Staging buffer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 5, 20])
def test_staging_count(n):
    mgr = MemoryManager()
    for i in range(n):
        mgr.stage_fact(f"p.{i}", make_memory(f"a{i}", path=f"p.{i}"))
    assert mgr.get_stats()["staging_count"] == n
    mgr.clear_staging()
    assert mgr.get_stats()["staging_count"] == 0


# ---------------------------------------------------------------------------
# Commit and read
# ---------------------------------------------------------------------------


def test_committed_fact_readable():
    mgr = MemoryManager()
    m = make_memory("agent", path="test.key")
    mgr.commit_fact("test.key", m)
    result = mgr.read_fact("test.key")
    assert result is m


def test_read_increments_count():
    mgr = MemoryManager()
    m = make_memory("agent", path="p")
    mgr.commit_fact("p", m)
    mgr.read_fact("p")
    mgr.read_fact("p")
    assert mgr.committed["p"].read_count == 2


# ---------------------------------------------------------------------------
# Eviction — threshold-based
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "threshold, age_days, confidence, trust, should_evict",
    [
        (0.10, 365, 0.5, 0.5, False),  # low threshold — even stale survives
        (0.40, 365, 0.3, 0.3, True),   # medium threshold — old low-quality evicted
        (0.90, 365, 0.5, 0.5, True),   # high threshold — most evicted
        (0.40,   0, 0.9, 0.8, False),  # medium threshold — fresh high-quality survives
    ],
)
def test_eviction_threshold(threshold, age_days, confidence, trust, should_evict):
    mgr = MemoryManager(eviction_threshold=threshold)
    engine = ConflictResolutionEngine()
    m = make_memory("agent", age_days=age_days, confidence=confidence,
                    source="database" if confidence >= 0.5 else "agent_claim",
                    reference_time=REF)
    mgr.commit_fact("test.ev", m)
    evicted = mgr.evict_to_cold(engine, {"agent": trust}, current_time=REF)
    if should_evict:
        assert evicted >= 1
        cold = mgr.retrieve_from_cold("test.ev")
        assert len(cold) >= 1
    else:
        assert evicted == 0
        assert mgr.read_fact("test.ev") is not None


# ---------------------------------------------------------------------------
# History preservation (lossless)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_versions", [1, 3, 10])
def test_history_lossless(n_versions):
    mgr = MemoryManager()
    path = "versioned.fact"
    for i in range(n_versions):
        ts = REF + timedelta(minutes=i)
        m = make_memory(f"agent_{i}", path=path, reference_time=ts)
        mgr.commit_fact(path, m)

    history = mgr.get_full_history(path)
    assert len(history) == n_versions


def test_history_preserved_after_eviction():
    mgr = MemoryManager(eviction_threshold=0.95)
    engine = ConflictResolutionEngine()
    path = "h.fact"
    for i in range(3):
        mgr.commit_fact(path, make_memory("a", path=path, age_days=365))
    mgr.evict_to_cold(engine, {"a": 0.3}, current_time=REF)
    assert len(mgr.get_full_history(path)) == 3
