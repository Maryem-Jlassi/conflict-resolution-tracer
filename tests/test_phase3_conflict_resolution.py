"""
Phase 3 — Golden conflict scenarios (spec worked examples).

These two tests are the narrative heart of the paper.
Parametrized coverage lives in test_parametrized_conflict.py.
"""

import pytest
from datetime import datetime, timedelta

from lcm_core.conflict import ConflictResolutionEngine
from tests.conftest import make_memory

NOW = datetime(2026, 7, 12, 10, 0, 0)


@pytest.fixture
def engine():
    return ConflictResolutionEngine(uncertainty_threshold=0.0)


def test_blood_type_recency_dominates(engine):
    """
    Spec §4.2 worked example.

    EHR record (A+) is 8 months old. Lab result (O+) just arrived.
    Both agents equally trusted. Fresh recency wins.
    """
    ehr = make_memory("ehr_agent", source="database", age_days=240,
                      confidence=0.95, payload={"patient.blood_type": "A+"},
                      reference_time=NOW)
    lab = make_memory("lab_agent", source="database", age_days=0,
                      confidence=0.93, payload={"patient.blood_type": "O+"},
                      reference_time=NOW)

    result = engine.resolve_conflict(
        ehr, lab, {"ehr_agent": 0.9, "lab_agent": 0.9}, reference_time=NOW
    )

    assert result.winner.agent_id == "lab_agent"
    assert result.psi_winner > result.psi_loser


def test_high_trust_old_beats_low_trust_new(engine):
    """
    Contrast case.

    Trusted database record (8 months old) vs fresh low-trust agent claim.
    Trust + authority combine to overcome recency disadvantage.
    """
    trusted = make_memory("trusted_agent", source="database", age_days=240,
                          confidence=0.95, payload={"config.mode": "production"},
                          reference_time=NOW)
    untrusted = make_memory("untrusted_agent", source="agent_claim", age_days=0,
                            confidence=0.5, payload={"config.mode": "debug"},
                            reference_time=NOW)

    result = engine.resolve_conflict(
        trusted, untrusted,
        {"trusted_agent": 0.9, "untrusted_agent": 0.2},
        reference_time=NOW,
    )

    assert result.winner.agent_id == "trusted_agent"
