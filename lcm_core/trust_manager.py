"""
Trust Manager - dynamically updated, domain-specific agent trust scores.

Trust represents historical reliability of an agent, not the reliability
of a single memory. It is never manually assigned; it is observed over time.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any


_DEFAULT_PRIOR = 0.5  # Neutral cold-start trust for new agents
_DEFAULT_HALF_LIFE_SECONDS = 30 * 86400.0  # 30-day half-life for temporal trust decay
_DEFAULT_IMMEDIATE_READ_GRACE_SECONDS = 1.0


@dataclass
class AgentDomainRecord:
    """
    Tracks verified claim outcomes for one agent in one domain.

    Formula:  trust = verified_correct / total_claims
    During cold-start (total_claims == 0): trust = prior
    With Temporal Decay: T_now = 0.5 + (T_last - 0.5) * e^(-gamma * dt)
    """
    total_claims: int = 0
    verified_correct: int = 0
    verified_wrong: int = 0
    last_outcome_time: Optional[datetime] = None

    @property
    def raw_trust_score(self) -> float:
        if self.total_claims == 0:
            return _DEFAULT_PRIOR
        return self.verified_correct / self.total_claims

    # ------------------------------------------------------------------
    # Uncertainty-aware trust (Phase 3)
    # ------------------------------------------------------------------

    def get_wilson_interval(
        self,
        z: float = 1.96,
    ) -> tuple[float, float]:
        """
        Wilson score interval for the observed success rate.

        This is the "confidence-aware" counterpart to the naive point estimate
        ``correct/total``: with few observations the interval is wide (high
        uncertainty) and with many it converges to the point estimate.

        Args:
            z: z-score for the desired confidence level (1.96 → 95%).

        Returns:
            (low, high) bounds in [0, 1]. With zero observations the whole
            unit interval is returned (maximal uncertainty).
        """
        n = self.total_claims
        if n == 0:
            return (0.0, 1.0)
        k = self.verified_correct
        p = k / n
        z2 = z * z
        denom_scale = 1.0 / (1.0 + z2 / n)
        center = (p + z2 / (2.0 * n)) * denom_scale
        margin = (
            z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
        ) * denom_scale
        return (max(0.0, center - margin), min(1.0, center + margin))

    def get_uncertainty(
        self,
        z: float = 1.96,
    ) -> float:
        """Half-width of the Wilson interval — a measure of how uncertain the
        point estimate is. 0.0 means fully certain; 0.5 is maximal uncertainty."""
        low, high = self.get_wilson_interval(z=z)
        return (high - low) / 2.0

    def get_conservative_trust(
        self,
        z: float = 1.96,
    ) -> float:
        """
        Worst-case, uncertainty-penalized trust score.

        This is the LOWER bound of the Wilson interval: an agent with a single
        correct verification (naive trust 1.0) is heavily penalized because the
        sample is tiny, while an agent with many consistent verifications gets a
        conservative score very close to its point estimate.
        """
        low, _ = self.get_wilson_interval(z=z)
        return low

    def get_uncertainty_profile(
        self,
        z: float = 1.96,
    ) -> Dict[str, Any]:
        """Bundle the uncertainty-aware trust statistics for one record."""
        n = self.total_claims
        k = self.verified_correct
        low, high = self.get_wilson_interval(z=z)
        return {
            "naive_trust": k / n if n else _DEFAULT_PRIOR,
            "outcome_count": n,
            "correct_count": k,
            "incorrect_count": n - k,
            "uncertainty": (high - low) / 2.0,
            "interval_low": low,
            "interval_high": high,
            "conservative_trust": low,
            "z_score": z,
        }

    def get_decayed_trust(
        self,
        current_time: Optional[datetime] = None,
        half_life_seconds: float = _DEFAULT_HALF_LIFE_SECONDS,
        immediate_read_grace_seconds: float = _DEFAULT_IMMEDIATE_READ_GRACE_SECONDS,
    ) -> float:
        """
        Calculates temporal trust decay based on elapsed time since last outcome.

        Formula: T_now = 0.5 + (T_last - 0.5) * e^(-gamma * dt)
        """
        if self.total_claims == 0:
            return _DEFAULT_PRIOR

        raw_trust = self.raw_trust_score
        if self.last_outcome_time is None:
            return raw_trust

        explicit_time = current_time is not None
        ref_time = current_time or datetime.utcnow()
        delta_t = max(0.0, (ref_time - self.last_outcome_time).total_seconds())

        if half_life_seconds <= 0:
            return raw_trust

        # A read performed as part of the same logical operation must expose the
        # outcome just recorded, not a platform/scheduling-dependent micro-decay.
        # Second-and-larger elapsed times still follow the documented decay; a
        # sub-second grace is negligible against the default 30-day half-life.
        if not explicit_time and delta_t < immediate_read_grace_seconds:
            return raw_trust

        gamma = math.log(2.0) / half_life_seconds
        decayed = 0.5 + (raw_trust - 0.5) * math.exp(-gamma * delta_t)
        return decayed


@dataclass
class AgentTrustRecord:
    """
    All domain-specific trust records for a single agent.

    Domains are arbitrary strings, e.g. "healthcare", "coding", "finance".
    A special key "_global" is maintained as the cross-domain fallback.
    """
    agent_id: str
    domains: Dict[str, AgentDomainRecord] = field(default_factory=dict)

    def _get_or_create(self, domain: str) -> AgentDomainRecord:
        if domain not in self.domains:
            self.domains[domain] = AgentDomainRecord()
        return self.domains[domain]

    def record_outcome(
        self,
        correct: bool,
        domain: str = "_global",
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Update the given domain and the global aggregation (unless domain IS _global)."""
        ts = timestamp or datetime.utcnow()
        domains_to_update = (domain,) if domain == "_global" else (domain, "_global")
        for d in domains_to_update:
            rec = self._get_or_create(d)
            rec.total_claims += 1
            rec.last_outcome_time = ts
            if correct:
                rec.verified_correct += 1
            else:
                rec.verified_wrong += 1

    def get_trust(
        self,
        domain: str = "_global",
        current_time: Optional[datetime] = None,
        half_life_seconds: float = _DEFAULT_HALF_LIFE_SECONDS,
        immediate_read_grace_seconds: float = _DEFAULT_IMMEDIATE_READ_GRACE_SECONDS,
    ) -> float:
        """
        Return trust score for a domain with temporal decay applied.

        Falls back to _global if the domain has no history yet.
        Returns the cold-start prior if neither exists.
        """
        if domain in self.domains and self.domains[domain].total_claims > 0:
            return self.domains[domain].get_decayed_trust(
                current_time, half_life_seconds, immediate_read_grace_seconds)
        if "_global" in self.domains and self.domains["_global"].total_claims > 0:
            return self.domains["_global"].get_decayed_trust(
                current_time, half_life_seconds, immediate_read_grace_seconds)
        return _DEFAULT_PRIOR

    def summary(self) -> Dict[str, object]:
        """Human-readable summary of this agent's trust profile."""
        return {
            "agent_id": self.agent_id,
            "domains": {
                d: {
                    "total_claims": r.total_claims,
                    "verified_correct": r.verified_correct,
                    "verified_wrong": r.verified_wrong,
                    "trust_score": r.raw_trust_score,
                    "last_outcome_time": r.last_outcome_time.isoformat() if r.last_outcome_time else None,
                }
                for d, r in self.domains.items()
            },
        }


class TrustManager:
    """
    Registry of per-agent, per-domain trust scores with Temporal Trust Decay.

    Usage:
        manager = TrustManager()

        # Record outcomes as verifications arrive
        manager.record_outcome("medical_agent", correct=True, domain="healthcare")

        # Query trust (decays toward neutral 0.5 as time elapses without new activity)
        trust = manager.get_trust("medical_agent", domain="healthcare")
    """

    def __init__(
        self,
        cold_start_prior: float = _DEFAULT_PRIOR,
        half_life_days: float = 30.0,
        immediate_read_grace_seconds: float = _DEFAULT_IMMEDIATE_READ_GRACE_SECONDS,
    ):
        self._prior = cold_start_prior
        self._half_life_seconds = half_life_days * 86400.0
        if immediate_read_grace_seconds < 0:
            raise ValueError("immediate_read_grace_seconds must be non-negative")
        self._immediate_read_grace_seconds = immediate_read_grace_seconds
        self._records: Dict[str, AgentTrustRecord] = {}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get_trust(
        self,
        agent_id: str,
        domain: str = "_global",
        current_time: Optional[datetime] = None,
        *,
        as_of: Optional[datetime] = None,
    ) -> float:
        """
        Return trust score for agent in the given domain with temporal decay.
        """
        if current_time is not None and as_of is not None:
            raise ValueError("supply only one of current_time or as_of")
        effective_time = as_of if as_of is not None else current_time
        if agent_id not in self._records:
            return self._prior
        return self._records[agent_id].get_trust(
            domain,
            current_time=effective_time,
            half_life_seconds=self._half_life_seconds,
            immediate_read_grace_seconds=self._immediate_read_grace_seconds,
        )

    def record_outcome(
        self,
        agent_id: str,
        correct: bool,
        domain: str = "_global",
        timestamp: Optional[datetime] = None,
        *,
        observed_at: Optional[datetime] = None,
    ) -> None:
        """
        Update trust for agent after a claim has been externally verified.
        """
        if timestamp is not None and observed_at is not None:
            raise ValueError("supply only one of timestamp or observed_at")
        effective_time = observed_at if observed_at is not None else timestamp
        if agent_id not in self._records:
            self._records[agent_id] = AgentTrustRecord(agent_id=agent_id)
        self._records[agent_id].record_outcome(
            correct=correct, domain=domain, timestamp=effective_time)

    def get_outcome_counts(self, agent_id: str, domain: str = "_global") -> Dict[str, int]:
        """
        Returns outcome statistics for an agent in a domain.

        Returns:
            {"total": int, "correct": int, "incorrect": int}
        """
        if agent_id not in self._records:
            return {"total": 0, "correct": 0, "incorrect": 0}
        record = self._records[agent_id]
        domain_rec = record.domains.get(domain) or record.domains.get("_global")
        if not domain_rec:
            return {"total": 0, "correct": 0, "incorrect": 0}
        return {
            "total": domain_rec.total_claims,
            "correct": domain_rec.verified_correct,
            "incorrect": domain_rec.verified_wrong,
        }

    def get_trust_with_meta(
        self,
        agent_id: str,
        domain: str = "_global",
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Returns full trust score and metadata.

        Returns:
            {
              "trust_score": float,
              "outcome_count": int,
              "correct_count": int,
              "incorrect_count": int,
              "domain": str,
            }
        """
        counts = self.get_outcome_counts(agent_id, domain=domain)
        trust_score = self.get_trust(agent_id, domain=domain, current_time=current_time)
        return {
            "trust_score": trust_score,
            "outcome_count": counts["total"],
            "correct_count": counts["correct"],
            "incorrect_count": counts["incorrect"],
            "domain": domain,
        }

    # ------------------------------------------------------------------
    # Uncertainty-aware trust API (Phase 3)
    # ------------------------------------------------------------------

    def _domain_record(self, agent_id: str, domain: str = "_global") -> AgentDomainRecord:
        """Resolve the record for an agent+domain with _global fallback (or a
        fresh cold-start record if none exists)."""
        if agent_id not in self._records:
            return AgentDomainRecord()
        rec = self._records[agent_id].domains.get(domain)
        if rec is not None:
            return rec
        rec = self._records[agent_id].domains.get("_global")
        if rec is not None:
            return rec
        return AgentDomainRecord()

    def get_uncertainty(
        self,
        agent_id: str,
        domain: str = "_global",
        z: float = 1.96,
    ) -> float:
        """
        Half-width of the Wilson confidence interval for the agent's trust in a
        domain. 0.0 → fully certain; 0.5 → maximal uncertainty (no data).
        """
        return self._domain_record(agent_id, domain).get_uncertainty(z=z)

    def get_trust_interval(
        self,
        agent_id: str,
        domain: str = "_global",
        z: float = 1.96,
    ) -> Dict[str, float]:
        """(low, high) Wilson interval bounds for the agent's trust in a domain."""
        rec = self._domain_record(agent_id, domain)
        low, high = rec.get_wilson_interval(z=z)
        return {"low": low, "high": high}

    def get_conservative_trust(
        self,
        agent_id: str,
        domain: str = "_global",
        z: float = 1.96,
    ) -> float:
        """
        Worst-case, uncertainty-penalized trust (lower Wilson bound).

        Unlike the naive point estimate this cannot be inflated by a handful of
        lucky outcomes: a single correct verification still yields a conservative
        score near 0, and it only approaches the point estimate as more
        independent outcomes are observed.
        """
        return self._domain_record(agent_id, domain).get_conservative_trust(z=z)

    def get_trust_with_uncertainty(
        self,
        agent_id: str,
        domain: str = "_global",
        current_time: Optional[datetime] = None,
        z: float = 1.96,
    ) -> Dict[str, Any]:
        """
        Full uncertainty-aware trust profile for one agent+domain.

        Returns:
            {
              "trust_score": float,            # decayed point estimate (pipeline default)
              "naive_trust": float,            # raw correct/total (baseline, no decay)
              "outcome_count": int,
              "correct_count": int,
              "incorrect_count": int,
              "uncertainty": float,            # interval half-width
              "interval_low": float,
              "interval_high": float,
              "conservative_trust": float,     # worst-case, uncertainty-penalized
              "domain": str,
            }
        """
        counts = self.get_outcome_counts(agent_id, domain=domain)
        rec = self._domain_record(agent_id, domain)
        profile = rec.get_uncertainty_profile(z=z)
        return {
            "trust_score": self.get_trust(agent_id, domain=domain, current_time=current_time),
            "naive_trust": profile["naive_trust"],
            "outcome_count": profile["outcome_count"],
            "correct_count": profile["correct_count"],
            "incorrect_count": profile["incorrect_count"],
            "uncertainty": profile["uncertainty"],
            "interval_low": profile["interval_low"],
            "interval_high": profile["interval_high"],
            "conservative_trust": profile["conservative_trust"],
            "domain": domain,
        }

    def compare_trust_models(
        self,
        agent_id: str,
        domain: str = "_global",
        z: float = 1.96,
    ) -> Dict[str, Any]:
        """
        Confidence-aware trust vs the naive correct/total baseline.

        The gap between the naive point estimate and the conservative
        (uncertainty-penalized) score quantifies how much a small sample can
        overstate reliability — inputs for the Phase 12 comparative metrics.
        """
        rec = self._domain_record(agent_id, domain)
        n = rec.total_claims
        k = rec.verified_correct
        naive = k / n if n else self._prior
        conservative = rec.get_conservative_trust(z=z)
        return {
            "naive": naive,
            "conservative": conservative,
            "difference": naive - conservative,
            "outcome_count": n,
            "domain": domain,
        }

    def get_outcome_summary(
        self,
        agent_id: str,
        domain: str = "_global",
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Public outcome summary method."""
        counts = self.get_outcome_counts(agent_id, domain=domain)
        trust_score = self.get_trust(agent_id, domain=domain, current_time=current_time)
        return {
            "total": counts["total"],
            "correct": counts["correct"],
            "trust": trust_score,
        }

    def build_trust_table(
        self,
        agent_ids: list[str],
        domain: str = "_global",
        current_time: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """
        Build a {agent_id: trust_score} dict for use in ConflictResolutionEngine.
        """
        return {aid: self.get_trust(aid, domain, current_time=current_time) for aid in agent_ids}

    def get_agent_summary(self, agent_id: str) -> Optional[Dict[str, object]]:
        """Return trust summary for one agent, or None if unknown."""
        if agent_id not in self._records:
            return None
        return self._records[agent_id].summary()

    def all_agents(self) -> list[str]:
        """Return list of all tracked agent IDs."""
        return list(self._records.keys())
