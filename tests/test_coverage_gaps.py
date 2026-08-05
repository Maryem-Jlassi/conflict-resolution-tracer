"""
Targeted coverage tests for edge paths not hit by the main test suite.

Kept minimal: only tests that cover genuinely hard-to-reach branches
that have research or operational significance.
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from lcm_core.locking import (
    AsyncLockManager, StateCode,
    LockContentionError, SemanticContradictionError, StaleStateDriftError,
    get_lock_manager,
)
from lcm_core.event_bus import EventBus, get_event_bus
from lcm_core.events import LCMEvent, EventType
from lcm_core.conflict import ConflictResolutionEngine, ResolutionConfig
from lcm_core.provenance import validate_and_stamp, RejectionError
from lcm_core.schema import StampedUMF, ProvenanceInfo
from lcm_core.trust_manager import TrustManager
from lcm_core.memory_mgmt import MemoryManager


# ---------------------------------------------------------------------------
# Locking — error classes prove the state-code hierarchy exists
# ---------------------------------------------------------------------------

def test_lock_contention_error_state_code():
    assert LockContentionError("p").code == StateCode.WRITE_LOCK_CONTENTION


def test_semantic_contradiction_error_state_code():
    err = SemanticContradictionError("p", "A", "B")
    assert err.code == StateCode.SEMANTIC_CONTRADICTION
    assert err.existing_value == "A"


def test_stale_drift_error_state_code():
    assert StaleStateDriftError("p").code == StateCode.STALE_STATE_DRIFT


@pytest.mark.asyncio
async def test_is_locked_reflects_held_state():
    m = AsyncLockManager()
    await m.acquire_write_lock("x")
    assert m.is_locked("x")
    await m.release_write_lock("x")
    assert not m.is_locked("x")


def test_global_lock_manager_singleton():
    assert get_lock_manager() is get_lock_manager()


# ---------------------------------------------------------------------------
# EventBus — exception swallowing and unsubscribe
# ---------------------------------------------------------------------------

def test_crashing_listener_does_not_block_good_listener():
    bus = EventBus()
    received = []

    def bad(event): raise RuntimeError("crash")
    def good(event): received.append(event)

    bus.subscribe(bad)
    bus.subscribe(good)
    bus.publish(LCMEvent(EventType.MEMORY_INGESTED, datetime.utcnow(), {}))
    assert len(received) == 1


def test_unsubscribe_removes_listener():
    bus = EventBus()
    received = []

    def listener(event): received.append(event)

    bus.subscribe(listener)
    bus.unsubscribe(listener)
    bus.publish(LCMEvent(EventType.MEMORY_INGESTED, datetime.utcnow(), {}))
    assert len(received) == 0


def test_listener_count():
    bus = EventBus()
    assert bus.listener_count == 0
    bus.subscribe(lambda e: None)
    assert bus.listener_count == 1


# ---------------------------------------------------------------------------
# Conflict — uncertainty threshold marks unresolved
# ---------------------------------------------------------------------------

def test_uncertainty_threshold_unresolved():
    engine = ConflictResolutionEngine(
        config=ResolutionConfig(
            w_recency=0.25, w_confidence=0.25, w_trust=0.25, w_provenance=0.25,
            uncertainty_threshold=0.99,
        )
    )
    now = datetime(2026, 7, 14, 10, 0, 0)
    a = StampedUMF(
        agent_id="a", session_id="s", timestamp=now, confidence_score=0.5,
        assertion_payload={"k": "v"}, provenance_id="pa", ingested_at=now,
        provenance_info=ProvenanceInfo(verified_confidence=0.5, authority_score=0.5),
    )
    b = StampedUMF(
        agent_id="b", session_id="s", timestamp=now, confidence_score=0.5,
        assertion_payload={"k": "v"}, provenance_id="pb", ingested_at=now,
        provenance_info=ProvenanceInfo(verified_confidence=0.5, authority_score=0.5),
    )
    result = engine.resolve_conflict(a, b, {"a": 0.5, "b": 0.5})
    assert result.unresolved is True


# ---------------------------------------------------------------------------
# Provenance — unexpected exception wrapped as RejectionError
# ---------------------------------------------------------------------------

def test_unexpected_exception_wrapped():
    raw = {
        "agent_id": "a", "session_id": "s",
        "timestamp": datetime.utcnow(), "confidence_score": 0.8,
        "assertion_payload": {"k": "v"},
    }
    with patch("lcm_core.provenance.UMF", side_effect=RuntimeError("boom")):
        with pytest.raises(RejectionError) as exc_info:
            validate_and_stamp(raw)
    assert "boom" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# MemoryManager — edge: aggregate on missing / empty path returns None
# ---------------------------------------------------------------------------

def test_aggregate_missing_path_returns_none():
    mgr = MemoryManager()
    assert mgr.aggregate_path_history("nonexistent") is None
