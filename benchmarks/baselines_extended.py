"""
Extended baselines for comparison against the LCM pipeline.

Each baseline exposes a single coroutine:
    resolve(existing, incoming, **kwargs) -> str   (agent_id of "winner")

Baselines:
    LastWriteWins      - always take the incoming packet (no resolution)
    RecencyOnly        - pick whichever packet has the more recent timestamp
    MajorityVoting     - accumulate votes; the value with the most votes wins
    FixedTrust         - Psi formula but with a static trust table (all agents 0.5)
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from lcm_core.schema import StampedUMF
from lcm_core.conflict import ConflictResolutionEngine


# ---------------------------------------------------------------------------
# Last-write-wins
# ---------------------------------------------------------------------------

class LastWriteWins:
    """Incoming packet always replaces existing, regardless of any signal."""

    name = "last_write_wins"

    def resolve(self, existing: StampedUMF, incoming: StampedUMF) -> StampedUMF:
        return incoming

    def is_correct(self, winner: StampedUMF, ground_truth_agent: str) -> bool:
        return winner.agent_id == ground_truth_agent


# ---------------------------------------------------------------------------
# Recency-only
# ---------------------------------------------------------------------------

class RecencyOnly:
    """Pick whichever packet has the more recent timestamp.  No trust, no confidence."""

    name = "recency_only"

    def resolve(self, existing: StampedUMF, incoming: StampedUMF) -> StampedUMF:
        if incoming.timestamp >= existing.timestamp:
            return incoming
        return existing

    def is_correct(self, winner: StampedUMF, ground_truth_agent: str) -> bool:
        return winner.agent_id == ground_truth_agent


# ---------------------------------------------------------------------------
# Majority voting
# ---------------------------------------------------------------------------

class MajorityVoting:
    """
    Accumulate all claims for a path; the value asserted by the most agents wins.
    Ties are broken by recency.

    Because voting requires seeing all submissions before deciding,
    this baseline operates on a batch of candidates rather than
    a pair-wise comparison.
    """

    name = "majority_voting"

    def resolve_batch(self, candidates: List[StampedUMF]) -> StampedUMF:
        """
        Pick the winner from a list of candidates by vote count.

        Args:
            candidates: All packets submitted for the same path.

        Returns:
            The StampedUMF whose payload value had the most votes.
        """
        if not candidates:
            raise ValueError("candidates list is empty")
        if len(candidates) == 1:
            return candidates[0]

        # Count votes per serialised value
        vote_counts: Dict[str, int] = defaultdict(int)
        value_to_latest: Dict[str, StampedUMF] = {}

        for c in candidates:
            # Use string representation of the whole payload as the vote key
            key = str(sorted(c.assertion_payload.items()))
            vote_counts[key] += 1
            # Keep the most recent packet for this value (tie-break)
            if key not in value_to_latest or c.timestamp > value_to_latest[key].timestamp:
                value_to_latest[key] = c

        best_key = max(vote_counts, key=lambda k: (vote_counts[k], value_to_latest[k].timestamp))
        return value_to_latest[best_key]

    def resolve(self, existing: StampedUMF, incoming: StampedUMF) -> StampedUMF:
        """Pair-wise shim for benchmarks that work with two packets at a time."""
        return self.resolve_batch([existing, incoming])

    def is_correct(self, winner: StampedUMF, ground_truth_agent: str) -> bool:
        return winner.agent_id == ground_truth_agent


# ---------------------------------------------------------------------------
# Fixed-trust Psi
# ---------------------------------------------------------------------------

class FixedTrust:
    """
    Full Psi formula but every agent gets the same static trust score.
    Demonstrates what happens when trust differentiation is removed.
    """

    name = "fixed_trust"

    def __init__(self, trust_value: float = 0.5):
        self._trust = trust_value
        self._engine = ConflictResolutionEngine(
            uncertainty_threshold=0.05,
        )

    def resolve(self, existing: StampedUMF, incoming: StampedUMF,
                reference_time: Optional[datetime] = None) -> StampedUMF:
        trust_table = {
            existing.agent_id: self._trust,
            incoming.agent_id: self._trust,
        }
        result = self._engine.resolve_conflict(
            existing, incoming, trust_table, reference_time=reference_time
        )
        return result.winner

    def is_correct(self, winner: StampedUMF, ground_truth_agent: str) -> bool:
        return winner.agent_id == ground_truth_agent


# ---------------------------------------------------------------------------
# Convenience registry
# ---------------------------------------------------------------------------

ALL_BASELINES = [
    LastWriteWins(),
    RecencyOnly(),
    MajorityVoting(),
    FixedTrust(trust_value=0.5),
]
