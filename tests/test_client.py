"""
Tests for the LCM HTTP client SDK (lcm_client.client).

These tests verify that LCMClient.write() forwards the evidence-binding
and multi-agent-coherence fields to the HTTP /write endpoint, mirroring the
server-side WriteRequest model and the direct-core pipeline benchmarks.

A fake httpx.Client is monkeypatched in so no live service is required.
"""

from __future__ import annotations

import inspect

import pytest

from lcm_client import LCMClient
import lcm_client.client as client_module


# ---------------------------------------------------------------------------
# Fake httpx transport layer
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 201
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client that records POST payloads."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.calls: list = []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, params=None):
        self.calls.append({"method": "POST", "url": str(url), "json": json})
        return _FakeResponse({
            "status": "committed",
            "provenance_id": "test-prov-1",
            "message": "Packet committed (no prior memory at this path).",
            "winner_agent": None,
            "loser_agent": None,
            "unresolved": False,
        })

    def get(self, url, params=None):
        self.calls.append({"method": "GET", "url": str(url), "params": params})
        return _FakeResponse({"service": "lcm", "status": "operational"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    """Patch httpx.Client inside the lcm_client module and return an LCMClient."""
    _FakeClient.instances = []
    monkeypatch.setattr(client_module.httpx, "Client", _FakeClient)
    return LCMClient(base_url="http://testserver")


def _last_payload() -> dict:
    """Return the JSON body from the most recent POST made by the client."""
    instance = _FakeClient.instances[-1]
    post_calls = [c for c in instance.calls if "json" in c]
    assert post_calls, "no POST was recorded"
    return post_calls[-1]["json"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_write_exposes_evidence_signature_in_payload(client):
    """evidence_signature must be forwarded to the server."""
    client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
        evidence_signature="sig_abc123",
    )
    payload = _last_payload()
    assert payload["evidence_signature"] == "sig_abc123"


def test_write_exposes_multi_agent_coherence_fields(client):
    """agreeing_agents, total_independent_agents and verified_memories_consistent
    must be forwarded to the server."""
    client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
        agreeing_agents=3,
        total_independent_agents=5,
        verified_memories_consistent=True,
    )
    payload = _last_payload()
    assert payload["agreeing_agents"] == 3
    assert payload["total_independent_agents"] == 5
    assert payload["verified_memories_consistent"] is True


def test_write_includes_verified_memories_consistent_false(client):
    """False is a meaningful explicit value and must be sent (not dropped as falsy)."""
    client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
        verified_memories_consistent=False,
    )
    payload = _last_payload()
    assert "verified_memories_consistent" in payload
    assert payload["verified_memories_consistent"] is False


def test_write_omits_defaults_when_not_provided(client):
    """When the new fields are not supplied, they must not pollute the payload
    (preserving backward-compatible behaviour)."""
    client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
    )
    payload = _last_payload()
    assert "evidence_signature" not in payload
    assert "agreeing_agents" not in payload
    assert "total_independent_agents" not in payload
    assert "verified_memories_consistent" not in payload


def test_write_returns_server_response(client):
    """The parsed server response dict is returned to the caller."""
    result = client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
        evidence_signature="sig_abc123",
    )
    assert result["status"] == "committed"
    assert result["provenance_id"] == "test-prov-1"


def test_write_defaults_match_server_model():
    """The client signature defaults must match the server WriteRequest defaults
    so direct-core and HTTP experiments exercise the same feature set."""
    sig = inspect.signature(LCMClient.write)
    assert sig.parameters["evidence_signature"].default is None
    assert sig.parameters["agreeing_agents"].default == 0
    assert sig.parameters["total_independent_agents"].default == 0
    assert sig.parameters["verified_memories_consistent"].default is None


def test_write_full_feature_payload(client):
    """A fully-featured write mirrors the server WriteRequest schema."""
    client.write(
        agent_id="agent_a",
        session_id="sess_1",
        confidence_score=0.9,
        assertion_payload={"temperature": 36.5},
        evidence_records=[{"type": "database", "source": "postgres", "relevance": 1.0}],
        agreeing_agents=3,
        total_independent_agents=5,
        verified_memories_consistent=True,
        evidence_signature="sig_abc123",
        domain="healthcare",
    )
    payload = _last_payload()
    assert payload["evidence_signature"] == "sig_abc123"
    assert payload["agreeing_agents"] == 3
    assert payload["total_independent_agents"] == 5
    assert payload["verified_memories_consistent"] is True
    assert payload["domain"] == "healthcare"
    assert payload["evidence_records"] == [
        {"type": "database", "source": "postgres", "relevance": 1.0}
    ]
