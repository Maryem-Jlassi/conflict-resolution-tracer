"""
Unit tests — Runtime metrics & telemetry (Phase 12).

Covers the MetricsRegistry primitives, derived-rate computation, and the
instrumentation hooks wired into the write pipeline and the crypto verifier:
write-outcome counters, end-to-end latency histogram, evidence verification
counters (replay / temporal / invalid signature), trust updates and lineage
node recording.
"""

import pytest
from datetime import datetime, timedelta

from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import (
    reset_replay_guard,
    sign_evidence_message,
    verify_evidence_signature_crypto,
)
from crt_core.locking import AsyncLockManager
from crt_core.loop_detection import LoopDetector
from crt_core.metrics import (
    MetricsRegistry,
    compute_metrics_snapshot,
    get_metrics_registry,
    record_write_status,
    reset_metrics_registry,
)
from crt_core.pipeline import WritePipeline
from crt_core.trust_manager import TrustManager

REF = datetime(2026, 7, 14, 10, 0, 0)


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics_registry()
    yield
    reset_metrics_registry()


# ---------------------------------------------------------------------------
# Registry primitives
# ---------------------------------------------------------------------------


class TestMetricsRegistry:
    def test_incr_snapshot_reset(self):
        reg = MetricsRegistry()
        reg.incr("a")
        reg.incr("a", delta=2)
        assert reg.snapshot()["counters"] == {"a": 3.0}
        reg.reset()
        assert reg.snapshot()["counters"] == {}

    def test_gauge_and_observe(self):
        reg = MetricsRegistry()
        reg.set_gauge("g", 7)
        reg.observe("lat", 10.0)
        reg.observe("lat", 20.0)
        snap = reg.snapshot()
        assert snap["gauges"] == {"g": 7.0}
        assert snap["histograms"]["lat"] == [10.0, 20.0]

    def test_global_registry_is_singleton(self):
        assert get_metrics_registry() is get_metrics_registry()
        reset_metrics_registry()
        assert get_metrics_registry().snapshot()["counters"] == {}


class TestRecordWriteStatus:
    @pytest.mark.parametrize(
        "status,counter",
        [
            ("committed", "writes.status.committed"),
            ("conflict_resolved", "writes.status.conflict_resolved"),
            ("unresolved", "writes.status.unresolved"),
            ("rejected", "writes.status.rejected"),
            ("rejected_untrusted", "writes.status.rejected_untrusted"),
            ("rejected_suspicious", "writes.status.rejected_suspicious"),
            ("loop_frozen", "writes.status.loop_frozen"),
            ("lock_failed", "writes.status.lock_failed"),
        ],
    )
    def test_status_maps_to_counter(self, status, counter):
        record_write_status(status)
        snap = get_metrics_registry().snapshot()["counters"]
        assert snap["writes.total"] == 1.0
        assert snap[counter] == 1.0

    def test_conflict_counters(self):
        record_write_status("conflict_resolved")
        record_write_status("unresolved")
        snap = get_metrics_registry().snapshot()["counters"]
        assert snap["conflicts.resolved"] == 1.0
        assert snap["conflicts.unresolved"] == 1.0

    def test_unknown_status_lands_in_pipeline_error(self):
        record_write_status("nonsense")
        snap = get_metrics_registry().snapshot()["counters"]
        assert snap["writes.status.pipeline_error"] == 1.0


# ---------------------------------------------------------------------------
# Derived rates & histogram percentiles
# ---------------------------------------------------------------------------


class TestComputeSnapshot:
    def test_zero_denominator_rates_are_zero(self):
        reg = get_metrics_registry()
        reg.incr("evidence.replay_rejected")
        snap = compute_metrics_snapshot(reg)
        assert snap["rates"]["replay_rejection_rate"] == 0.0

    def test_rejection_rate(self):
        reg = get_metrics_registry()
        record_write_status("rejected")
        record_write_status("committed")
        record_write_status("committed")
        snap = compute_metrics_snapshot(reg)
        assert snap["rates"]["gate_rejection_rate"] == pytest.approx(1 / 3)
        assert snap["rates"]["commit_rate"] == pytest.approx(2 / 3)

    def test_crypto_rates(self):
        reg = get_metrics_registry()
        for _ in range(10):
            reg.incr("evidence.verification.total")
        for _ in range(3):
            reg.incr("evidence.temporal_rejected")
        for _ in range(2):
            reg.incr("evidence.replay_rejected")
        snap = compute_metrics_snapshot(reg)
        assert snap["rates"]["temporal_rejection_rate"] == pytest.approx(0.3)
        assert snap["rates"]["replay_rejection_rate"] == pytest.approx(0.2)

    def test_histogram_percentiles(self):
        reg = get_metrics_registry()
        for i in range(1, 101):
            reg.observe("pipeline.latency_ms", float(i))
        snap = compute_metrics_snapshot(reg)["histograms"]["pipeline.latency_ms"]
        assert snap["count"] == 100
        assert snap["mean_ms"] == pytest.approx(50.5, abs=0.01)
        assert snap["p50_ms"] == pytest.approx(50.0, abs=1.0)
        assert snap["p95_ms"] == pytest.approx(95.0, abs=1.0)
        assert snap["max_ms"] == 100.0

    def test_snapshot_has_generated_at(self):
        snap = compute_metrics_snapshot()
        assert "generated_at" in snap


# ---------------------------------------------------------------------------
# Pipeline instrumentation
# ---------------------------------------------------------------------------


class _DictStorage:
    """Minimal in-memory storage with a lineage hook, mirroring integration tests."""

    def __init__(self):
        self._live = {}
        self._archived = {}
        self._pending = {}
        self._nodes = []

    def get_existing(self, path):
        return self._live.get(path)

    def commit(self, umf, path):
        self._live[path] = umf

    def commit_pending(self, umf, path):
        if path not in self._pending:
            self._pending[path] = []
        self._pending[path].append(umf)

    def archive(self, provenance_id):
        self._archived[provenance_id] = provenance_id

    def update_provenance_fields(self, provenance_id, **fields):
        pass

    def store_lineage_node(self, node):
        self._nodes.append(node)


def _pipeline():
    return WritePipeline(
        storage=_DictStorage(),
        trust_manager=TrustManager(),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(rate_threshold=1000.0),
    )


def _raw(agent, path, value, confidence=0.8, **extra):
    payload = {"agent_id": agent, "session_id": "metrics-test",
               "timestamp": REF, "confidence_score": confidence,
               "assertion_payload": {path: value}}
    payload.update(extra)
    return payload


class TestPipelineInstrumentation:
    @pytest.mark.asyncio
    async def test_committed_write_records_counters(self):
        pl = _pipeline()
        res = await pl.process(_raw("agent-a", "metrics.a", "v1"))
        assert res.status == "committed"
        snap = get_metrics_registry().snapshot()
        assert snap["counters"]["writes.total"] == 1.0
        assert snap["counters"]["writes.status.committed"] == 1.0
        assert snap["counters"].get("writes.status.rejected", 0.0) == 0.0

    @pytest.mark.asyncio
    async def test_conflict_resolved_write_records_conflict(self):
        from crt_core.crypto import sign_assertion_evidence
        ev = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE, source_id="db", relevance_score=1.0
        )
        incoming=_raw("agent-b", "metrics.conflict", "v1", confidence=0.8)
        sig = sign_assertion_evidence(EvidenceType.DATABASE, "db",
            agent_id=incoming["agent_id"],timestamp=incoming["timestamp"],assertion_payload=incoming["assertion_payload"])
        pl = _pipeline()
        await pl.process(_raw("agent-a", "metrics.conflict", "v1", confidence=0.8))
        res = await pl.process(
            incoming,
            evidence_records=[ev],
            evidence_signature=sig,
        )
        assert res.status == "conflict_resolved"
        snap = get_metrics_registry().snapshot()["counters"]
        assert snap["writes.total"] == 2.0
        assert snap["writes.status.conflict_resolved"] == 1.0
        assert snap["conflicts.resolved"] == 1.0

    @pytest.mark.asyncio
    async def test_rejected_write_records_rejection(self):
        pl = _pipeline()
        res = await pl.process(_raw("agent-a", "metrics.bad", "v1", confidence=-0.5))
        assert res.status == "rejected"
        snap = compute_metrics_snapshot(get_metrics_registry())
        assert snap["counters"]["writes.status.rejected"] == 1.0
        assert snap["rates"]["gate_rejection_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_latency_histogram_recorded(self):
        pl = _pipeline()
        await pl.process(_raw("agent-a", "metrics.lat", "v1"))
        hist = get_metrics_registry().snapshot()["histograms"]["pipeline.latency_ms"]
        assert len(hist) == 1
        assert hist[0] >= 0.0

    @pytest.mark.asyncio
    async def test_trust_update_counter(self):
        pl = _pipeline()
        pl.record_verification("agent-a", correct=True)
        assert get_metrics_registry().snapshot()["counters"]["trust.updates"] == 1.0

    @pytest.mark.asyncio
    async def test_lineage_node_counter(self):
        pl = _pipeline()
        await pl.process(_raw("agent-a", "metrics.lineage", "v1"))
        snap = get_metrics_registry().snapshot()["counters"]
        assert snap["lineage.nodes_recorded"] == 1.0


# ---------------------------------------------------------------------------
# Crypto verifier instrumentation
# ---------------------------------------------------------------------------


class TestCryptoInstrumentation:
    @pytest.fixture(autouse=True)
    def _dev_key(self, monkeypatch):
        monkeypatch.setenv("CRT_ALLOW_DEV_EVIDENCE_KEY", "1")
        reset_replay_guard()
        yield
        reset_replay_guard()

    def _counters(self):
        return get_metrics_registry().snapshot()["counters"]

    def test_valid_signature_counts_verification_only(self):
        sig = sign_evidence_message(EvidenceType.DATABASE, "db", assertion_hash="h")
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig,
            assertion_hash="h", reference_time=REF,
        ) is True
        assert self._counters()["evidence.verification.total"] == 1.0
        assert self._counters().get("evidence.replay_rejected", 0.0) == 0.0
        assert self._counters().get("evidence.temporal_rejected", 0.0) == 0.0

    def test_garbage_signature_increments_invalid(self):
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", "not-a-signature",
            reference_time=REF,
        ) is False
        assert self._counters()["evidence.signature_invalid"] == 1.0

    def test_replay_increments_replay_rejected(self):
        nonce = "nonce-metrics-replay"
        issued = "2026-07-14T09:00:00"
        expires = "2026-07-14T11:00:00"
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db",
            assertion_hash="h", nonce=nonce,
            issued_at=issued, expires_at=expires,
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig,
            assertion_hash="h", nonce=nonce,
            issued_at=issued, expires_at=expires,
            reference_time=REF,
        ) is True
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig,
            assertion_hash="h", nonce=nonce,
            issued_at=issued, expires_at=expires,
            reference_time=REF,
        ) is False
        assert self._counters()["evidence.replay_rejected"] == 1.0

    def test_expired_signature_increments_temporal_rejected(self):
        issued = "2026-07-14T08:00:00"
        expires = "2026-07-14T09:00:00"
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db",
            assertion_hash="h",
            issued_at=issued, expires_at=expires,
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig,
            assertion_hash="h", issued_at=issued, expires_at=expires,
            reference_time=REF,
        ) is False
        assert self._counters()["evidence.temporal_rejected"] == 1.0
