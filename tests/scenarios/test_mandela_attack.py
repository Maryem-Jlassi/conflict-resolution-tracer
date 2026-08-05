"""
Scenario tests — Mandela injection attack resistance

These tests encode the paper's core security claim:
    No number of low-trust repetitions can override a single high-trust baseline.

Each test is a different attack configuration; all must result in zero overrides.
They are kept as explicit named scenarios rather than parametrized so that
a reader can understand the research claim without decoding parameter tables.
"""

import pytest
from datetime import timedelta

from lcm_core.conflict import ConflictResolutionEngine
from tests.conftest import REFERENCE_TIME, make_memory

REF = REFERENCE_TIME
engine = ConflictResolutionEngine(uncertainty_threshold=0.0)


def _attack(n_attacks: int, attacker_trust: float, attacker_source: str = "agent_claim"):
    """
    Run n_attacks injections against a high-trust baseline.
    Returns (override_count, final_winner_agent_id).
    """
    baseline = make_memory(
        agent="guardian",
        source="database",
        age_days=0,
        confidence=0.95,
        payload={"system.status": "secure"},
        reference_time=REF,
    )
    trust_table = {"guardian": 0.9}
    for i in range(n_attacks):
        trust_table[f"attacker_{i}"] = attacker_trust

    current = baseline
    overrides = 0
    for i in range(n_attacks):
        attacker_id = f"attacker_{i}"
        attack = make_memory(
            agent=attacker_id,
            source=attacker_source,
            age_days=0,
            confidence=0.9,
            payload={"system.status": "compromised"},
            reference_time=REF + timedelta(seconds=i + 1),  # slightly newer each time
        )
        result = engine.resolve_conflict(current, attack, trust_table, reference_time=REF)
        if result.winner.agent_id == attacker_id:
            overrides += 1
        current = result.winner

    return overrides, current.agent_id


# ---------------------------------------------------------------------------
# Named attack scenarios
# ---------------------------------------------------------------------------


def test_single_attacker_fails():
    """One low-trust attacker cannot override a high-trust baseline."""
    overrides, winner = _attack(n_attacks=1, attacker_trust=0.2)
    assert overrides == 0
    assert winner == "guardian"


def test_twenty_five_attackers_fail():
    """25 low-trust attackers (the original Mandela test) must all fail."""
    overrides, winner = _attack(n_attacks=25, attacker_trust=0.2)
    assert overrides == 0, f"Baseline overridden {overrides} times"
    assert winner == "guardian"


def test_hundred_attackers_fail():
    """Scaling up to 100 attackers must not change the outcome."""
    overrides, winner = _attack(n_attacks=100, attacker_trust=0.1)
    assert overrides == 0, f"Baseline overridden {overrides} times"
    assert winner == "guardian"


def test_very_high_confidence_attacker_still_fails():
    """
    Even if an attacker reports confidence=0.99, without trust or authority
    it cannot override a database-backed high-trust baseline.
    """
    baseline = make_memory(
        agent="guardian", source="database", age_days=0, confidence=0.95,
        payload={"k": "truth"}, reference_time=REF,
    )
    attack = make_memory(
        agent="attacker", source="agent_claim", age_days=0, confidence=0.99,
        payload={"k": "lie"}, reference_time=REF + timedelta(seconds=1),
    )
    result = engine.resolve_conflict(
        baseline, attack,
        trust_table={"guardian": 0.9, "attacker": 0.1},
        reference_time=REF,
    )
    assert result.winner.agent_id == "guardian"


def test_cold_start_attacker_cannot_override():
    """
    A brand-new agent (cold-start trust = 0.5) must not override a
    well-established high-trust baseline even if the claim is newer.
    """
    overrides, winner = _attack(n_attacks=10, attacker_trust=0.5)
    # With trust=0.5 (cold start) against trust=0.9, baseline should hold
    assert winner == "guardian"
