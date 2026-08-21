"""
Integration tests — GET /metrics telemetry endpoint (Phase 12).

Exercises the HTTP surface: after pipeline writes and evidence-signature
verification through the service, the snapshot reflects write-outcome counters
and derived rates (gate rejection, conflict resolution, …).
"""

import httpx
import pytest
from datetime import datetime

from crt_core.confidence_engine import EvidenceType
from crt_core.crypto import sign_assertion_evidence
from crt_service.app import app, reset_for_testing


@pytest.fixture
def http_client():
    reset_for_testing()
    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _payload(agent, path, value, *, confidence=0.9, evidence=False, ts="2026-08-02T12:00:00Z"):
    payload = {
        "agent_id": agent,
        "session_id": "sess-metrics",
        "timestamp": ts,
        "confidence_score": confidence,
        "assertion_payload": {path: value},
    }
    if evidence:
        payload["evidence_records"] = [
            {"type": "database", "source": "db", "relevance": 1.0}
        ]
        payload["evidence_signature"] = sign_assertion_evidence(
            EvidenceType.DATABASE, "db", agent_id=agent, timestamp=ts,
            assertion_payload={path: value},
        )
    return payload


@pytest.mark.asyncio
async def test_metrics_endpoint_tracks_writes(http_client):
    r = await http_client.post("/write", json=_payload("a", "m.a", "v1"))
    assert r.status_code == 201
    r = await http_client.post("/write", json=_payload("a", "m.b", "v1"))
    assert r.status_code == 201
    # Rejected: non-dict assertion_payload fails UMF validation (pipeline 400)
    r = await http_client.post(
        "/write", json=_payload("a", "m.c", "v1") | {"assertion_payload": 42}
    )
    assert r.status_code == 400

    resp = await http_client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()

    assert data["counters"]["writes.total"] == 3.0
    assert data["counters"]["writes.status.committed"] == 2.0
    assert data["counters"]["writes.status.rejected"] == 1.0
    assert data["rates"]["gate_rejection_rate"] == pytest.approx(1 / 3)
    assert data["rates"]["commit_rate"] == pytest.approx(2 / 3)
    assert data["histograms"]["pipeline.latency_ms"]["count"] == 3
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_metrics_endpoint_reflects_conflict(http_client):
    await http_client.post("/write", json=_payload("a", "m.conf", "v1"))
    r = await http_client.post(
        "/write", json=_payload("b", "m.conf", "v1", evidence=True)
    )
    assert r.status_code == 201, r.text
    data = (await http_client.get("/metrics")).json()
    assert data["counters"]["writes.status.conflict_resolved"] == 1.0
    assert data["counters"]["conflicts.resolved"] == 1.0


@pytest.mark.asyncio
async def test_metrics_are_agent_agnostic(http_client):
    """No PII leak: counters must not contain agent/session identifiers."""
    await http_client.post("/write", json=_payload("sensitive-agent", "m.pii", "v1"))
    data = (await http_client.get("/metrics")).json()
    blob = str(data)
    assert "sensitive-agent" not in blob
    assert "sess-metrics" not in blob
