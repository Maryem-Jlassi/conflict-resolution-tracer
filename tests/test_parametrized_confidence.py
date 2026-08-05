"""
Parametrized confidence engine tests.

Proves the evidence authority ordering and score properties hold
across all source types and combinations, not just isolated cases.
"""

import pytest
from lcm_core.confidence_engine import (
    ConfidenceEngine,
    ConfidenceWeights,
    EvidenceRecord,
    EvidenceType,
    EVIDENCE_AUTHORITY,
)

engine = ConfidenceEngine()

# ---------------------------------------------------------------------------
# 1. Complete authority ordering
# ---------------------------------------------------------------------------

ORDERED_TYPES = [
    EvidenceType.USER_INPUT,    # 1.0
    EvidenceType.DATABASE,      # 0.9
    EvidenceType.TOOL_OUTPUT,   # 0.85
    EvidenceType.DOCUMENT,      # 0.75
    EvidenceType.AGENT_CLAIM,   # 0.3
]


@pytest.mark.parametrize(
    "higher, lower",
    [(ORDERED_TYPES[i], ORDERED_TYPES[i + 1]) for i in range(len(ORDERED_TYPES) - 1)],
)
def test_authority_ordering_strict(higher, lower):
    """Every adjacent pair in the authority hierarchy must be strictly ordered."""
    assert EVIDENCE_AUTHORITY[higher] > EVIDENCE_AUTHORITY[lower], (
        f"{higher.value} ({EVIDENCE_AUTHORITY[higher]}) should be > "
        f"{lower.value} ({EVIDENCE_AUTHORITY[lower]})"
    )


@pytest.mark.parametrize("ev_type", list(EvidenceType))
def test_single_evidence_score_in_range(ev_type):
    """Score for any single evidence type must be in [0, 1]."""
    rec = EvidenceRecord(evidence_type=ev_type, relevance_score=1.0)
    score = engine.calculate([rec])
    assert 0.0 <= score <= 1.0, f"{ev_type.value} score {score} out of range"


# ---------------------------------------------------------------------------
# 2. source_type string → score mapping covers all aliases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source_str, expected_score",
    [
        ("user_input",  EVIDENCE_AUTHORITY[EvidenceType.USER_INPUT]),
        ("database",    EVIDENCE_AUTHORITY[EvidenceType.DATABASE]),
        ("db",          EVIDENCE_AUTHORITY[EvidenceType.DATABASE]),
        ("tool_output", EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT]),
        ("tool",        EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT]),
        ("document",    EVIDENCE_AUTHORITY[EvidenceType.DOCUMENT]),
        ("doc",         EVIDENCE_AUTHORITY[EvidenceType.DOCUMENT]),
        ("agent_claim", EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),
        ("llm",         EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),
        ("agent",       EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),
        ("unknown_xyz", EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),  # fallback
        (None,          EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),  # None fallback
    ],
)
def test_source_type_string_mapping(source_str, expected_score):
    score = engine.score_from_source_type(source_str)
    assert score == expected_score, f"source_type='{source_str}': expected {expected_score}, got {score}"


# ---------------------------------------------------------------------------
# 3. Relevance scales score proportionally
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relevance", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_relevance_monotone_for_database(relevance):
    """Higher relevance → higher evidence score (all else equal)."""
    rec = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=relevance)
    score = engine.calculate([rec])
    expected_evidence = EVIDENCE_AUTHORITY[EvidenceType.DATABASE] * relevance
    # evidence component contributes 50% of total
    # agreement neutral (0.5), verification neutral (0.5)
    expected_total = 0.5 * expected_evidence + 0.3 * 0.5 + 0.2 * 0.5
    assert abs(score - expected_total) < 1e-9, (
        f"relevance={relevance}: expected {expected_total:.6f}, got {score:.6f}"
    )


# ---------------------------------------------------------------------------
# 4. Agreement scoring — parametrized over agreeing/total ratios
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "agreeing, total, expected_ratio",
    [
        (0, 4, 0.0),
        (1, 4, 0.25),
        (2, 4, 0.5),
        (3, 4, 0.75),
        (4, 4, 1.0),
        (0, 0, 0.5),  # no data → neutral
    ],
)
def test_agreement_score_ratio(agreeing, total, expected_ratio):
    """Agreement score must equal agreeing/total (or 0.5 for no data)."""
    ev = EvidenceRecord(evidence_type=EvidenceType.TOOL_OUTPUT, relevance_score=1.0)
    score = engine.calculate(
        [ev], agreeing_agents=agreeing, total_independent_agents=total
    )
    # Manually compute expected total
    evidence = EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT] * 1.0
    expected = 0.5 * evidence + 0.3 * expected_ratio + 0.2 * 0.5
    assert abs(score - expected) < 1e-9, (
        f"{agreeing}/{total}: expected {expected:.6f}, got {score:.6f}"
    )


# ---------------------------------------------------------------------------
# 5. Verification consistency — three states
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "consistent, expected_verif_score",
    [
        (True,  1.0),
        (None,  0.5),
        (False, 0.0),
    ],
)
def test_verification_score_states(consistent, expected_verif_score):
    ev = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=1.0)
    score = engine.calculate([ev], verified_memories_consistent=consistent)
    evidence = EVIDENCE_AUTHORITY[EvidenceType.DATABASE]
    expected = 0.5 * evidence + 0.3 * 0.5 + 0.2 * expected_verif_score
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# 6. No evidence → agent_claim floor
# ---------------------------------------------------------------------------

def test_no_evidence_returns_agent_claim_floor():
    score = engine.calculate([])
    # evidence_score = AGENT_CLAIM authority (0.3)
    # agreement = neutral (0.5), verification = neutral (0.5)
    expected = 0.5 * 0.3 + 0.3 * 0.5 + 0.2 * 0.5
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# 7. Custom weights — verify they're applied correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "w_ev, w_ag, w_vr",
    [
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.5, 0.3, 0.2),
        (0.33, 0.33, 0.34),
    ],
)
def test_custom_weights_applied(w_ev, w_ag, w_vr):
    custom_engine = ConfidenceEngine(
        weights=ConfidenceWeights(evidence=w_ev, agreement=w_ag, verification=w_vr)
    )
    ev = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=1.0)
    score = custom_engine.calculate(
        [ev],
        agreeing_agents=3, total_independent_agents=3,
        verified_memories_consistent=True,
    )
    evidence = EVIDENCE_AUTHORITY[EvidenceType.DATABASE]
    expected = w_ev * evidence + w_ag * 1.0 + w_vr * 1.0
    assert abs(score - min(1.0, expected)) < 1e-9
