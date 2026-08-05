"""
Unit tests — ConfidenceEngine

Tests the evidence authority ordering, score calculation, and weight
configuration in isolation. No conflict engine, no pipeline, no agents.
"""

import pytest
from lcm_core.confidence_engine import (
    ConfidenceEngine,
    ConfidenceWeights,
    EvidenceRecord,
    EvidenceType,
    EVIDENCE_AUTHORITY,
)
from tests.conftest import make_evidence

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ce():
    return ConfidenceEngine()


# ---------------------------------------------------------------------------
# Weight validation
# ---------------------------------------------------------------------------

def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        ConfidenceWeights(evidence=0.5, agreement=0.3, verification=0.3).validate()


def test_valid_weights_do_not_raise():
    ConfidenceWeights(evidence=0.5, agreement=0.3, verification=0.2).validate()


# ---------------------------------------------------------------------------
# Authority ordering — full hierarchy in one parametrized test
# ---------------------------------------------------------------------------

_ORDERED = [
    EvidenceType.USER_INPUT,    # 1.00
    EvidenceType.DATABASE,      # 0.90
    EvidenceType.TOOL_OUTPUT,   # 0.85
    EvidenceType.DOCUMENT,      # 0.75
    EvidenceType.AGENT_CLAIM,   # 0.30
]


@pytest.mark.parametrize(
    "higher, lower",
    [(_ORDERED[i], _ORDERED[i + 1]) for i in range(len(_ORDERED) - 1)],
)
def test_authority_hierarchy_strict(higher, lower):
    assert EVIDENCE_AUTHORITY[higher] > EVIDENCE_AUTHORITY[lower]


@pytest.mark.parametrize("ev_type", list(EvidenceType))
def test_single_evidence_score_in_range(ce, ev_type):
    score = ce.calculate([make_evidence(ev_type.value)])
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Source-type string → score mapping covers all aliases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source_str, expected",
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
        ("unknown_xyz", EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),
        (None,          EVIDENCE_AUTHORITY[EvidenceType.AGENT_CLAIM]),
    ],
)
def test_source_type_string_mapping(ce, source_str, expected):
    assert ce.score_from_source_type(source_str) == expected


# ---------------------------------------------------------------------------
# Relevance scales score proportionally
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("relevance", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_relevance_monotone(ce, relevance):
    rec = EvidenceRecord(evidence_type=EvidenceType.DATABASE, relevance_score=relevance)
    score = ce.calculate([rec])
    expected_ev = EVIDENCE_AUTHORITY[EvidenceType.DATABASE] * relevance
    expected = 0.5 * expected_ev + 0.3 * 0.5 + 0.2 * 0.5
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# Agreement ratio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "agreeing, total, ratio",
    [(0, 4, 0.0), (2, 4, 0.5), (4, 4, 1.0), (0, 0, 0.5)],
)
def test_agreement_score(ce, agreeing, total, ratio):
    ev = make_evidence("tool_output")
    score = ce.calculate([ev], agreeing_agents=agreeing, total_independent_agents=total)
    expected = 0.5 * EVIDENCE_AUTHORITY[EvidenceType.TOOL_OUTPUT] + 0.3 * ratio + 0.2 * 0.5
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# Verification consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "consistent, verif",
    [(True, 1.0), (None, 0.5), (False, 0.0)],
)
def test_verification_states(ce, consistent, verif):
    ev = make_evidence("database")
    score = ce.calculate([ev], verified_memories_consistent=consistent)
    expected = 0.5 * EVIDENCE_AUTHORITY[EvidenceType.DATABASE] + 0.3 * 0.5 + 0.2 * verif
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# Cold-start
# ---------------------------------------------------------------------------

def test_no_evidence_returns_agent_claim_floor(ce):
    expected = 0.5 * 0.3 + 0.3 * 0.5 + 0.2 * 0.5
    assert abs(ce.calculate([]) - expected) < 1e-9


def test_cold_start_database_evidence(ce):
    assert ce.cold_start_confidence([make_evidence("database")]) == EVIDENCE_AUTHORITY[EvidenceType.DATABASE]


# ---------------------------------------------------------------------------
# Custom weights applied correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "w_ev, w_ag, w_vr",
    [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.5, 0.3, 0.2)],
)
def test_custom_weights(w_ev, w_ag, w_vr):
    custom = ConfidenceEngine(ConfidenceWeights(evidence=w_ev, agreement=w_ag, verification=w_vr))
    ev = make_evidence("database")
    score = custom.calculate([ev], agreeing_agents=3, total_independent_agents=3,
                              verified_memories_consistent=True)
    expected = min(1.0, w_ev * EVIDENCE_AUTHORITY[EvidenceType.DATABASE] + w_ag * 1.0 + w_vr * 1.0)
    assert abs(score - expected) < 1e-9
