"""Algebraic conflict resolution engine V1 - Ψ = (R + C + T) / 3."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from decimal import Decimal, getcontext
from .numeric import classify_scores as canonical_classify_scores, quantize_score, serialize_score, weighted_total

from .schema import StampedUMF

# Set high precision for deterministic arithmetic
getcontext().prec = 28


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

@dataclass
class ResolutionConfig:
    """
    Configurable weights and thresholds for the V1 Ψ formula.

    Ψ = (R + C + T) / 3

    All weights must sum to 1.0. The uncertainty_threshold controls
    when a conflict is treated as too close to call.
    
    Note on uncertainty_threshold:
    - Production default: 0.05 (enables unresolved for close scores)
    - Cold-start tests: 0.0 (deterministic tie-breaking for regression)
    - Labeled conflicts: 0.0 by default (can be overridden for unresolved tests)
    """
    w_recency: float = 1/3
    w_confidence: float = 1/3
    w_trust: float = 1/3
    decay_lambda: Optional[float] = None  # None → 24 h half-life
    uncertainty_threshold: float = 0.05  # |ΨA - ΨB| < this → unresolved

    def validate(self) -> None:
        total = self.w_recency + self.w_confidence + self.w_trust
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
    unresolved: bool = False
    psi_winner_breakdown: dict = None
    psi_loser_breakdown: dict = None
    description: str = ""
    operation_time: Optional[datetime] = None
    psi_margin_exact: Optional[str] = None
    threshold_exact: Optional[str] = None


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------

class ConflictResolutionEngine:
    """
    V1: Implements the Ψ formula for deterministic conflict resolution.

    Ψ = (R + C + T) / 3

    Where:
      w_r, w_c, w_t : fixed weights (1/3 each)
      Recency       : e^(-λΔt) - exponential decay with 24h half-life
      Confidence    : externally verified confidence from ProvenanceInfo
      Trust         : observed agent reliability from TrustManager

    Provenance is a mandatory Stage-1 audit layer. It is NOT a scalar
    component of Ψ. authority_score must be present for fail-closed
    compliance but does not contribute to the resolution score.
    """

    _PHASE_A_POLICIES = frozenset({
        "full_crt",
        "last_write_wins",
        "recency_only",
        "majority_unique_agent",
        "majority_independent_source",
        "fixed_neutral_trust",
        "full_minus_recency",
        "full_minus_confidence",
        "full_minus_trust",
    })

    def __init__(
        self,
        w_recency: float = 1/3,
        w_confidence: float = 1/3,
        w_trust: float = 1/3,
        decay_lambda: Optional[float] = None,
        uncertainty_threshold: float = 0.05,
        config: Optional[ResolutionConfig] = None,
        resolution_policy: str = "full_crt",
    ):
        if resolution_policy not in self._PHASE_A_POLICIES:
            raise ValueError(f"unsupported resolution policy: {resolution_policy}")
        self.resolution_policy = resolution_policy
        
        if config is not None:
            config.validate()
            self.w_r = config.w_recency
            self.w_c = config.w_confidence
            self.w_t = config.w_trust
            self.uncertainty_threshold = config.uncertainty_threshold
            lam = config.decay_lambda
        else:
            self.w_r = w_recency
            self.w_c = w_confidence
            self.w_t = w_trust
            self.uncertainty_threshold = uncertainty_threshold
            lam = decay_lambda

        # λ such that e^(-λ × 86400) ≈ 0.5  (24-h half-life)
        self.lambda_ = lam if lam is not None else (-math.log(0.5) / 86400.0)

    @staticmethod
    def classify_scores(
        existing_score: float,
        incoming_score: float,
        uncertainty_threshold: float,
    ) -> str:
        """Apply the canonical scalar conflict-decision boundary."""
        return canonical_classify_scores(existing_score, incoming_score, uncertainty_threshold)

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

        Ψ = (R + C + T) / 3

        Provenance is Stage-1 metadata only. authority_score must be present
        for fail-closed compliance but does NOT contribute to Ψ.

        Args:
            umf: The stamped UMF packet.
            trust_score: Historical reliability score for the agent (0-1).
            reference_time: Time to measure recency from (defaults to now).

        Returns:
            Ψ score (0-1 range, ≈1 for fresh + confident + trusted).
        
        Raises:
            ValueError: If verified_confidence is None (must be pre-computed).
            ValueError: If authority_score is None (Stage-1 provenance contract).
        """
        if reference_time is None:
            reference_time = datetime.utcnow()

        # Convert to Decimal for deterministic arithmetic
        w_r = Decimal(str(self.w_r))
        w_c = Decimal(str(self.w_c))
        w_t = Decimal(str(self.w_t))
        lambda_d = Decimal(str(self.lambda_))

        delta_t = max(0, (reference_time - umf.timestamp).total_seconds())
        recency = float(Decimal(str(math.exp(-float(lambda_d) * delta_t))))

        verified_value = umf.provenance_info.verified_confidence
        if verified_value is None:
            raise ValueError(
                f"verified_confidence must be pre-computed in ProvenanceInfo. "
                f"Agent {umf.agent_id} in session {umf.session_id} has None. "
                f"Use ConfidenceEngine to compute it before ingestion."
            )

        # Stage-1 provenance fail-closed check: authority_score must be present
        # even though it does not contribute to Ψ in V1.
        authority_value = umf.provenance_info.authority_score
        if authority_value is None:
            raise ValueError(
                f"authority_score must be pre-computed in ProvenanceInfo. "
                f"Agent {umf.agent_id} in session {umf.session_id} has None. "
                f"Set based on source_type: user_input=1.0, database=0.9, etc."
            )

        psi_d = weighted_total(
            {"recency": recency, "confidence": verified_value, "trust": trust_score},
            {"recency": w_r, "confidence": w_c, "trust": w_t},
        )
        return float(psi_d)

    def calculate_psi_breakdown(
        self,
        umf: StampedUMF,
        trust_score: float,
        reference_time: Optional[datetime] = None,
    ) -> dict:
        """Return per-component Ψ scores for inspector explainability.

        Returns a dict with keys R, C, T (each in 0-1), weights,
        and the final total Ψ score.  Silently returns zeros if
        verified_confidence is unset (should not happen in production
        but protects Inspector from crashing).
        """
        if reference_time is None:
            reference_time = datetime.utcnow()

        w_r = Decimal(str(self.w_r))
        w_c = Decimal(str(self.w_c))
        w_t = Decimal(str(self.w_t))
        lambda_d = Decimal(str(self.lambda_))

        delta_t = max(0.0, (reference_time - umf.timestamp).total_seconds())
        recency_value = float(Decimal(str(math.exp(-float(lambda_d) * delta_t))))

        verified_value = umf.provenance_info.verified_confidence or 0.0

        psi_d = weighted_total(
            {"recency": recency_value, "confidence": verified_value, "trust": trust_score},
            {"recency": w_r, "confidence": w_c, "trust": w_t},
        )
        psi = float(psi_d)

        return {
            "R": round(recency_value, 4),
            "C": round(verified_value, 4),
            "T": round(trust_score, 4),
            "w_r": float(w_r),
            "w_c": float(w_c),
            "w_t": float(w_t),
            "total_psi": round(psi, 4),
            "R_exact": serialize_score(recency_value),
            "C_exact": serialize_score(verified_value),
            "T_exact": serialize_score(trust_score),
            "total_psi_exact": serialize_score(psi_d),
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
        trust_manager=None,
        strict_domain: bool = False,
        votes: Optional[List[Dict[str, Any]]] = None,
    ) -> ConflictResult:
        """
        Resolve conflict between existing and incoming UMF packets.

        Trust lookup priority:
          1. TrustManager (dynamic, domain-aware) if provided
          2. trust_table fallback
          3. Neutral 0.5

        If |Ψ_incoming - Ψ_existing| < uncertainty_threshold the conflict
        is marked *unresolved* and both memories are preserved; neither is
        designated a loser.
        """
        if reference_time is None:
            reference_time = datetime.utcnow()

        def _trust(agent_id: str) -> float:
            if trust_manager is not None:
                q_domain = domain or (
                    existing.provenance_info.domain
                    if existing.provenance_info.domain
                    else "_global"
                )
                return trust_manager.get_trust(
                    agent_id, q_domain, as_of=reference_time, strict_domain=strict_domain
                )
            return trust_table.get(agent_id, 0.5)

        trust_existing = _trust(existing.agent_id)
        trust_incoming = _trust(incoming.agent_id)

        psi_existing = self.calculate_psi(existing, trust_existing, reference_time)
        psi_incoming = self.calculate_psi(incoming, trust_incoming, reference_time)

        breakdown_existing = self.calculate_psi_breakdown(existing, trust_existing, reference_time)
        breakdown_incoming = self.calculate_psi_breakdown(incoming, trust_incoming, reference_time)

        diff = float(quantize_score(abs(quantize_score(psi_incoming) - quantize_score(psi_existing))))

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
                f"Rejecting {l_agent}: Your '{l_auth_type}' record (Ψ {psi_loss:.3f}) is mathematically "
                f"insufficient to override {w_agent}'s '{w_auth_type}' record (Ψ {psi_win:.3f}){conf_clause}."
            )

        if self.resolution_policy in {"majority_unique_agent", "majority_independent_source"}:
            return self._resolve_majority(
                existing=existing,
                incoming=incoming,
                majority_policy=self.resolution_policy,
                reference_time=reference_time,
            )
        decision = (
            "incoming" if self.resolution_policy == "last_write_wins"
            else self.classify_scores(psi_existing, psi_incoming, self.uncertainty_threshold)
        )

        if decision == "unresolved":
            desc = _build_audit_description(existing, incoming, psi_existing, psi_incoming, True)
            return ConflictResult(
                winner=existing,
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
                operation_time=reference_time,
                psi_margin_exact=serialize_score(diff),
                threshold_exact=serialize_score(self.uncertainty_threshold),
            )

        if decision == "incoming":
            desc = _build_audit_description(incoming, existing, psi_incoming, psi_existing, False)
            reason = (
                "Incoming admissible write selected by server-authoritative last-write-wins ordering"
                if self.resolution_policy == "last_write_wins"
                else f"Incoming Ψ={psi_incoming:.4f} > Existing Ψ={psi_existing:.4f}"
            )
            return ConflictResult(
                winner=incoming,
                loser=existing,
                psi_winner=psi_incoming,
                psi_loser=psi_existing,
                reason=reason,
                psi_winner_breakdown=breakdown_incoming,
                psi_loser_breakdown=breakdown_existing,
                description=desc,
                operation_time=reference_time,
                psi_margin_exact=serialize_score(diff),
                threshold_exact=serialize_score(self.uncertainty_threshold),
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
                operation_time=reference_time,
                psi_margin_exact=serialize_score(diff),
                threshold_exact=serialize_score(self.uncertainty_threshold),
            )

    # ------------------------------------------------------------------
    # Majority policies
    # ------------------------------------------------------------------

    def _resolve_majority(
        self,
        existing: StampedUMF,
        incoming: StampedUMF,
        majority_policy: str,
        reference_time: Optional[datetime] = None,
    ) -> ConflictResult:
        """Majority-by-unique-agent / majority-by-independent-source."""
        desc = (
            f"Majority policy '{majority_policy}' cannot break a 1-1 tie "
            f"between {existing.agent_id} and {incoming.agent_id} without "
            f"additional corroborating votes. Incumbent preserved."
        )
        return ConflictResult(
            winner=existing,
            loser=incoming,
            psi_winner=0.0,
            psi_loser=0.0,
            reason="unresolved_majority_tie",
            unresolved=True,
            psi_winner_breakdown={},
            psi_loser_breakdown={},
            description=desc,
            operation_time=reference_time or datetime.utcnow(),
        )
