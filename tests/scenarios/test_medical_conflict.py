"""
Scenario tests — Medical domain conflicts

These tests mirror the worked examples from the paper.
Each test represents a specific clinical scenario with a clear ground-truth answer.
They are kept verbose intentionally: a reviewer should be able to read each
test and understand the research claim it validates.
"""

import pytest
from datetime import timedelta

from lcm_core.conflict import ConflictResolutionEngine
from tests.conftest import REFERENCE_TIME, make_memory

REF = REFERENCE_TIME
engine = ConflictResolutionEngine(uncertainty_threshold=0.0)


# ---------------------------------------------------------------------------
# Scenario 1: Blood-type conflict (worked example from paper §4.2)
#
# EHR record says A+, entered 8 months ago.
# New lab result says O+, just received.
# Both agents equally trusted.  Recency should decide.
# ---------------------------------------------------------------------------


def test_blood_type_fresh_lab_beats_stale_ehr():
    """
    Spec worked example.  O+ lab result (fresh) should override A+ EHR record (8 months old)
    when trust and confidence are equal.
    """
    ehr_record = make_memory(
        agent="ehr_agent",
        source="database",
        age_days=240,        # ~8 months
        confidence=0.95,
        payload={"patient.blood_type": "A+"},
        reference_time=REF,
    )
    lab_result = make_memory(
        agent="lab_agent",
        source="database",
        age_days=0,
        confidence=0.93,
        payload={"patient.blood_type": "O+"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        ehr_record, lab_result,
        trust_table={"ehr_agent": 0.9, "lab_agent": 0.9},
        reference_time=REF,
    )

    assert result.winner.agent_id == "lab_agent"
    assert result.winner.assertion_payload["patient.blood_type"] == "O+"
    # Quantitative check: winner Ψ meaningfully higher
    assert result.psi_winner > result.psi_loser


# ---------------------------------------------------------------------------
# Scenario 2: Drug interaction alert
#
# Database-backed contraindication from pharmacy system (authoritative, slightly old).
# LLM agent confidently asserts "no known interaction" (fresh, but unsupported).
# Authority must win over self-reported confidence.
# ---------------------------------------------------------------------------


def test_drug_interaction_database_beats_llm_assertion():
    pharmacy_alert = make_memory(
        agent="pharmacy_system",
        source="database",
        age_days=1,
        confidence=0.7,
        payload={"patient.drug_interaction": "contraindicated"},
        reference_time=REF,
    )
    llm_claim = make_memory(
        agent="llm_assistant",
        source="agent_claim",
        age_days=0,
        confidence=0.99,   # very high self-reported confidence
        payload={"patient.drug_interaction": "safe"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        pharmacy_alert, llm_claim,
        trust_table={"pharmacy_system": 0.5, "llm_assistant": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "pharmacy_system", (
        "Database-backed contraindication should hold against high-confidence LLM claim"
    )


# ---------------------------------------------------------------------------
# Scenario 3: Triage priority disagreement
#
# CrewAI triage agent assessed LOW priority (5 minutes ago).
# LangChain EHR agent assessed HIGH priority (now, higher confidence).
# Both from agent_claim source; recency + confidence decides.
# ---------------------------------------------------------------------------


def test_ehr_high_priority_overrides_triage_low_priority():
    triage_assessment = make_memory(
        agent="crewai_triage",
        source="agent_claim",
        age_days=0,
        confidence=0.85,
        payload={"patient.priority": "low"},
        reference_time=REF - timedelta(minutes=5),
    )
    ehr_assessment = make_memory(
        agent="langchain_ehr",
        source="agent_claim",
        age_days=0,
        confidence=0.92,
        payload={"patient.priority": "high"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        triage_assessment, ehr_assessment,
        trust_table={"crewai_triage": 0.5, "langchain_ehr": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "langchain_ehr"
    assert result.winner.assertion_payload["patient.priority"] == "high"


# ---------------------------------------------------------------------------
# Scenario 4: Veteran specialist vs new generalist (trust matters)
#
# Specialist doctor (trust=0.95 from 50 correct diagnoses) says "diabetes".
# New generalist (trust=0.5 cold start) says "healthy" fresh today.
# Historical trust must protect the specialist's diagnosis.
# ---------------------------------------------------------------------------


def test_specialist_trust_protects_diagnosis_against_generalist():
    specialist_diagnosis = make_memory(
        agent="specialist",
        source="database",
        age_days=7,
        confidence=0.9,
        payload={"patient.diagnosis": "type_2_diabetes"},
        reference_time=REF,
    )
    generalist_claim = make_memory(
        agent="generalist",
        source="agent_claim",
        age_days=0,
        confidence=0.8,
        payload={"patient.diagnosis": "healthy"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        specialist_diagnosis, generalist_claim,
        trust_table={"specialist": 0.95, "generalist": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "specialist"
