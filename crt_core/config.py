"""
CRT Configuration V1

Centralized configuration for V1 conflict resolution formula:
Ψ = (R + C + T) / 3

Provenance is a mandatory Stage-1 audit layer and is NOT a component of Ψ.
"""

from dataclasses import dataclass
from typing import Optional

from .confidence_engine import EVIDENCE_AUTHORITY, EvidenceType
from .numeric import numeric_specification, serialize_score

AGENT_CLAIM_DEFAULT_AUTHORITY: float = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]
AGENT_CLAIM_DEFAULT_CONFIDENCE: float = 0.3
UNVERIFIED_AUTHORITY_FALLBACK: float = 0.1
UNVERIFIED_CONFIDENCE_FALLBACK: float = 0.1

TRUST_REJECT_THRESHOLD: float = 0.0
HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD: float = 0.9
LOW_TRUST_THRESHOLD: float = 0.3


@dataclass
class PsiWeightsConfig:
    """
    Default weights for the V1 Ψ conflict resolution formula.
    
    Ψ = (R + C + T) / 3
    
    All weights must sum to 1.0. Provenance is NOT included in Ψ.
    """
    w_recency: float = 1/3
    w_confidence: float = 1/3
    w_trust: float = 1/3
    decay_lambda: Optional[float] = None  # None → 24 h half-life
    uncertainty_threshold: float = 0.05  # |ΨA - ΨB| < this → unresolved
    
    def validate(self) -> None:
        """Validate that weights sum to 1.0."""
        total = self.w_recency + self.w_confidence + self.w_trust
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"PsiWeightsConfig weights must sum to 1.0, got {total:.4f}"
            )


@dataclass
class EvidenceAuthorityConfig:
    """
    Default authority weights for evidence types.

    These mirror ``crt_core.confidence_engine.EVIDENCE_AUTHORITY`` — the single
    runtime source of truth for evidence authority. This config class exists for
    documentation, serialization and the Inspector, and MUST NOT drift from the
    engine table (the doc-consistency test enforces this).
    """
    user_input: float = EVIDENCE_AUTHORITY[EvidenceType.USER_INPUT]
    database: float = EVIDENCE_AUTHORITY[EvidenceType.DATABASE]
    document: float = EVIDENCE_AUTHORITY[EvidenceType.DOCUMENT]
    tool_output: float = EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT]
    agent_claim: float = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]
    agent_claim_default: float = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]

    def to_engine_table(self) -> dict:
        """Return a copy of the authority table as {EvidenceType: authority}."""
        return {
            EvidenceType.USER_INPUT: self.user_input,
            EvidenceType.DATABASE: self.database,
            EvidenceType.DOCUMENT: self.document,
            EvidenceType.TOOL_OUTPUT: self.tool_output,
            EvidenceType.AGENT_CLAIM: self.agent_claim,
            EvidenceType.AGENT_CLAIM_DEFAULT: self.agent_claim_default,
        }

    def validate(self) -> None:
        """Validate that all weights are in [0, 1]."""
        for name, value in self.to_engine_table().items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"EvidenceAuthorityConfig.{name.value} must be in [0, 1], got {value}"
                )


@dataclass
class LCMConfig:
    """
    Central configuration for V1 CRT middleware.
    """
    psi_weights: PsiWeightsConfig = None
    evidence_authority: EvidenceAuthorityConfig = None
    
    TRUST_REJECT_THRESHOLD: float = 0.0
    HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD: float = 0.9
    LOW_TRUST_THRESHOLD: float = 0.3
    
    def __post_init__(self):
        if self.psi_weights is None:
            self.psi_weights = PsiWeightsConfig()
        if self.evidence_authority is None:
            self.evidence_authority = EvidenceAuthorityConfig()
        
        self.psi_weights.validate()
        self.evidence_authority.validate()
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "psi_weights": {
                "w_recency": self.psi_weights.w_recency,
                "w_confidence": self.psi_weights.w_confidence,
                "w_trust": self.psi_weights.w_trust,
                "decay_lambda": self.psi_weights.decay_lambda,
                "uncertainty_threshold": self.psi_weights.uncertainty_threshold,
                "uncertainty_threshold_exact": serialize_score(self.psi_weights.uncertainty_threshold),
            },
            "numeric_semantics": numeric_specification(),
            "evidence_authority": {
                "user_input": self.evidence_authority.user_input,
                "database": self.evidence_authority.database,
                "document": self.evidence_authority.document,
                "tool_output": self.evidence_authority.tool_output,
                "agent_claim": self.evidence_authority.agent_claim,
                "agent_claim_default": self.evidence_authority.agent_claim_default,
            },
            "trust_thresholds": {
                "TRUST_REJECT_THRESHOLD": self.TRUST_REJECT_THRESHOLD,
                "HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD": self.HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD,
                "LOW_TRUST_THRESHOLD": self.LOW_TRUST_THRESHOLD,
            },
        }


# Default singleton instance
DEFAULT_CONFIG = LCMConfig()
