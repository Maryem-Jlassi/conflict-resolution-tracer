"""
LCM Configuration

Centralized configuration for LCM middleware components.

This module provides default values for configurable parameters that may be tuned
for evaluation purposes. The default pipeline uses these values directly.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from .confidence_engine import EVIDENCE_AUTHORITY, EvidenceType


@dataclass
class PsiWeightsConfig:
    """
    Default weights for the Ψ conflict resolution formula.
    
    Ψ = w_r * Recency + w_c * Confidence + w_t * Trust + w_p * Provenance
    
    All weights must sum to 1.0.
    
    Note on uncertainty_threshold:
    - Production default: 0.05 (enables unresolved for close scores)
    - Cold-start tests: 0.0 (deterministic tie-breaking for regression)
    - Labeled conflicts: 0.0 by default (can be overridden for unresolved tests)
    """
    w_recency: float = 0.25
    w_confidence: float = 0.25
    w_trust: float = 0.25
    w_provenance: float = 0.25
    decay_lambda: Optional[float] = None  # None → 24 h half-life
    uncertainty_threshold: float = 0.05  # |ΨA - ΨB| < this → unresolved
    
    def validate(self) -> None:
        """Validate that weights sum to 1.0."""
        total = self.w_recency + self.w_confidence + self.w_trust + self.w_provenance
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"PsiWeightsConfig weights must sum to 1.0, got {total:.4f}"
            )


@dataclass
class EvidenceAuthorityConfig:
    """
    Default authority weights for evidence types.

    These mirror ``lcm_core.confidence_engine.EVIDENCE_AUTHORITY`` — the single
    runtime source of truth for evidence authority. This config class exists for
    documentation, serialization and the Inspector, and MUST NOT drift from the
    engine table (the doc-consistency test enforces this).

    Semantics (see LIMITATIONS.md / README):
    * ``agent_claim`` / ``agent_claim_default`` are the low-authority fallback
      for unsupported agent output. A raw LLM self-reported ``confidence_score``
      never elevates them.
    * ``user_input`` requires gateway attestation (a valid evidence signature);
      without it the write is degraded to ``agent_claim_default``.
    """
    user_input: float = EVIDENCE_AUTHORITY[EvidenceType.USER_INPUT]
    database: float = EVIDENCE_AUTHORITY[EvidenceType.DATABASE]
    document: float = EVIDENCE_AUTHORITY[EvidenceType.DOCUMENT]
    tool_output: float = EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT]
    agent_claim: float = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]
    agent_claim_default: float = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]

    def to_engine_table(self) -> Dict[EvidenceType, float]:
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


# ── Confidence semantics ────────────────────────────────────────────────────
# Single place for the authority/confidence values used when agent output is
# unsupported or evidence is unsigned. Raw ``confidence_score`` is stored for
# audit/display only and never feeds conflict resolution or admission gates —
# those consume ``provenance_info.verified_confidence``.
AGENT_CLAIM_DEFAULT_AUTHORITY = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]
AGENT_CLAIM_DEFAULT_CONFIDENCE = EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM_DEFAULT]

# Fail-closed floor: external evidence present but its Ed25519 signature is
# missing/invalid → never allow unsigned evidence to claim elevated status.
UNVERIFIED_AUTHORITY_FALLBACK = 0.1
UNVERIFIED_CONFIDENCE_FALLBACK = 0.1


@dataclass
class LCMConfig:
    """
    Central configuration for LCM middleware.
    
    This consolidates all configurable parameters that might be tuned
    for evaluation purposes.
    """
    psi_weights: PsiWeightsConfig = None
    evidence_authority: EvidenceAuthorityConfig = None
    
    # Trust-based rejection thresholds
    TRUST_REJECT_THRESHOLD: float = 0.0  # Reject writes from agents with trust below this
    HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD: float = 0.9  # High confidence threshold for untrusted agents
    LOW_TRUST_THRESHOLD: float = 0.3  # Low trust threshold for evidence requirements
    
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
                "w_provenance": self.psi_weights.w_provenance,
                "decay_lambda": self.psi_weights.decay_lambda,
                "uncertainty_threshold": self.psi_weights.uncertainty_threshold,
            },
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

# Export constants for backward compatibility
TRUST_REJECT_THRESHOLD = DEFAULT_CONFIG.TRUST_REJECT_THRESHOLD
HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD = DEFAULT_CONFIG.HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD
LOW_TRUST_THRESHOLD = DEFAULT_CONFIG.LOW_TRUST_THRESHOLD
