"""CRT Client SDK implementation."""

import httpx
import hashlib
import hmac
import json
import os
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime


class CRTClient:
    """
    Framework-agnostic client for Conflict Resolution Tracer service.

    Uses httpx only - no dependency on crt_core or agent frameworks.
    """

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def write(
        self,
        agent_id: str,
        session_id: str,
        confidence_score: float,
        assertion_payload: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        media_uri: Optional[str] = None,
        media_hash: Optional[str] = None,
        domain: Optional[str] = None,
        evidence_records: Optional[List[Dict[str, Any]]] = None,
        agreeing_agents: int = 0,
        total_independent_agents: int = 0,
        verified_memories_consistent: Optional[bool] = None,
        evidence_signature: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Write a memory assertion to CRT.

        Args:
            agent_id: Identifier of the agent making the assertion.
            session_id: Session or thread identifier.
            confidence_score: Optional agent-provided confidence estimate (0.0-1.0).
                              Stored only for transparency and debugging.
                              NEVER used for conflict resolution; the middleware computes
                              verified_confidence independently from evidence_records.
            assertion_payload: The memory claim as a dict.
            timestamp: When the assertion was made (defaults to now).
            media_uri: Optional URI for multimodal content.
            media_hash: Optional SHA-256 hash of media.
            domain: Task domain for domain-specific trust (e.g. 'healthcare').
            evidence_records: External evidence supporting this claim.
                Each record is a dict with keys:
                  type     : str  — one of user_input | database | tool_output |
                                     document | agent_claim
                  source   : str  — optional source identifier
                  relevance: float — 0.0-1.0, how relevant this evidence is
                  verified : bool  — whether the evidence is externally verified

                Example:
                  [{"type": "database", "source": "postgres", "relevance": 1.0}]
            agreeing_agents: Number of independent agents asserting the same claim.
            total_independent_agents: Total independent agents that weighed in on this claim.
            verified_memories_consistent: Whether this claim is consistent with
                verified prior memories. Influences verified_confidence via the
                ConfidenceEngine.
            evidence_signature: Cryptographic signature from a trusted Data Provider
                for evidence binding. Required for database/tool_output/document
                evidence types; the middleware verifies it before assigning
                authority_score. Without a valid signature, authority_score is
                degraded to 0.1.

        Returns:
            Response dict with status, provenance_id, message,
            winner_agent, loser_agent, unresolved.
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "session_id": session_id,
            "timestamp": timestamp.isoformat(),
            "confidence_score": confidence_score,
            "assertion_payload": assertion_payload,
        }

        if media_uri:
            payload["media_uri"] = media_uri
        if media_hash:
            payload["media_hash"] = media_hash
        if domain:
            payload["domain"] = domain
        if evidence_records:
            payload["evidence_records"] = evidence_records
        if agreeing_agents:
            payload["agreeing_agents"] = agreeing_agents
        if total_independent_agents:
            payload["total_independent_agents"] = total_independent_agents
        if verified_memories_consistent is not None:
            payload["verified_memories_consistent"] = verified_memories_consistent
        if evidence_signature is not None:
            payload["evidence_signature"] = evidence_signature

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/write", json=payload)
            response.raise_for_status()
            return response.json()

    def verify(
        self, agent_id: str, correct: bool, domain: str = "_global",
        *, outcome_id: Optional[str] = None,
        target_provenance_id: Optional[str] = None,
        observed_at: Optional[str] = None,
        verifier_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Feed an external verification result back to the TrustManager.

        Call this when ground-truth about a committed claim becomes available.

        Args:
            agent_id: The agent whose claim was verified.
            correct: True if the claim was correct, False otherwise.
            domain: Task domain the claim belongs to.
        """
        outcome_id = outcome_id or f"sdk-{uuid.uuid4()}"
        payload = {
            "outcome_id": outcome_id,
            "target_agent_id": agent_id,
            "correct": correct,
            "domain": domain,
            "target_provenance_id": target_provenance_id,
            "observed_at": observed_at,
        }
        if verifier_token is None:
            secret = os.environ.get("CRT_VERIFIER_SECRET")
            if not secret:
                raise RuntimeError("CRT_VERIFIER_SECRET is required for authenticated verification")
            # Mirrors the protocol's stable six-field canonical payload without
            # importing crt_core into the framework-neutral SDK.
            message = "|".join((outcome_id, agent_id, domain, "1" if correct else "0",
                                target_provenance_id or "", observed_at or ""))
            verifier_token = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        payload["verifier_token"] = verifier_token
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/verify",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def get_context(self, path: str) -> Dict[str, Any]:
        """
        Retrieve context for a given path.

        Returns facts including verified_confidence and verification_status.
        """
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/context/{path}")
            response.raise_for_status()
            return response.json()

    def get_trust(self, agent_id: str, domain: str = "_global") -> Dict[str, Any]:
        """
        Get the current trust score for an agent.

        Args:
            agent_id: Agent ID to query
            domain: Domain for domain-specific trust (default: _global)

        Returns:
            Trust score and related metadata
        """
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/trust/{agent_id}",
                params={"domain": domain},
            )
            response.raise_for_status()
            return response.json()

    def health_check(self) -> Dict[str, Any]:
        """Check if CRT service is operational."""
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/")
            response.raise_for_status()
            return response.json()
