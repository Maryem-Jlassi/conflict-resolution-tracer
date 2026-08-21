from datetime import datetime
from typing import Any, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, field_validator, ConfigDict
from uuid import uuid4

if TYPE_CHECKING:
    from .confidence_engine import EvidenceRecord


class UMF(BaseModel):
    """Unified Memory Format - the core data packet for CRT."""

    agent_id: str = Field(..., min_length=1, description="Identifier of the agent making the assertion")
    session_id: str = Field(..., min_length=1, description="Session or thread identifier")
    timestamp: datetime = Field(..., description="When the assertion was made")
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="Optional agent-provided confidence estimate (stored for transparency only; never used for conflict resolution)"
    )
    assertion_payload: dict = Field(..., description="The actual memory claim as a structured dict")
    media_uri: Optional[str] = Field(None, description="Optional URI for multi-modal content")
    media_hash: Optional[str] = Field(None, description="Optional SHA-256 hash of referenced media")

    @field_validator("assertion_payload")
    @classmethod
    def validate_payload(cls, v: Any) -> dict:
        if not isinstance(v, dict):
            raise ValueError("assertion_payload must be a dictionary")
        if not v:
            raise ValueError("assertion_payload cannot be empty")
        return v

    @property
    def reported_confidence(self) -> float:
        """Compatibility alias for the agent's self-reported confidence.

        Stored for audit/display only. Authoritative conflict resolution and
        admission gates consume ``provenance_info.verified_confidence``, never
        this raw LLM self-report.
        """
        return self.confidence_score

    # Schema hardening (Track P1): fail closed on unexpected fields instead of
    # silently dropping them, matching the HTTP ingress model.
    model_config = ConfigDict(extra="forbid")


class ProvenanceInfo(BaseModel):
    """Rich provenance metadata attached to every stamped memory."""

    source_type: Optional[str] = Field(
        None,
        description="Origin type: user_input, database, tool_output, document, agent_claim"
    )
    source_id: Optional[str] = Field(
        None,
        description="Specific source identifier (tool name, DB table, document URI, etc.)"
    )
    authority_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Evidence authority (0.3-1.0): how reliable the source is (separate from verified_confidence)"
    )
    verification_status: str = Field(
        "unverified",
        description="One of: unverified, verified, contradicted, replaced"
    )
    memory_status: str = Field(
        "active",
        description="Storage role: active | pending_conflict | archived | superseded_pending | coalesced_pending",
    )
    parent_memory_ids: List[str] = Field(
        default_factory=list,
        description="provenance_ids of memories this one was derived from"
    )
    transformations: List[str] = Field(
        default_factory=list,
        description="Ordered list of transformation labels applied"
    )
    domain: Optional[str] = Field(
        None,
        description="Task domain (e.g. healthcare, finance, coding)"
    )
    verified_confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Confidence calculated by ConfidenceEngine, not the LLM"
    )
    valid_until: Optional[datetime] = Field(
        None,
        description="Expiry timestamp after which the memory should be re-verified"
    )
    confidence_signals: List[dict[str, Any]] = Field(
        default_factory=list,
        description="Auditable confidence-signal trust boundaries and verification results",
    )
    reported_agreeing_agents: int = 0
    reported_total_independent_agents: int = 0
    reported_memories_consistent: Optional[bool] = None
    
    model_config = ConfigDict(extra="allow")  # Allow extra fields like evidence_records


class StampedUMF(BaseModel):
    """UMF with server-side provenance stamps."""

    agent_id: str
    session_id: str
    timestamp: datetime
    confidence_score: float
    assertion_payload: dict
    media_uri: Optional[str] = None
    media_hash: Optional[str] = None

    provenance_id: str = Field(..., description="Immutable UUID assigned at ingestion")
    ingested_at: datetime = Field(..., description="Server timestamp when packet was received")

    provenance_info: ProvenanceInfo = Field(
        default_factory=ProvenanceInfo,
        description="Extended lineage and evidence metadata"
    )
    
    content_hash: Optional[str] = Field(None, description="SHA-256 hash of content for tamper detection")

    @property
    def reported_confidence(self) -> float:
        """Compatibility alias for the agent's self-reported confidence.

        Stored for audit/display only. Authoritative conflict resolution and
        admission gates consume ``provenance_info.verified_confidence``, never
        this raw LLM self-report.
        """
        return self.confidence_score

    # Schema hardening (Track P1): fail closed on unexpected fields inside the
    # persisted stamped object, matching the HTTP ingress model.
    model_config = ConfigDict(frozen=True, extra="forbid")
