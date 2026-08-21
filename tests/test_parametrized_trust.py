"""
Parametrized trust manager tests.

Proves trust accumulation, domain isolation, fallback, and cold-start
properties hold across a variety of outcome histories.
"""

import pytest
from crt_core.trust_manager import TrustManager

# ---------------------------------------------------------------------------
# 1. Trust score converges to correct/total ratio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "correct, wrong, expected_trust",
    [
        (10,  0,  1.0),
        (0,  10,  0.0),
        (5,   5,  0.5),
        (7,   3,  0.7),
        (1,   9,  0.1),
        (90, 10,  0.9),
    ],
)
def test_trust_converges_to_ratio(correct, wrong, expected_trust):
    trust = TrustManager()
    for _ in range(correct):
        trust.record_outcome("agent", correct=True)
    for _ in range(wrong):
        trust.record_outcome("agent", correct=False)
    score = trust.get_trust("agent")
    assert abs(score - expected_trust) < 1e-9, (
        f"correct={correct}, wrong={wrong}: expected {expected_trust}, got {score}"
    )


# ---------------------------------------------------------------------------
# 2. Cold-start prior for unknown agents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", ["new_agent", "ghost", "never_seen_42"])
def test_cold_start_returns_prior(agent_id):
    trust = TrustManager(cold_start_prior=0.5)
    assert trust.get_trust(agent_id) == 0.5


@pytest.mark.parametrize("prior", [0.1, 0.3, 0.5, 0.7, 0.9])
def test_custom_prior_respected(prior):
    trust = TrustManager(cold_start_prior=prior)
    assert trust.get_trust("unknown") == prior


# ---------------------------------------------------------------------------
# 3. Domain isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain_a, correct_a, domain_b, correct_b",
    [
        ("healthcare", 9, "finance",  1),
        ("coding",     5, "medicine", 0),
        ("legal",      8, "sports",   2),
    ],
)
def test_domain_trust_is_isolated(domain_a, correct_a, domain_b, correct_b):
    """Trust in domain A must not bleed into domain B."""
    trust = TrustManager()
    total = 10

    for _ in range(correct_a):
        trust.record_outcome("agent", correct=True, domain=domain_a)
    for _ in range(total - correct_a):
        trust.record_outcome("agent", correct=False, domain=domain_a)

    for _ in range(correct_b):
        trust.record_outcome("agent", correct=True, domain=domain_b)
    for _ in range(total - correct_b):
        trust.record_outcome("agent", correct=False, domain=domain_b)

    score_a = trust.get_trust("agent", domain=domain_a)
    score_b = trust.get_trust("agent", domain=domain_b)

    assert abs(score_a - correct_a / total) < 1e-9
    assert abs(score_b - correct_b / total) < 1e-9
    assert score_a != score_b or correct_a == correct_b


# ---------------------------------------------------------------------------
# 4. Domain fallback to _global
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "global_correct, global_wrong",
    [
        (8, 2),
        (3, 7),
        (5, 5),
    ],
)
def test_unknown_domain_falls_back_to_global(global_correct, global_wrong):
    trust = TrustManager()
    total = global_correct + global_wrong
    for _ in range(global_correct):
        trust.record_outcome("agent", correct=True, domain="_global")
    for _ in range(global_wrong):
        trust.record_outcome("agent", correct=False, domain="_global")

    expected = global_correct / total
    # A domain with no history should fall back to _global
    assert abs(trust.get_trust("agent", domain="unseen_domain") - expected) < 1e-9


# ---------------------------------------------------------------------------
# 5. Multiple agents are independent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agents_outcomes",
    [
        {"a": (9, 1), "b": (1, 9), "c": (5, 5)},
        {"x": (10, 0), "y": (0, 10)},
        {"p": (3, 7), "q": (7, 3), "r": (5, 5)},
    ],
)
def test_multiple_agents_independent(agents_outcomes):
    trust = TrustManager()
    for agent, (correct, wrong) in agents_outcomes.items():
        for _ in range(correct):
            trust.record_outcome(agent, correct=True)
        for _ in range(wrong):
            trust.record_outcome(agent, correct=False)

    for agent, (correct, wrong) in agents_outcomes.items():
        total = correct + wrong
        expected = correct / total
        score = trust.get_trust(agent)
        assert abs(score - expected) < 1e-9, (
            f"Agent {agent}: expected {expected}, got {score}"
        )


# ---------------------------------------------------------------------------
# 6. build_trust_table covers all agents including unknowns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "known_agents, unknown_agents",
    [
        ({"a": 1.0, "b": 0.0}, ["c", "d"]),
        ({"x": 0.5}, ["y"]),
        ({}, ["p", "q"]),
    ],
)
def test_build_trust_table(known_agents, unknown_agents):
    trust = TrustManager(cold_start_prior=0.5)
    for agent, score in known_agents.items():
        if score == 1.0:
            trust.record_outcome(agent, correct=True)
        elif score == 0.0:
            trust.record_outcome(agent, correct=False)
        else:
            trust.record_outcome(agent, correct=True)
            trust.record_outcome(agent, correct=False)

    all_agents = list(known_agents.keys()) + unknown_agents
    table = trust.build_trust_table(all_agents)

    for agent in unknown_agents:
        assert table[agent] == 0.5, f"Unknown {agent} should get prior 0.5"
    assert set(table.keys()) == set(all_agents)


# ---------------------------------------------------------------------------
# 7. record_outcome always updates _global in addition to specific domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["healthcare", "finance", "coding", "_global"])
def test_record_outcome_always_updates_global(domain):
    trust = TrustManager()
    for _ in range(8):
        trust.record_outcome("agent", correct=True, domain=domain)
    for _ in range(2):
        trust.record_outcome("agent", correct=False, domain=domain)

    # _global must always be updated regardless of which domain was specified
    global_score = trust.get_trust("agent", domain="_global")
    assert abs(global_score - 0.8) < 1e-9, (
        f"domain={domain}: _global should be 0.8, got {global_score}"
    )
