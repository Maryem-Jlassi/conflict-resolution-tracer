"""
Integration tests — WritePipeline

Exercises the full write path: validate → loop-detect → lock → resolve → commit.
Uses an in-memory dict storage — no SQLite, no HTTP.
"""

import pytest
from datetime import datetime, timedelta
from typing import Dict, Optional

from lcm_core.conflict import ConflictResolutionEngine, ResolutionConfig
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.crypto import sign_evidence_message
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.pipeline import WritePipeline
from lcm_core.schema import StampedUMF
from lcm_core.trust_manager import TrustManager
from tests.conftest import make_memory, make_evidence

# ---------------------------------------------------------------------------
# Minimal in-memory storage
# ---------------------------------------------------------------------------


class _DictStorage:
    def __init__(self):
        self._live: Dict[str, StampedUMF] = {}
        self._archived: Dict[str, str] = {}
        self._pending: Dict[str, list] = {}  # path -> list of pending StampedUMF

    def get_existing(self, path: str) -> Optional[StampedUMF]:
        return self._live.get(path)

    def commit(self, umf: StampedUMF, path: str) -> None:
        self._live[path] = umf

    def commit_pending(self, umf: StampedUMF, path: str) -> None:
        if path not in self._pending:
            self._pending[path] = []
        self._pending[path].append(umf)

    def archive(self, provenance_id: str) -> None:
        self._archived[provenance_id] = provenance_id

    def update_provenance_fields(self, provenance_id: str, **fields) -> None:
        # Mock implementation - no-op for in-memory storage
        pass


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _pipeline(trust=None, engine=None, loop_threshold=1000.0):
    return WritePipeline(
        storage=_DictStorage(),
        trust_manager=trust or TrustManager(),
        conflict_engine=engine or ConflictResolutionEngine(uncertainty_threshold=0.0),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(rate_threshold=loop_threshold),
    )


def _raw(agent, path, value, confidence=0.8, age_seconds=0):
    ts = datetime.utcnow() - timedelta(seconds=age_seconds)
    return {
        "agent_id": agent,
        "session_id": "integration_test",
        "timestamp": ts,
        "confidence_score": confidence,
        "assertion_payload": {path: value},
    }


# ---------------------------------------------------------------------------
# First write — no conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_write_committed():
    p = _pipeline()
    result = await p.process(_raw("agent_a", "user.lang", "Python"))
    assert result.status == "committed"
    assert result.committed.agent_id == "agent_a"


# ---------------------------------------------------------------------------
# Conflict resolution through pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_database_evidence_beats_cold_start():
    p = _pipeline()
    await p.process(_raw("agent_a", "fact", "old_value", confidence=0.3))

    # A signed DATABASE record gives the second write deterministic
    # verified_confidence + authority, so it must win regardless of the
    # recency coin-flip between two near-identical utcnow() timestamps.
    result = await p.process(
        _raw("agent_b", "fact", "new_value", confidence=0.8),
        evidence_records=[
            EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=1.0,
                           source_id="db://verified", verified=True)
        ],
        evidence_signature=sign_evidence_message(EvidenceType.DATABASE, "db://verified"),
    )
    # With higher verified_confidence and authority, agent_b should win
    assert result.status in ("conflict_resolved", "committed")
    assert result.committed.assertion_payload["fact"] == "new_value"


@pytest.mark.asyncio
async def test_high_trust_holds_against_newer_claim():
    trust = TrustManager()
    for _ in range(20):
        trust.record_outcome("veteran", correct=True)

    p = _pipeline(trust=trust)
    await p.process(_raw("veteran", "config.mode", "production", age_seconds=3600))

    result = await p.process(_raw("newcomer", "config.mode", "debug"))
    # veteran trust=1.0 > newcomer trust=0.5, should hold despite recency gap
    assert result.committed.agent_id == "veteran"


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["healthcare", "finance", "coding"])
async def test_domain_trust_respected(domain):
    trust = TrustManager()
    for _ in range(10):
        trust.record_outcome("specialist", correct=True, domain=domain)
    for _ in range(10):
        trust.record_outcome("generalist", correct=False, domain=domain)

    p = _pipeline(trust=trust)
    await p.process(
        _raw("specialist", "domain.fact", "correct", confidence=0.6, age_seconds=600),
        domain=domain,
    )
    # The generalist's claim must be rejected on TRUST alone — even when backed
    # by signed external evidence + agreement (verified_confidence = 0.95) and
    # a high self-reported confidence. The gate must NOT key off the raw
    # self-report (Phase 2): a raw 0.9 previously fired the gate while the
    # verified confidence was only 0.3 (unsupported claim), letting the raw
    # LLM value drive an authoritative decision.
    result = await p.process(
        _raw("generalist", "domain.fact", "wrong", confidence=0.9),
        domain=domain,
        evidence_records=[
            make_evidence("database", relevance=1.0),
        ],
        evidence_signature=sign_evidence_message(EvidenceType.DATABASE, None),
        agreeing_agents=2,
        total_independent_agents=2,
        verified_memories_consistent=True,
    )
    # With the trust gate, the low-trust generalist is rejected entirely
    # rather than going through conflict resolution
    assert result.committed is None  # Generalist rejected by trust gate
    assert "rejected" in result.status.lower()


# ---------------------------------------------------------------------------
# Unresolved conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_uncertainty_threshold_marks_unresolved():
    engine = ConflictResolutionEngine(
        config=ResolutionConfig(
            w_recency=0.25, w_confidence=0.25, w_trust=0.25, w_provenance=0.25,
            uncertainty_threshold=0.99,
        )
    )
    p = _pipeline(engine=engine)
    await p.process(_raw("a", "x", "A", confidence=0.5))
    result = await p.process(_raw("b", "x", "B", confidence=0.5))
    assert result.status == "unresolved"
    assert result.committed.agent_id == "a"  # incumbent holds


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oscillating_writes_frozen():
    p = _pipeline()
    p.loops = LoopDetector(rate_threshold=0.5, window_seconds=60, oscillation_threshold=2)

    now = datetime(2026, 7, 14, 10, 0, 0)
    for i in range(10):
        ts = now + timedelta(seconds=i * 0.1)
        p.loops.record_write("loop.path", f"agent_{i % 2}", "A" if i % 2 == 0 else "B", ts)

    assert p.loops.is_path_frozen("loop.path")
    result = await p.process(_raw("agent_0", "loop.path", "C"))
    assert result.status == "loop_frozen"


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_packet_rejected():
    p = _pipeline()
    result = await p.process({"agent_id": "", "session_id": "s",
                               "timestamp": datetime.utcnow(),
                               "confidence_score": 0.5,
                               "assertion_payload": {"k": "v"}})
    assert result.status == "rejected"


# ---------------------------------------------------------------------------
# Trust feedback via record_verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "correct_calls, wrong_calls, expected_trust",
    [(3, 0, 1.0), (0, 3, 0.0), (2, 1, 2 / 3)],
)
async def test_record_verification_updates_trust(correct_calls, wrong_calls, expected_trust):
    p = _pipeline()
    await p.process(_raw("agent", "k", "v"))

    for _ in range(correct_calls):
        p.record_verification("agent", correct=True)
    for _ in range(wrong_calls):
        p.record_verification("agent", correct=False)

    assert abs(p.trust.get_trust("agent") - expected_trust) < 1e-9
