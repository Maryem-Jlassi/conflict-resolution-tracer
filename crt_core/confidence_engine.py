"""
Confidence Engine - middleware-calculated evidence-based confidence scores.

Replaces self-reported LLM confidence with externally verifiable signals.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class EvidenceType(str, Enum):
    """Source types ranked by authority."""
    USER_INPUT = "user_input"           # Verified user input / authoritative record
    DATABASE = "database"               # Deterministic DB / tool output
    DOCUMENT = "document"               # Document or file reference
    TOOL_OUTPUT = "tool_output"         # External tool call result
    AGENT_CLAIM = "agent_claim"         # Unsupported LLM-only claim (lowest)
    AGENT_CLAIM_DEFAULT = "agent_claim_default"  # Default fallback when no evidence supplied


# Default authority weights per evidence type (cold-start priority order)
EVIDENCE_AUTHORITY: Dict[EvidenceType, float] = {
    EvidenceType.USER_INPUT:         1.0,
    EvidenceType.DATABASE:           0.9,
    EvidenceType.TOOL_OUTPUT:        0.85,
    EvidenceType.DOCUMENT:           0.75,
    EvidenceType.AGENT_CLAIM:        0.3,
    EvidenceType.AGENT_CLAIM_DEFAULT: 0.3,  # Same as AGENT_CLAIM
}


@dataclass
class EvidenceRecord:
    """
    Describes external evidence supporting a memory claim.

    Attach one or more of these to a UMF packet before ingestion
    so the ConfidenceEngine can derive a verified confidence score.
    """
    evidence_type: EvidenceType
    source_id: Optional[str] = None           # e.g. tool name, DB table, doc URI
    content_hash: Optional[str] = None        # SHA-256 of evidence content if available
    relevance_score: float = 1.0              # How relevant is this evidence (0-1)
    verified: bool = False                    # Has this been externally verified?
    issued_at: Optional[str] = None           # ISO-8601 evidence issue time (Phase 4)
    expires_at: Optional[str] = None          # ISO-8601 evidence expiry time (Phase 4)
    nonce: Optional[str] = None               # unique per-binding nonce (Phase 5)
    provider_id: Optional[str] = None         # evidence provider identity (Phase 5/6)
    key_id: Optional[str] = None              # provider key identifier (Phase 5/6)
    independence_group: Optional[str] = None  # independently controlled source group
    verification_method: Optional[str] = None # declared method; authoritative status is middleware-derived


@dataclass
class ConfidenceWeights:
    """Configurable weights for the confidence formula."""
    evidence: float = 0.50
    agreement: float = 0.30
    verification: float = 0.20

    def validate(self) -> None:
        total = self.evidence + self.agreement + self.verification
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ConfidenceWeights must sum to 1.0, got {total:.4f}"
            )


class ConfidenceEngine:
    """
    Calculates middleware-verified confidence scores for memory claims.

    **Phase 1 / Trusted Integration Layer:**
    This implementation assumes evidence is supplied by a trusted integration layer
    (e.g., application code, trusted services) that has already verified the evidence
    before passing it to CRT. The middleware does NOT perform independent verification
    of evidence (e.g., calling databases or external tools to validate claims).

    Confidence is derived from external signals only:
        - Evidence availability and quality (from trusted integration layer)
        - Agreement across independent agents
        - Consistency with previously verified memories
        - Source authority / reliability

    The original agent-provided confidence_score on the UMF packet is
    intentionally ignored by this engine.

    **Future Work (Phase 2):**
    - Middleware-side evidence verification (direct DB/tool calls)
    - Evidence freshness tracking
    - Evidence chain-of-custody validation
    """

    def __init__(self, weights: Optional[ConfidenceWeights] = None):
        self.weights = weights or ConfidenceWeights()
        self.weights.validate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        evidence_records: List[EvidenceRecord],
        agreeing_agents: int = 0,
        total_independent_agents: int = 0,
        verified_memories_consistent: Optional[bool] = None,
    ) -> float:
        """
        Compute a verified confidence score in [0, 1].

        Args:
            evidence_records: External evidence attached to the claim.
            agreeing_agents: **AUDIT ONLY** - Number of independent agents that assert the same claim.
                This field is NOT used in confidence calculation and cannot inflate scores.
            total_independent_agents: **AUDIT ONLY** - Total agents that weighed in on this claim.
                This field is NOT used in confidence calculation and cannot inflate scores.
            verified_memories_consistent: **AUDIT ONLY** - Whether claim matches verified history.
                This field is NOT used in confidence calculation and cannot inflate scores.

        Returns:
            Verified confidence score in [0, 1] based solely on middleware-owned evidence.
        """
        evidence_score = self._evidence_score(evidence_records)
        agreement_score = 0.5
        verification_score = 0.5

        score = (
            self.weights.evidence * evidence_score
            + self.weights.agreement * agreement_score
            + self.weights.verification * verification_score
        )
        return min(1.0, max(0.0, score))

    def cold_start_confidence(
        self,
        evidence_records: List[EvidenceRecord],
    ) -> float:
        """
        Confidence when no agent history or previous memories are available.

        Falls back to evidence quality + source authority only.
        """
        return self._evidence_score(evidence_records)

    # ------------------------------------------------------------------
    # Internal scoring helpers
    # ------------------------------------------------------------------

    def _evidence_score(self, records: List[EvidenceRecord]) -> float:
        """Score based on best available evidence authority × relevance."""
        if not records:
            # No evidence supplied → treat as default agent claim (audit semantics)
            return EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]

        # Use the highest-authority evidence record weighted by its relevance
        best = max(
            records,
            key=lambda r: EVIDENCE_AUTHORITY[r.evidence_type] * r.relevance_score,
        )
        authority = EVIDENCE_AUTHORITY[best.evidence_type]
        return authority * best.relevance_score

    def score_from_source_type(self, source_type: Optional[str]) -> float:
        """
        Direct mapping: source_type string → confidence score.
        
        For use during fixture creation or when only source_type is available.
        Never uses agent-reported confidence.
        """
        if source_type is None:
            return EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]
        
        source_lower = source_type.lower()
        
        # Map common source strings to EvidenceType
        type_map = {
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
        
        evidence_type = type_map.get(source_lower, EvidenceType.AGENT_CLAIM)
        return EVIDENCE_AUTHORITY[evidence_type]

    def authority_score_from_source_type(self, source_type: Optional[str]) -> float:
        """
        Map source type to evidence authority score for ProvenanceInfo.authority_score.
        
        This is the SAME as score_from_source_type() - they both use evidence authority.
        Provided as explicit method for clarity in benchmark code.
        """
        return self.score_from_source_type(source_type)

    def _agreement_score(self, agreeing: int, total: int) -> float:
        """Agreement ratio among independent agents."""
        if total <= 0:
            return 0.5  # No data → neutral
        return agreeing / total

    def _verification_score(self, consistent: Optional[bool]) -> float:
        """Score based on consistency with verified memory history."""
        if consistent is None:
            return 0.5   # Unknown → neutral
        return 1.0 if consistent else 0.0
