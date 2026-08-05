"""Algebraic conflict resolution engine - Ψ formula implementation."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Union
from decimal import Decimal, getcontext

from .schema import StampedUMF

# Set high precision for deterministic arithmetic
getcontext().prec = 28


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

@dataclass
class ResolutionConfig:
    """
    Configurable weights and thresholds for the Ψ formula.

    Ψ = w_r * Recency + w_c * Confidence + w_t * Trust + w_p * Provenance

    All weights must sum to 1.0. The uncertainty_threshold controls
    when a conflict is treated as too close to call.
    
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
        total = self.w_recency + self.w_confidence + self.w_trust + self.w_provenance
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ResolutionConfig weights must sum to 1.0, got {total:.4f}"
            )


# ------------------------------------------------------------------
# Result type
# ------------------------------------------------------------------

@dataclass
class ConflictResult:
    """Result of conflict resolution."""
    winner: StampedUMF
    loser: StampedUMF
    psi_winner: float
    psi_loser: float
    reason: str
    unresolved: bool = False   # True when scores are within uncertainty_threshold
    psi_winner_breakdown: dict = None  # Per-component scores for winner
    psi_loser_breakdown: dict = None    # Per-component scores for loser
    description: str = ""              # Human-readable adjudicator audit string


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------

class ConflictResolutionEngine:
    """
    Implements the Ψ formula for deterministic conflict resolution.

    Ψ = (w_r · Recency) + (w_c · C_verified) + (w_t · T_historical) + (w_p · P_authority)

    Where:
      w_r, w_c, w_t, w_p : configurable weights (default 0.25 each)
      Recency           : e^(-λΔt) - exponential decay with 24h half-life
      C_verified        : externally verified confidence from ProvenanceInfo
      T_historical      : observed agent reliability from TrustManager
      P_authority       : provenance authority (0.3=agent_claim, 1.0=user_input)
    """

    def __init__(
        self,
        w_recency: float = 0.25,
        w_confidence: float = 0.25,
        w_trust: float = 0.25,
        w_provenance: float = 0.25,
        decay_lambda: Optional[float] = None,
        uncertainty_threshold: float = 0.05,  # Production default: enables unresolved for close scores
        config: Optional[ResolutionConfig] = None,
        psi_weights: Optional[Dict[str, float]] = None,
    ):
        # Accept either individual params, a ResolutionConfig object, or a psi_weights dict
        if config is not None:
            config.validate()
            self.w_r = config.w_recency
            self.w_c = config.w_confidence
            self.w_t = config.w_trust
            self.w_p = config.w_provenance
            self.uncertainty_threshold = config.uncertainty_threshold
            lam = config.decay_lambda
        elif psi_weights is not None:
            # Accept dict format for convenience with labeled_conflicts
            self.w_r = psi_weights.get("recency", 0.25)
            self.w_c = psi_weights.get("confidence", 0.25)
            self.w_t = psi_weights.get("trust", 0.25)
            self.w_p = psi_weights.get("provenance", 0.25)
            self.uncertainty_threshold = uncertainty_threshold
            lam = decay_lambda

            # Normalize weights if they don't sum to 1.0 (for convenience)
            total = self.w_r + self.w_c + self.w_t + self.w_p
            if abs(total - 1.0) > 1e-6:
                # Normalize to sum to 1.0
                self.w_r = self.w_r / total
                self.w_c = self.w_c / total
                self.w_t = self.w_t / total
                self.w_p = self.w_p / total
        else:
            self.w_r = w_recency
            self.w_c = w_confidence
            self.w_t = w_trust
            self.w_p = w_provenance
            self.uncertainty_threshold = uncertainty_threshold
            lam = decay_lambda

        # λ such that e^(-λ × 86400) ≈ 0.5  (24-h half-life)
        self.lambda_ = lam if lam is not None else (-math.log(0.5) / 86400.0)

    # ------------------------------------------------------------------
    # Ψ calculation
    # ------------------------------------------------------------------

    def calculate_psi(
        self,
        umf: StampedUMF,
        trust_score: float,
        reference_time: Optional[datetime] = None,
    ) -> float:
        """
        Calculate Ψ score for a given UMF packet with deterministic arithmetic.

        Ψ = w_r * Recency + w_c * Confidence + w_t * Trust + w_p * Provenance

        Uses Decimal arithmetic for deterministic floating-point calculations.

        Args:
            umf: The stamped UMF packet.
            trust_score: Historical reliability score for the agent (0-1).
            reference_time: Time to measure recency from (defaults to now).

        Returns:
            Ψ score (0-1 range, ≈1 for fresh + confident + trusted + good provenance).
        
        Raises:
            ValueError: If verified_confidence or authority_score is None (must be pre-computed).
        """
        if reference_time is None:
            reference_time = datetime.utcnow()

        # Convert to Decimal for deterministic arithmetic
        w_r = Decimal(str(self.w_r))
        w_c = Decimal(str(self.w_c))
        w_t = Decimal(str(self.w_t))
        w_p = Decimal(str(self.w_p))
        lambda_d = Decimal(str(self.lambda_))

        # Recency component
        delta_t = max(0, (reference_time - umf.timestamp).total_seconds())
        recency = float(Decimal(str(math.exp(-float(lambda_d) * delta_t))))

        # Confidence component: externally verified confidence
        verified = umf.provenance_info.verified_confidence
        if verified is None:
            raise ValueError(
                f"verified_confidence must be pre-computed in ProvenanceInfo. "
                f"Agent {umf.agent_id} in session {umf.session_id} has None. "
                f"Use ConfidenceEngine to compute it before ingestion."
            )

        # Provenance component: evidence authority (separate from confidence)
        authority = umf.provenance_info.authority_score
        if authority is None:
            raise ValueError(
                f"authority_score must be pre-computed in ProvenanceInfo. "
                f"Agent {umf.agent_id} in session {umf.session_id} has None. "
                f"Set based on source_type: user_input=1.0, database=0.9, etc."
            )

        # Compute full Ψ with deterministic Decimal arithmetic
        recency_d = Decimal(str(recency))
        verified_d = Decimal(str(verified))
        trust_d = Decimal(str(trust_score))
        authority_d = Decimal(str(authority))

        psi_d = (w_r * recency_d + w_c * verified_d + w_t * trust_d + w_p * authority_d)
        psi = float(psi_d)

        return psi

    def calculate_psi_breakdown(
        self,
        umf: StampedUMF,
        trust_score: float,
        reference_time: Optional[datetime] = None,
    ) -> dict:
        """Return per-component Ψ scores for inspector explainability.

        Uses deterministic Decimal arithmetic for consistency.

        Returns a dict with keys R, C, T, A (each in 0-1), weights,
        and the final total Ψ score.  Silently returns zeros if
        verified_confidence or authority_score are unset (should not
        happen in production but protects Inspector from crashing).
        """
        if reference_time is None:
            reference_time = datetime.utcnow()

        # Convert to Decimal for deterministic arithmetic
        w_r = Decimal(str(self.w_r))
        w_c = Decimal(str(self.w_c))
        w_t = Decimal(str(self.w_t))
        w_p = Decimal(str(self.w_p))
        lambda_d = Decimal(str(self.lambda_))

        delta_t = max(0.0, (reference_time - umf.timestamp).total_seconds())
        recency = float(Decimal(str(math.exp(-float(lambda_d) * delta_t))))
        verified = umf.provenance_info.verified_confidence or 0.0
        authority = umf.provenance_info.authority_score or 0.0

        # Compute with Decimal arithmetic
        recency_d = Decimal(str(recency))
        verified_d = Decimal(str(verified))
        trust_d = Decimal(str(trust_score))
        authority_d = Decimal(str(authority))

        psi_d = (w_r * recency_d + w_c * verified_d + w_t * trust_d + w_p * authority_d)
        psi = float(psi_d)

        return {
            "R": round(recency, 4),
            "C": round(verified, 4),
            "T": round(trust_score, 4),
            "A": round(authority, 4),
            "w_r": float(w_r),
            "w_c": float(w_c),
            "w_t": float(w_t),
            "w_p": float(w_p),
            "total_psi": round(psi, 4),
        }


    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def resolve_conflict(
        self,
        existing: StampedUMF,
        incoming: StampedUMF,
        trust_table: Dict[str, float],
        reference_time: Optional[datetime] = None,
        domain: Optional[str] = None,
        trust_manager=None,   # Optional[TrustManager] – avoids circular import
    ) -> ConflictResult:
        """
        Resolve conflict between existing and incoming UMF packets.

        Trust lookup priority:
          1. TrustManager (dynamic, domain-aware) if provided
          2. trust_table fallback
          3. Neutral 0.5

        If |Ψ_incoming - Ψ_existing| < uncertainty_threshold the conflict
        is marked *unresolved* and both memories are preserved; neither is
        designated a loser.  The caller decides how to handle this state.

        Args:
            existing: Currently committed UMF packet.
            incoming: New incoming UMF packet.
            trust_table: Static {agent_id: trust_score} fallback.
            reference_time: Reference time for Ψ calculation.
            domain: Task domain for domain-specific trust.
            trust_manager: Optional TrustManager instance.

        Returns:
            ConflictResult with winner, loser, scores, and unresolved flag.
        """
        # Resolve trust scores
        def _trust(agent_id: str) -> float:
            if trust_manager is not None:
                q_domain = domain or (
                    existing.provenance_info.domain
                    if existing.provenance_info.domain
                    else "_global"
                )
                return trust_manager.get_trust(agent_id, q_domain)
            return trust_table.get(agent_id, 0.5)

        trust_existing = _trust(existing.agent_id)
        trust_incoming = _trust(incoming.agent_id)

        psi_existing = self.calculate_psi(existing, trust_existing, reference_time)
        psi_incoming = self.calculate_psi(incoming, trust_incoming, reference_time)

        # Calculate breakdowns for explainability
        breakdown_existing = self.calculate_psi_breakdown(existing, trust_existing, reference_time)
        breakdown_incoming = self.calculate_psi_breakdown(incoming, trust_incoming, reference_time)

        diff = abs(psi_incoming - psi_existing)

        def _build_audit_description(
            winner_umf: StampedUMF,
            loser_umf: StampedUMF,
            psi_win: float,
            psi_loss: float,
            unresolved: bool,
        ) -> str:
            w_agent = winner_umf.agent_id
            l_agent = loser_umf.agent_id

            w_auth_type = winner_umf.provenance_info.source_type or "agent_claim"
            l_auth_type = loser_umf.provenance_info.source_type or "agent_claim"

            w_auth = winner_umf.provenance_info.authority_score or 0.3
            l_auth = loser_umf.provenance_info.authority_score or 0.3

            w_conf = winner_umf.confidence_score
            l_conf = loser_umf.confidence_score

            if unresolved:
                return (
                    f"Unresolved Conflict between {w_agent} and {l_agent}: "
                    f"score delta ({abs(psi_win - psi_loss):.4f}) is within uncertainty threshold. "
                    f"Both memories preserved."
                )

            conf_diff_pct = int(round((l_conf - w_conf) * 100))
            conf_clause = f" despite your {conf_diff_pct}% higher confidence score" if conf_diff_pct > 0 else ""

            return (
                f"Rejecting {l_agent}: Your '{l_auth_type}' (Auth {l_auth:.2f}, Ψ {psi_loss:.3f}) is mathematically "
                f"insufficient to override {w_agent}'s '{w_auth_type}' record (Auth {w_auth:.2f}, Ψ {psi_win:.3f}){conf_clause}."
            )

        # Uncertainty zone: scores too close to distinguish
        if diff < self.uncertainty_threshold:
            desc = _build_audit_description(existing, incoming, psi_existing, psi_incoming, True)
            return ConflictResult(
                winner=existing,   # Incumbent preserved; no overwrite
                loser=incoming,
                psi_winner=psi_existing,
                psi_loser=psi_incoming,
                reason=(
                    f"Scores too close to resolve "
                    f"(|Ψ_in={psi_incoming:.4f} - Ψ_ex={psi_existing:.4f}|={diff:.4f} "
                    f"< threshold={self.uncertainty_threshold}). "
                    "Conflict marked unresolved; awaiting additional evidence."
                ),
                unresolved=True,
                psi_winner_breakdown=breakdown_existing,
                psi_loser_breakdown=breakdown_incoming,
                description=desc,
            )

        if psi_incoming > psi_existing:
            desc = _build_audit_description(incoming, existing, psi_incoming, psi_existing, False)
            return ConflictResult(
                winner=incoming,
                loser=existing,
                psi_winner=psi_incoming,
                psi_loser=psi_existing,
                reason=f"Incoming Ψ={psi_incoming:.4f} > Existing Ψ={psi_existing:.4f}",
                psi_winner_breakdown=breakdown_incoming,
                psi_loser_breakdown=breakdown_existing,
                description=desc,
            )
        else:
            desc = _build_audit_description(existing, incoming, psi_existing, psi_incoming, False)
            return ConflictResult(
                winner=existing,
                loser=incoming,
                psi_winner=psi_existing,
                psi_loser=psi_incoming,
                reason=f"Existing Ψ={psi_existing:.4f} >= Incoming Ψ={psi_incoming:.4f}",
                psi_winner_breakdown=breakdown_existing,
                psi_loser_breakdown=breakdown_incoming,
                description=desc,
            )


# ------------------------------------------------------------------
# Module-level singleton (backward-compatible)
# ------------------------------------------------------------------

_engine = ConflictResolutionEngine()


def get_resolution_engine() -> ConflictResolutionEngine:
    """Get the global conflict resolution engine."""
    return _engine