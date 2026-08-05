"""
Core module coverage — compressor, loop detection, memory management, multimodal.

Lightweight focused tests replacing the removed phase test files.
Each test covers a distinct functional requirement, not just line coverage.
"""

import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from lcm_core.compressor import ContextRetriever
from lcm_core.loop_detection import LoopDetector
from lcm_core.memory_mgmt import MemoryManager
from lcm_core.multimodal import MultimodalManager, MultimodalContext
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.schema import StampedUMF, ProvenanceInfo
from lcm_core.confidence_engine import ConfidenceEngine
from lcm_core.locking import AsyncLockManager

NOW = datetime(2026, 7, 14, 10, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _umf(agent, path, value, confidence=0.8, age_seconds=0):
    ts = NOW - timedelta(seconds=age_seconds)
    ce = ConfidenceEngine()
    return StampedUMF(
        agent_id=agent, session_id="s", timestamp=ts,
        confidence_score=confidence, assertion_payload={path: value},
        provenance_id=f"p_{agent}_{path}", ingested_at=ts,
        provenance_info=ProvenanceInfo(
            source_type="database", authority_score=0.9,
            verified_confidence=ce.score_from_source_type("database"),
        ),
    )


# ===========================================================================
# ContextRetriever (compressor.py)
# ===========================================================================

def test_retriever_path_prefix_filtering():
    r = ContextRetriever()
    r.store_fact("patient.vitals.temp", _umf("a", "patient.vitals.temp", 37.5))
    r.store_fact("patient.vitals.hr",   _umf("a", "patient.vitals.hr", 72))
    r.store_fact("system.config.mode",  _umf("a", "system.config.mode", "prod"))

    vitals = r.get_context("patient.vitals")
    assert len(vitals) == 2
    assert all("vitals" in f["path"] for f in vitals)

    system = r.get_context("system")
    assert len(system) == 1


def test_retriever_u_shaped_placement():
    """Highest-priority fact must appear first and last when N > 3."""
    r = ContextRetriever()
    for i in range(8):
        r.store_fact(f"t.{i}", _umf("a", f"t.{i}", i, confidence=0.9 - i * 0.05))

    results = r.get_context("t")
    assert len(results) > 8              # duplicated end entries
    assert results[0]["path"] == results[-1]["path"]   # U-shape


def test_retriever_empty_returns_empty():
    r = ContextRetriever()
    assert r.get_context("nothing") == []


def test_retriever_no_match_returns_empty():
    r = ContextRetriever()
    r.store_fact("patient.temp", _umf("a", "patient.temp", 37.0))
    assert r.get_context("system") == []


def test_retriever_psi_score_in_results():
    r = ContextRetriever()
    r.store_fact("p", _umf("a", "p", "v"))
    results = r.get_context("p", trust_table={"a": 0.8})
    assert "psi_score" in results[0]
    assert 0 < results[0]["psi_score"] <= 1.5


# ===========================================================================
# LoopDetector (loop_detection.py)
# ===========================================================================

def test_oscillation_detected_and_frozen():
    d = LoopDetector(rate_threshold=5.0, window_seconds=2, oscillation_threshold=3)
    path = "test.oscillate"
    for i in range(20):
        ts = NOW + timedelta(milliseconds=i * 100)
        d.record_write(path, f"agent_{i % 2}", ["A", "B"][i % 2], ts)
        if d.is_path_frozen(path):
            break
    assert d.is_path_frozen(path)


def test_slow_writes_not_frozen():
    d = LoopDetector(rate_threshold=5.0)
    for i in range(10):
        ts = NOW + timedelta(seconds=i * 5)
        result = d.record_write("p", f"a{i%2}", f"v{i%2}", ts)
        assert result is None or not result.is_looping


def test_monotone_writes_not_frozen():
    d = LoopDetector(rate_threshold=1.0, window_seconds=60)
    for i in range(20):
        ts = NOW + timedelta(milliseconds=i * 50)
        d.record_write("counter", "agent", f"unique_{i}", ts)
    assert not d.is_path_frozen("counter")


def test_unfreeze_allows_new_writes():
    d = LoopDetector(rate_threshold=5.0, oscillation_threshold=2)
    for i in range(10):
        d.record_write("p", f"a{i%2}", f"v{i%2}", NOW + timedelta(milliseconds=i * 50))
    assert d.is_path_frozen("p")
    d.unfreeze_path("p")
    d.clear_history("p")
    assert not d.is_path_frozen("p")


def test_multiple_paths_independent():
    d = LoopDetector(rate_threshold=5.0, oscillation_threshold=2)
    for i in range(10):
        d.record_write("p1", f"a{i%2}", f"v{i%2}", NOW + timedelta(milliseconds=i * 50))
    result = d.record_write("p2", "a", "v", NOW)
    assert not d.is_path_frozen("p2")


def test_frozen_paths_list():
    d = LoopDetector(rate_threshold=5.0, oscillation_threshold=2)
    for path in ["px", "py"]:
        for i in range(10):
            d.record_write(path, f"a{i%2}", f"v{i%2}", NOW + timedelta(milliseconds=i * 50))
    frozen = d.get_frozen_paths()
    assert len(frozen) >= 1


# ===========================================================================
# MemoryManager (memory_mgmt.py)
# ===========================================================================

def test_staging_commit_clear_cycle():
    mgr = MemoryManager()
    m = _umf("a", "k", "v")
    mgr.stage_fact("k", m)
    assert mgr.get_stats()["staging_count"] == 1
    mgr.commit_fact("k", m)
    mgr.clear_staging()
    assert mgr.get_stats()["staging_count"] == 0
    assert mgr.get_stats()["committed_count"] == 1


def test_read_updates_tracking():
    mgr = MemoryManager()
    m = _umf("a", "k", "v")
    mgr.commit_fact("k", m)
    mgr.read_fact("k")
    mgr.read_fact("k")
    assert mgr.committed["k"].read_count == 2
    assert mgr.committed["k"].last_read_at is not None


def test_eviction_moves_to_cold():
    mgr = MemoryManager(eviction_threshold=0.4)
    engine = ConflictResolutionEngine()
    # agent_claim authority=0.3, 1-year old → Ψ will be well below 0.4
    ce = ConfidenceEngine()
    ts = NOW - timedelta(days=365)
    old = StampedUMF(
        agent_id="a", session_id="s", timestamp=ts, confidence_score=0.3,
        assertion_payload={"old": "v"}, provenance_id="p_old", ingested_at=ts,
        provenance_info=ProvenanceInfo(
            source_type="agent_claim", authority_score=0.3,
            verified_confidence=ce.score_from_source_type("agent_claim"),
        ),
    )
    mgr.commit_fact("old", old)
    evicted = mgr.evict_to_cold(engine, {"a": 0.3}, current_time=NOW)
    assert evicted >= 1
    assert len(mgr.retrieve_from_cold("old")) >= 1


def test_history_lossless():
    mgr = MemoryManager()
    for i in range(4):
        mgr.commit_fact("p", _umf(f"a{i}", "p", f"v{i}"))
    assert len(mgr.get_full_history("p")) == 4


def test_aggregate_summary():
    mgr = MemoryManager()
    for i in range(3):
        mgr.commit_fact("p", _umf(f"a{i%2}", "p", f"v{i}"))
    s = mgr.aggregate_path_history("p")
    assert s["version_count"] == 3
    assert s["path"] == "p"


def test_cold_storage_retrievable():
    mgr = MemoryManager(eviction_threshold=0.9)
    engine = ConflictResolutionEngine()
    m = _umf("a", "cold_key", "val", confidence=0.4, age_seconds=3600)
    mgr.commit_fact("cold_key", m)
    mgr.evict_to_cold(engine, {"a": 0.3}, current_time=NOW)
    cold = mgr.retrieve_from_cold("cold_key")
    assert len(cold) >= 1
    assert cold[0].umf.assertion_payload["cold_key"] == "val"


def test_unread_ttl_eviction():
    mgr = MemoryManager(unread_ttl_seconds=86400)
    engine = ConflictResolutionEngine()
    m = _umf("a", "stale", "v")
    mgr.commit_fact("stale", m)
    mgr.committed["stale"].created_at = NOW - timedelta(days=2)
    evicted = mgr.evict_to_cold(engine, {"a": 0.8}, current_time=NOW)
    assert evicted >= 1


# ===========================================================================
# MultimodalManager / MultimodalContext (multimodal.py)
# ===========================================================================

def test_multimodal_full_lifecycle():
    mgr = MultimodalManager()
    ctx = mgr.create_context("c1")
    ctx.add_modality("text", "hello", encoding="utf-8", size_bytes=5)
    ctx.add_modality("image", b"img", encoding="binary", size_bytes=100)

    assert ctx.get_total_size() == 105
    assert ctx.get_modality("text") == "hello"

    ctx.remove_modality("image")
    assert ctx.get_modality("image") is None
    assert ctx.get_total_size() == 5


def test_multimodal_merge_conflict_newest():
    import time
    mgr = MultimodalManager()
    tgt = mgr.create_context("tgt")
    tgt.add_modality("text", "old", encoding="utf-8", size_bytes=3)
    time.sleep(0.01)
    src = mgr.create_context("src")
    src.add_modality("text", "new", encoding="utf-8", size_bytes=3)

    result = mgr.merge_contexts("src", "tgt", conflict_strategy="newest")
    assert result.get_modality("text") == "new"


def test_multimodal_delete_nonexistent_returns_false():
    mgr = MultimodalManager()
    assert mgr.delete_context("ghost") is False


def test_multimodal_to_dict_structure():
    ctx = MultimodalContext(context_id="x")
    ctx.add_modality("audio", b"bytes", encoding="binary", size_bytes=50)
    d = ctx.to_dict()
    assert d["context_id"] == "x"
    assert "audio" in d["modalities"]
    assert "created_at" in d


# ===========================================================================
# AsyncLockManager — additional coverage
# ===========================================================================

@pytest.mark.asyncio
async def test_lock_contention_returns_state_code():
    mgr = AsyncLockManager()
    path = "contention.path"
    import asyncio

    async def holder():
        await mgr.acquire_write_lock(path, timeout=5.0)
        await asyncio.sleep(1.0)
        await mgr.release_write_lock(path)

    async def impatient():
        await asyncio.sleep(0.05)
        return await mgr.acquire_write_lock(path, timeout=0.1, max_retries=2)

    from lcm_core.locking import StateCode
    _, result = await asyncio.gather(holder(), impatient())
    assert not result.acquired
    assert result.state_code == StateCode.WRITE_LOCK_CONTENTION
