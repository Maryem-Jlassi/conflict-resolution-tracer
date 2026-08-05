"""
Multimodal context manager — minimal essential tests.

LCM does not process binary content. These tests confirm the context
lifecycle works correctly and that conflict-engine handles media_hash
differences the same way as any other assertion conflict.
"""

import pytest
from datetime import datetime, timedelta

from lcm_core.multimodal import MultimodalManager
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.schema import StampedUMF, ProvenanceInfo
from lcm_core.confidence_engine import ConfidenceEngine


# ---------------------------------------------------------------------------
# Context lifecycle
# ---------------------------------------------------------------------------

def test_create_add_retrieve_remove():
    mgr = MultimodalManager()
    ctx = mgr.create_context("ctx1")
    ctx.add_modality("image", b"fake", encoding="binary", size_bytes=100)

    assert ctx.get_modality("image") == b"fake"
    assert ctx.get_total_size() == 100

    ctx.remove_modality("image")
    assert ctx.get_modality("image") is None


def test_auto_id_unique():
    mgr = MultimodalManager()
    ids = {mgr.create_context().context_id for _ in range(5)}
    assert len(ids) == 5


def test_merge_adds_missing_modality():
    mgr = MultimodalManager()
    src = mgr.create_context("src")
    tgt = mgr.create_context("tgt")
    src.add_modality("text", "hello", encoding="utf-8", size_bytes=5)
    tgt.add_modality("image", b"img", encoding="binary", size_bytes=10)

    result = mgr.merge_contexts("src", "tgt")
    assert result.get_modality("text") == "hello"
    assert result.get_modality("image") is not None


# ---------------------------------------------------------------------------
# Media hash conflict routes through Ψ engine unchanged
# ---------------------------------------------------------------------------

def test_stale_media_hash_loses_on_recency():
    engine = ConflictResolutionEngine(uncertainty_threshold=0.0)
    ce = ConfidenceEngine()
    now = datetime.utcnow()

    existing = StampedUMF(
        agent_id="old_agent", session_id="s1",
        timestamp=now - timedelta(hours=2),
        confidence_score=0.9,
        assertion_payload={"scan": "old"},
        media_hash="old_hash_" + "a" * 54,
        provenance_id="p1", ingested_at=now - timedelta(hours=2),
        provenance_info=ProvenanceInfo(
            source_type="database", authority_score=0.9,
            verified_confidence=ce.score_from_source_type("database"),
        ),
    )
    incoming = StampedUMF(
        agent_id="new_agent", session_id="s2",
        timestamp=now, confidence_score=0.85,
        assertion_payload={"scan": "new"},
        media_hash="new_hash_" + "b" * 54,
        provenance_id="p2", ingested_at=now,
        provenance_info=ProvenanceInfo(
            source_type="database", authority_score=0.9,
            verified_confidence=ce.score_from_source_type("database"),
        ),
    )

    result = engine.resolve_conflict(existing, incoming, {"old_agent": 0.8, "new_agent": 0.8})
    assert result.winner.agent_id == "new_agent"
    assert result.loser is not None  # archived, never deleted
