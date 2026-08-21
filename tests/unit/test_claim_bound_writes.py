from datetime import datetime

import pytest
import httpx

from agents.crt_adapter import MemoryWrite, _assertion_hash, sign_memory_write
from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import (
    canonical_assertion_hash,
    sign_assertion_evidence,
    sign_evidence_message,
)
from crt_core.provenance import RejectionError, validate_and_stamp


TS = datetime(2026, 8, 7, 9, 30, 0)


def raw(agent="agent-a", key="scope.fact", value="alpha", timestamp=TS):
    return {"agent_id": agent, "session_id": "s", "timestamp": timestamp,
            "confidence_score": 0.5, "assertion_payload": {key: value}}


def signed(packet, domain=None):
    return sign_assertion_evidence(
        EvidenceType.DATABASE, "db://authoritative", agent_id=packet["agent_id"],
        timestamp=packet["timestamp"], assertion_payload=packet["assertion_payload"],
        domain=domain,
    )


def verify(packet, signature, domain=None):
    return validate_and_stamp(
        packet, domain=domain,
        evidence_records=[EvidenceRecord(EvidenceType.DATABASE, "db://authoritative")],
        evidence_signature=signature,
    )


def test_bound_signature_accepts_exact_direct_core_claim():
    packet = raw(); result = verify(packet, signed(packet))
    assert result.provenance_info.authority_score == pytest.approx(0.9)


@pytest.mark.parametrize("mutation", [
    lambda p: p["assertion_payload"].update({"scope.fact": "modified"}),
    lambda p: p.update(agent_id="agent-b"),
    lambda p: p.update(assertion_payload={"other.scope": "alpha"}),
    lambda p: p.update(timestamp=datetime(2026, 8, 7, 9, 30, 1)),
])
def test_bound_signature_rejects_modified_claim_fields(mutation):
    original = raw(); signature = signed(original); changed = raw(); mutation(changed)
    with pytest.raises(RejectionError, match="invalid for the received assertion"):
        verify(changed, signature)


def test_domain_is_bound_and_malformed_signature_rejected():
    packet = raw(); signature = signed(packet, "health")
    with pytest.raises(RejectionError): verify(packet, signature, "finance")
    with pytest.raises(RejectionError): verify(packet, "not-base64")


def test_empty_signature_never_increases_authority():
    packet = raw()
    result = verify(packet, None)
    assert result.provenance_info.authority_score <= 0.1
    assert result.provenance_info.verified_confidence <= 0.1


def test_legacy_unbound_v1_signature_cannot_bypass_claim_binding():
    packet = raw()
    legacy_signature = sign_evidence_message(
        EvidenceType.DATABASE, "db://authoritative"
    )
    with pytest.raises(RejectionError, match="invalid for the received assertion"):
        verify(packet, legacy_signature)


def test_missing_binding_fields_fail_before_signing():
    with pytest.raises(ValueError): canonical_assertion_hash("", TS, {"k": "v"})
    with pytest.raises(ValueError): canonical_assertion_hash("a", TS, {})


def test_agent_adapter_uses_identical_canonical_binding():
    write = MemoryWrite("agent-a", "scope.fact", "alpha", "database", TS)
    assert _assertion_hash(write) == canonical_assertion_hash(
        "agent-a", TS, {"scope.fact": "alpha"}
    )
    write.evidence_signature = sign_memory_write(write)
    changed = MemoryWrite("agent-a", "scope.fact", "modified", "database", TS,
                          evidence_signature=write.evidence_signature)
    from agents.crt_adapter import AutoGenCRTAdapter
    with pytest.raises(ValueError, match="invalid for the received assertion"):
        AutoGenCRTAdapter().write(changed)


def test_forged_agreement_and_consistency_are_audit_only():
    packet=raw()
    result=validate_and_stamp(packet,agreeing_agents=999,total_independent_agents=999,
                              verified_memories_consistent=True)
    assert result.provenance_info.verified_confidence == pytest.approx(0.3)
    assert result.provenance_info.reported_agreeing_agents == 999
    assert result.provenance_info.reported_memories_consistent is True


def test_verified_provider_signal_records_method_and_hash():
    packet=raw(); evidence_hash="b"*64
    signature=sign_assertion_evidence(
        EvidenceType.DATABASE,"db://authoritative",agent_id=packet["agent_id"],
        timestamp=packet["timestamp"],assertion_payload=packet["assertion_payload"],
        content_hash=evidence_hash,
    )
    result=validate_and_stamp(packet,evidence_records=[EvidenceRecord(
        EvidenceType.DATABASE,"db://authoritative",content_hash=evidence_hash,
        independence_group="operator-a")],evidence_signature=signature)
    signal=result.provenance_info.confidence_signals[0]
    assert signal["verification_status"] == "verified"
    assert signal["verification_method"] == "ed25519_v1"
    assert signal["independence_group"] == "operator-a"


@pytest.mark.asyncio
async def test_http_recomputes_binding_and_rejects_different_request():
    from crt_service.app import app, reset_for_testing
    reset_for_testing(); packet=raw(); signature=signed(packet)
    request={**packet,"timestamp":packet["timestamp"].isoformat(),
             "evidence_records":[{"type":"database","source":"db://authoritative"}],
             "evidence_signature":signature}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        accepted=await client.post("/write",json=request); assert accepted.status_code == 201
        changed={**request,"assertion_payload":{"scope.fact":"different"}}
        rejected=await client.post("/write",json=changed); assert rejected.status_code == 400
        assert "invalid for the received assertion" in rejected.text


def test_lcm_client_to_http_uses_same_binding(monkeypatch):
    import asyncio
    from crt_client.client import LCMClient
    from crt_service.app import app, reset_for_testing
    reset_for_testing(); packet=raw(); signature=signed(packet)
    class ClientProxy:
        def __init__(self,**_): pass
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def post(self,url,json):
            async def request():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://local") as client:
                    return await client.post(url.replace("http://local",""),json=json)
            return asyncio.run(request())
    monkeypatch.setattr(httpx,"Client",ClientProxy)
    response=LCMClient("http://local").write(
        packet["agent_id"],packet["session_id"],packet["confidence_score"],
        packet["assertion_payload"],timestamp=packet["timestamp"],
        evidence_records=[{"type":"database","source":"db://authoritative"}],
        evidence_signature=signature)
    assert response["status"] == "committed"
