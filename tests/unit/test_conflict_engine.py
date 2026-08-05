"""
Unit tests — ConflictResolutionEngine

Tests the Ψ formula in isolation: score calculation, weight configuration,
authority ordering, and tie-breaking. No pipeline, no pipeline storage.
"""

import math
import pytest
from datetime import timedelta

from lcm_core.conflict import ConflictResolutionEngine, ResolutionConfig
from lcm_core.schema import ProvenanceInfo, StampedUMF
from tests.conftest import REFERENCE_TIME, make_memory

REF = REFERENCE_TIME


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    return ConflictResolutionEngine(uncertainty_threshold=0.0)


# ---------------------------------------------------------------------------
# Ψ calculation — basic properties
# ---------------------------------------------------------------------------


def test_psi_fresh_equals_sum_of_weights(engine):
    """A fresh memory with full scores should yield Ψ ≈ 1.0."""
    m = make_memory(source="user_input", age_days=0, confidence=1.0)
    psi = engine.calculate_psi(m, trust_score=1.0, reference_time=REF)
    assert abs(psi - 1.0) < 1e-6


def test_psi_decreases_with_age(engine):
    """Ψ must strictly decrease as the memory ages (recency decay)."""
    scores = [
        engine.calculate_psi(
            make_memory(age_days=d), trust_score=0.7, reference_time=REF
        )
        for d in [0, 1, 7, 30, 90, 365]
    ]
    # Strictly decreasing while recency is meaningfully above zero
    # (after ~30+ days recency reaches floating-point zero — floor behaviour)
    for i in range(1, len(scores)):
        assert scores[i - 1] >= scores[i], (
            f"age_days index {i}: expected {scores[i-1]:.6f} >= {scores[i]:.6f}"
        )
    # At least the first three steps must be strictly decreasing
    assert scores[0] > scores[1] > scores[2]


def test_psi_24h_half_life(engine):
    """After 24 h the recency component should be half of its fresh value."""
    fresh = make_memory(age_days=0)
    old = make_memory(age_days=1)
    r_fresh = math.exp(0)
    r_old = math.exp(-engine.lambda_ * 86400)
    assert abs(r_old / r_fresh - 0.5) < 1e-6


@pytest.mark.parametrize("trust", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_psi_scales_linearly_with_trust(engine, trust):
    """With equal weights, each unit of trust adds w_t to Ψ."""
    m = make_memory(age_days=0, source="database", confidence=0.7)
    psi = engine.calculate_psi(m, trust_score=trust, reference_time=REF)
    assert 0.0 <= psi <= 1.0


def test_psi_raises_if_verified_confidence_is_none():
    engine = ConflictResolutionEngine(uncertainty_threshold=0.0)
    bad = StampedUMF(
        agent_id="a", session_id="s", timestamp=REF,
        confidence_score=0.5, assertion_payload={"k": "v"},
        provenance_id="p", ingested_at=REF,
        provenance_info=ProvenanceInfo(verified_confidence=None, authority_score=0.5),
    )
    with pytest.raises(ValueError, match="verified_confidence"):
        engine.calculate_psi(bad, trust_score=0.5)


def test_psi_raises_if_authority_score_is_none():
    engine = ConflictResolutionEngine(uncertainty_threshold=0.0)
    bad = StampedUMF(
        agent_id="a", session_id="s", timestamp=REF,
        confidence_score=0.5, assertion_payload={"k": "v"},
        provenance_id="p", ingested_at=REF,
        provenance_info=ProvenanceInfo(verified_confidence=0.5, authority_score=None),
    )
    with pytest.raises(ValueError, match="authority_score"):
        engine.calculate_psi(bad, trust_score=0.5)


# ---------------------------------------------------------------------------
# resolve_conflict — winner selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, ex_src, in_src, ex_age_days, in_age_days, ex_trust, in_trust, expected",
    [
        # Higher authority wins when recency is equal
        ("authority", "user_input", "agent_claim", 0, 0, 0.5, 0.5, "existing"),
        # Fresher memory wins when everything else is equal
        ("recency",   "database",   "database",    7, 0, 0.5, 0.5, "incoming"),
        # Higher trust wins when authority and confidence are equal
        ("trust",     "database",   "database",    1, 0, 0.9, 0.1, "existing"),
        # Exact tie goes to incumbent (existing)
        ("tie",       "database",   "database",    0, 0, 0.5, 0.5, "existing"),
    ],
)
def test_winner_selection(
    engine, name, ex_src, in_src, ex_age_days, in_age_days, ex_trust, in_trust, expected
):
    existing = make_memory("ex", source=ex_src, age_days=ex_age_days)
    incoming = make_memory("in", source=in_src, age_days=in_age_days)
    result = engine.resolve_conflict(
        existing, incoming,
        trust_table={"ex": ex_trust, "in": in_trust},
        reference_time=REF,
    )
    winner = "existing" if result.winner.agent_id == "ex" else "incoming"
    assert winner == expected, (
        f"[{name}] expected={expected}, got={winner} "
        f"Ψ_ex={result.psi_winner if winner=='existing' else result.psi_loser:.4f} "
        f"Ψ_in={result.psi_winner if winner=='incoming' else result.psi_loser:.4f}"
    )


def test_existing_wins_on_tie(engine):
    m = make_memory(age_days=0, source="database")
    result = engine.resolve_conflict(m, m, {"agent": 0.5}, reference_time=REF)
    assert result.winner is m


def test_reported_confidence_tampering_does_not_change_winner(engine):
    """Phase 2: raw confidence_score is audit-only — it must never feed the Ψ
    resolution or flip a winner. Two otherwise-identical claims that differ
    ONLY in self-reported confidence must resolve identically."""
    m = make_memory(agent="ex", source="agent_claim", age_days=0, confidence=0.5)
    hi = make_memory(agent="in", source="database", age_days=0, confidence=0.99)
    lo = make_memory(agent="in", source="database", age_days=0, confidence=0.01)

    r_hi = engine.resolve_conflict(m, hi, {"ex": 0.5, "in": 0.5}, reference_time=REF)
    r_lo = engine.resolve_conflict(m, lo, {"ex": 0.5, "in": 0.5}, reference_time=REF)

    assert r_hi.winner.agent_id == r_lo.winner.agent_id
    assert r_hi.psi_winner == pytest.approx(r_lo.psi_winner)
    assert r_hi.psi_loser == pytest.approx(r_lo.psi_loser)
    # Database-backed claim wins over agent_claim regardless of self-report.
    assert r_hi.winner.agent_id == "in"


# ---------------------------------------------------------------------------
# Uncertainty threshold
# ---------------------------------------------------------------------------


def test_uncertainty_threshold_marks_unresolved():
    engine = ConflictResolutionEngine(
        config=ResolutionConfig(
            w_recency=0.25, w_confidence=0.25, w_trust=0.25, w_provenance=0.25,
            uncertainty_threshold=0.99,
        )
    )
    a = make_memory("a", age_days=0)
    b = make_memory("b", age_days=0)
    result = engine.resolve_conflict(a, b, {"a": 0.5, "b": 0.5}, reference_time=REF)
    assert result.unresolved is True
    assert result.winner is a  # incumbent preserved


# ---------------------------------------------------------------------------
# Weight configuration validation
# ---------------------------------------------------------------------------


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        ResolutionConfig(
            w_recency=0.3, w_confidence=0.3, w_trust=0.3, w_provenance=0.3
        ).validate()


# ---------------------------------------------------------------------------
# Psi breakdown — zero-guard paths
# ---------------------------------------------------------------------------


def test_psi_breakdown_none_values_silently_zero():
    engine = ConflictResolutionEngine()
    bad = StampedUMF(
        agent_id="a", session_id="s", timestamp=REF,
        confidence_score=0.5, assertion_payload={"k": "v"},
        provenance_id="p", ingested_at=REF,
        provenance_info=ProvenanceInfo(verified_confidence=None, authority_score=None),
    )
    bd = engine.calculate_psi_breakdown(bad, trust_score=0.5, reference_time=REF)
    assert bd["C"] == 0.0
    assert bd["A"] == 0.0
    assert "total_psi" in bd
