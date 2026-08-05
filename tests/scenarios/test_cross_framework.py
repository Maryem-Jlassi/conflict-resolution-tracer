"""
Scenario tests — Cross-framework memory coherence

Proves that agents from different frameworks (simulated as different
agent IDs with different source types and trust histories) converge
on a single coherent memory state through LCM — without ever
communicating directly.

Each test tells a complete story:
  - Who the agents are
  - What they each claim
  - What LCM should decide and why
"""

import pytest
from datetime import timedelta

from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from tests.conftest import REFERENCE_TIME, make_memory

REF = REFERENCE_TIME
engine = ConflictResolutionEngine(uncertainty_threshold=0.0)


# ---------------------------------------------------------------------------
# Scenario 1: Two frameworks disagree on patient priority
#
# CrewAI triage agent: LOW priority (agent_claim, 5 seconds ago)
# LangChain EHR agent: HIGH priority (agent_claim, now, higher confidence)
# Neither knows about the other.  LCM recency + confidence decides.
# ---------------------------------------------------------------------------


def test_langchain_ehr_overrides_crewai_triage():
    """
    LangChain EHR agent (fresh, higher confidence) overrides
    CrewAI triage agent (slightly older, lower confidence).
    Both use agent_claim — recency + confidence is the tiebreaker.
    """
    crewai_claim = make_memory(
        agent="crewai_triage",
        source="agent_claim",
        age_days=0,
        confidence=0.85,
        payload={"patient.priority": "low"},
        reference_time=REF - timedelta(seconds=5),
    )
    langchain_claim = make_memory(
        agent="langchain_ehr",
        source="agent_claim",
        age_days=0,
        confidence=0.92,
        payload={"patient.priority": "high"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        crewai_claim, langchain_claim,
        trust_table={"crewai_triage": 0.5, "langchain_ehr": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "langchain_ehr"
    assert result.winner.assertion_payload["patient.priority"] == "high"
    assert result.psi_winner > result.psi_loser


# ---------------------------------------------------------------------------
# Scenario 2: AutoGen research agent vs LangGraph verifier
#
# AutoGen found a fact from a database (authoritative).
# LangGraph verifier contradicts it using only LLM reasoning (agent_claim).
# Authority must protect the database-backed fact.
# ---------------------------------------------------------------------------


def test_autogen_database_holds_against_langgraph_llm():
    """
    AutoGen's database-backed fact must hold against a LangGraph
    agent_claim contradiction, regardless of the LLM's confidence.
    """
    autogen_fact = make_memory(
        agent="autogen_researcher",
        source="database",
        age_days=1,
        confidence=0.80,
        payload={"research.finding": "transformers_use_attention"},
        reference_time=REF,
    )
    langgraph_claim = make_memory(
        agent="langgraph_verifier",
        source="agent_claim",
        age_days=0,
        confidence=0.95,
        payload={"research.finding": "transformers_use_convolutions"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        autogen_fact, langgraph_claim,
        trust_table={"autogen_researcher": 0.5, "langgraph_verifier": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "autogen_researcher"
    assert result.winner.assertion_payload["research.finding"] == "transformers_use_attention"


# ---------------------------------------------------------------------------
# Scenario 3: Three sequential framework writes — only the best survives
#
# Write 1 (CrewAI):     agent_claim,  trust=0.5,  confidence=0.7
# Write 2 (LangChain):  database,     trust=0.5,  confidence=0.8  ← wins round 1
# Write 3 (AutoGen):    agent_claim,  trust=0.9,  confidence=0.9
#
# After all three, the database-backed LangChain fact should hold
# because its authority (0.9) + trust (0.5) outweighs the AutoGen
# agent_claim (0.3) even with higher trust.
# ---------------------------------------------------------------------------


def test_three_framework_sequential_writes():
    """
    Three sequential framework writes to the same path.
    The database-backed write must survive both subsequent challenges.
    """
    trust_table = {
        "crewai_agent":    0.5,
        "langchain_agent": 0.5,
        "autogen_agent":   0.9,
    }

    crewai_write = make_memory(
        agent="crewai_agent", source="agent_claim",
        age_days=0, confidence=0.7,
        payload={"shared.fact": "crewai_value"},
        reference_time=REF - timedelta(minutes=10),
    )
    langchain_write = make_memory(
        agent="langchain_agent", source="database",
        age_days=0, confidence=0.8,
        payload={"shared.fact": "langchain_value"},
        reference_time=REF - timedelta(minutes=5),
    )
    autogen_write = make_memory(
        agent="autogen_agent", source="agent_claim",
        age_days=0, confidence=0.9,
        payload={"shared.fact": "autogen_value"},
        reference_time=REF,
    )

    # Round 1: CrewAI vs LangChain
    r1 = engine.resolve_conflict(crewai_write, langchain_write, trust_table, reference_time=REF)
    assert r1.winner.agent_id == "langchain_agent", "Database should beat agent_claim in round 1"

    # Round 2: LangChain (winner) vs AutoGen
    r2 = engine.resolve_conflict(r1.winner, autogen_write, trust_table, reference_time=REF)
    assert r2.winner.agent_id == "langchain_agent", (
        "Database authority (0.9) should protect LangChain fact against "
        "AutoGen agent_claim (0.3) even with higher trust"
    )


# ---------------------------------------------------------------------------
# Scenario 4: Same-session writes — last same-agent write always wins
#
# When the same agent updates a fact twice, the newer version should win
# regardless of confidence (the agent is refining its own claim).
# ---------------------------------------------------------------------------


def test_same_agent_newer_write_wins():
    """
    An agent updating its own claim: the newer write always supersedes the older one.
    This is not a conflict — it's a refinement.
    """
    first_write = make_memory(
        agent="crewai_analyst", source="agent_claim",
        age_days=0, confidence=0.6,
        payload={"analysis.result": "preliminary"},
        reference_time=REF - timedelta(minutes=2),
    )
    second_write = make_memory(
        agent="crewai_analyst", source="agent_claim",
        age_days=0, confidence=0.9,
        payload={"analysis.result": "final"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        first_write, second_write,
        trust_table={"crewai_analyst": 0.7},
        reference_time=REF,
    )

    assert result.winner.agent_id == "crewai_analyst"
    assert result.winner.assertion_payload["analysis.result"] == "final"


# ---------------------------------------------------------------------------
# Scenario 5: Verified user input overrides all automated agents
#
# A human admin explicitly overrides an agent's recommendation.
# user_input authority (1.0) must beat any automated source.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scenario 5: Verified user input overrides agent_claim at equal trust
#
# user_input authority (1.0) beats agent_claim (0.3) when trust is equal.
# This is the realistic case: human admin overrides an LLM's suggestion.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_source",
    ["agent_claim", "tool_output"],
)
def test_user_input_overrides_lower_authority_sources(agent_source):
    """
    user_input (authority=1.0) overrides agent_claim and tool_output
    at equal trust. Authority gap is the deciding factor.
    """
    automated = make_memory(
        agent="automated_agent", source=agent_source,
        age_days=0, confidence=0.99,
        payload={"config.value": "agent_recommendation"},
        reference_time=REF - timedelta(seconds=1),
    )
    user_override = make_memory(
        agent="human_admin", source="user_input",
        age_days=0, confidence=0.7,
        payload={"config.value": "human_decision"},
        reference_time=REF,
    )

    result = engine.resolve_conflict(
        automated, user_override,
        trust_table={"automated_agent": 0.5, "human_admin": 0.5},
        reference_time=REF,
    )

    assert result.winner.agent_id == "human_admin", (
        f"user_input should override {agent_source} at equal trust"
    )
