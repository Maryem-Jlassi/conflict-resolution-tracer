"""
LCM Adapters for multi-agent frameworks.

Provides a unified interface to integrate LCM with AutoGen, LangGraph, CrewAI, etc.
Each adapter handles framework-specific communication patterns.

**Experimental — fail-closed source labeling.**

An agent cannot self-assign an elevated ``source_type`` (``user_input``,
``database``, ``tool_output``, ``document``). Those authority levels are only
honored when the write carries a valid Ed25519 ``evidence_signature`` that
verifies against the configured public key (``LCM_EVIDENCE_PUBLIC_KEY``, or the
explicit dev key behind ``LCM_ALLOW_DEV_EVIDENCE_KEY=1`` in tests). Without a
valid signature the write is recorded as ``agent_claim_default`` (authority
0.3), mirroring the fail-closed behavior of
:func:`lcm_core.provenance.validate_and_stamp`.

Only the trusted integration layer (framework adapter code, application code
holding the provider key) may attach signatures. Use
:func:`sign_memory_write` in tests/dev; production integrations must sign with
the real provider key over the same canonical message.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import warnings

from lcm_core.crypto import verify_evidence_signature_crypto
from lcm_core.schema import StampedUMF, ProvenanceInfo
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.confidence_engine import ConfidenceEngine, EvidenceType, EVIDENCE_AUTHORITY
from lcm_core.canonical import canonical_bytes, canonical_sha256


@dataclass
class MemoryWrite:
    """Represents a memory write from an agent."""
    agent_id: str
    key: str
    value: Any
    source_type: str  # "database", "agent_claim", "tool_output", etc.
    timestamp: datetime
    confidence_score: float = 0.7
    evidence_signature: Optional[str] = None  # required for elevated source types


# ---------------------------------------------------------------------------
# Fail-closed source labeling helpers
# ---------------------------------------------------------------------------

_SOURCE_TYPE_TO_EVIDENCE: Dict[str, EvidenceType] = {
    "user_input": EvidenceType.USER_INPUT,
    "database": EvidenceType.DATABASE,
    "db": EvidenceType.DATABASE,
    "tool_output": EvidenceType.TOOL_OUTPUT,
    "tool": EvidenceType.TOOL_OUTPUT,
    "document": EvidenceType.DOCUMENT,
    "doc": EvidenceType.DOCUMENT,
    "agent_claim": EvidenceType.AGENT_CLAIM,
    "agent_claim_default": EvidenceType.AGENT_CLAIM_DEFAULT,
    "llm": EvidenceType.AGENT_CLAIM,
    "agent": EvidenceType.AGENT_CLAIM,
}

_AGENT_CLAIM_DEFAULT_AUTHORITY = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]


def _evidence_type_for_source_type(source_type: Optional[str]) -> EvidenceType:
    if not source_type:
        return EvidenceType.AGENT_CLAIM
    return _SOURCE_TYPE_TO_EVIDENCE.get(source_type.lower(), EvidenceType.AGENT_CLAIM)


def _is_elevated(ev: EvidenceType) -> bool:
    return EVIDENCE_AUTHORITY[ev] > _AGENT_CLAIM_DEFAULT_AUTHORITY


def memory_write_needs_signature(source_type: Optional[str]) -> bool:
    """True if an elevated ``source_type`` requires a verifiable evidence signature."""
    return _is_elevated(_evidence_type_for_source_type(source_type))


def _content_hash(write: "MemoryWrite") -> str:
    return canonical_sha256(write.value)


def _assertion_hash(write: "MemoryWrite") -> str:
    return canonical_sha256({write.key: write.value})


def sign_memory_write(write: "MemoryWrite") -> str:
    """
    Dev/test helper: produce a valid Ed25519 signature for a ``MemoryWrite``
    using the development key. NOT for production — production integrations
    must sign with the trusted provider's real private key over the same
    canonical message (evidence_type, source_id=key, content_hash,
    assertion_hash). Requires ``benchmark_dev_evidence_key()`` (or a configured
    ``LCM_EVIDENCE_PUBLIC_KEY``) at verification time.
    """
    from lcm_core.crypto import sign_evidence_message

    ev = _evidence_type_for_source_type(write.source_type)
    return sign_evidence_message(
        ev, write.key,
        content_hash=_content_hash(write),
        assertion_hash=_assertion_hash(write),
    )


@dataclass
class MemoryConflict:
    """Represents a detected conflict in memory."""
    key: str
    existing_agent: str
    incoming_agent: str
    existing_value: Any
    incoming_value: Any
    resolution: str  # "existing_won", "incoming_won", "unresolved"
    reason: str


class LCMMemoryStore(ABC):
    """Abstract base for LCM-backed memory stores across frameworks."""

    def __init__(self):
        self.engine = ConflictResolutionEngine(uncertainty_threshold=0.0)
        self.trust_mgr = TrustManager()
        self.confidence_engine = ConfidenceEngine()
        self.memory: Dict[str, StampedUMF] = {}
        self.conflicts_log: List[MemoryConflict] = []

    def write(self, write: MemoryWrite) -> Optional[MemoryConflict]:
        """
        Write to memory with conflict detection.

        Returns:
            MemoryConflict if a conflict was detected and resolved, None if no conflict.
        """
        key = write.key

        # Fail-closed source labeling: elevated source types (database,
        # tool_output, document, user_input) are honored ONLY when backed by a
        # valid Ed25519 evidence signature; otherwise they are recorded as
        # agent_claim_default so an agent cannot self-elevate its authority.
        ev_type = _evidence_type_for_source_type(write.source_type)
        if _is_elevated(ev_type):
            if not (write.evidence_signature and verify_evidence_signature_crypto(
                ev_type, write.key, write.evidence_signature,
                content_hash=_content_hash(write),
                assertion_hash=_assertion_hash(write),
            )):
                warnings.warn(
                    f"Elevated source_type '{write.source_type}' for key '{write.key}' "
                    "has no valid evidence signature; recording as agent_claim_default. "
                    "Provide a matching evidence_signature to elevate authority.",
                    stacklevel=2,
                )
                ev_type = EvidenceType.AGENT_CLAIM_DEFAULT
        source_type = ev_type.value

        # Compute verified confidence from the (possibly degraded) source type
        verified_conf = self.confidence_engine.score_from_source_type(source_type)

        incoming = StampedUMF(
            agent_id=write.agent_id,
            session_id="multi_agent_session",
            timestamp=write.timestamp,
            confidence_score=write.confidence_score,
            assertion_payload={key: write.value},
            provenance_id=f"prov_{key}_{write.agent_id}_{write.timestamp.isoformat()}",
            ingested_at=write.timestamp,
            provenance_info=ProvenanceInfo(
                verified_confidence=verified_conf,
                authority_score=self.confidence_engine.authority_score_from_source_type(source_type),
                source_type=source_type,
            ),
        )

        # Check for conflict
        if key not in self.memory:
            self.memory[key] = incoming
            return None

        existing = self.memory[key]
        if existing.agent_id == write.agent_id:
            # Same agent updating → always replace
            self.memory[key] = incoming
            return None

        # Different agent → potential conflict, resolve with Ψ
        trust_table = {
            existing.agent_id: self.trust_mgr.get_trust(existing.agent_id),
            incoming.agent_id: self.trust_mgr.get_trust(incoming.agent_id),
        }

        # Use the later timestamp as reference for recency calculation
        reference_time = max(existing.timestamp, incoming.timestamp)

        result = self.engine.resolve_conflict(
            existing, incoming, trust_table, reference_time=reference_time
        )
        winner = result.winner
        loser = existing if winner == incoming else incoming

        # Update memory
        self.memory[key] = winner

        # Log conflict
        conflict = MemoryConflict(
            key=key,
            existing_agent=existing.agent_id,
            incoming_agent=incoming.agent_id,
            existing_value=existing.assertion_payload.get(key),
            incoming_value=incoming.assertion_payload.get(key),
            resolution="incoming_won" if winner == incoming else "existing_won",
            reason=result.reason,
        )
        self.conflicts_log.append(conflict)

        return conflict

    def read(self, key: str) -> Optional[Any]:
        """Read from memory."""
        if key in self.memory:
            umf = self.memory[key]
            return umf.assertion_payload.get(key)
        return None

    def record_outcome(self, agent_id: str, was_correct: bool):
        """Record agent reliability for trust updates."""
        self.trust_mgr.record_outcome(agent_id, correct=was_correct)

    def get_conflicts(self) -> List[MemoryConflict]:
        """Get all logged conflicts."""
        return self.conflicts_log

    @abstractmethod
    def integrate_with_framework(self):
        """Framework-specific integration logic."""
        pass


class AutoGenLCMAdapter(LCMMemoryStore):
    """Adapter for Microsoft AutoGen framework."""

    def __init__(self):
        super().__init__()
        self.agent_map = {}

    def integrate_with_framework(self):
        pass

    def register_agent(self, agent_name: str, agent_id: str):
        self.agent_map[agent_name] = agent_id


class LangGraphLCMAdapter(LCMMemoryStore):
    """Adapter for LangGraph framework."""

    def __init__(self):
        super().__init__()
        self.graph_state = {}

    def integrate_with_framework(self):
        pass

    def sync_graph_state(self, agent_id: str, updates: Dict[str, Any]):
        now = datetime.utcnow()
        for key, value in updates.items():
            write = MemoryWrite(
                agent_id=agent_id,
                key=key,
                value=value,
                source_type="agent_claim",
                timestamp=now,
            )
            self.write(write)


class CrewAILCMAdapter(LCMMemoryStore):
    """Adapter for CrewAI framework."""

    def __init__(self):
        super().__init__()
        self.crew_memory = {}

    def integrate_with_framework(self):
        pass

    def sync_task_output(self, agent_id: str, task_key: str, output: str):
        write = MemoryWrite(
            agent_id=agent_id,
            key=task_key,
            value=output,
            source_type="tool_output",
            timestamp=datetime.utcnow(),
        )
        self.write(write)
