"""Schema fixtures only; these are not empirical real-agent results."""

import copy

import pytest

from experiments.result_schema import validate_real_agent_artifact


def valid_fixture():
    call = {"name": "write_memory", "arguments": {"path": "research.key_insight"},
            "result": "committed", "success": True}
    invocation = {"agent_id": "a1", "role": "research", "model": "llama3.1:8b",
                  "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:01Z",
                  "prompt_sha256": "1" * 64, "response_sha256": "2" * 64,
                  "raw_response": "used a native tool", "structured_tool_calls": [call]}
    second = dict(invocation, agent_id="a2", role="verification",
                  response_sha256="3" * 64, structured_tool_calls=[])
    return {"classification": "real_agent", "agent_mode": "real_llm", "backend": "ollama",
            "model": "llama3.1:8b", "llm_available": True, "run_id": "run-1",
            "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:02Z",
            "git_commit": "abc", "configuration_hash": "4" * 64,
            "database_identity": "temporary.sqlite", "service_url": "http://127.0.0.1:8123",
            "scenario": "basic", "scenario_contract": {"scenario_id": "basic",
                "expected_conflict_opportunities": 0}, "trials": 1,
            "trial_results": [{"run_id": "trial-1"}], "model_invocations": [invocation, second],
            "write_log": [{"agent_id": "a1", "path": "research.key_insight", "value": "x",
                           "status": "committed", "success": True}], "conflict_log": [],
            "memory_readbacks": [{"success": True, "server_provenance": {"provenance_id": "p"}}],
            "final_memory": {"research.key_insight": {"verified_confidence": .3,
                "authority_score": .3, "source_type": "agent_claim_default"}},
            "initial_trust": {"a1": {"trust_score": .5, "outcome_count": 0},
                              "a2": {"trust_score": .5, "outcome_count": 0}},
            "scripted_fallback_writes": False, "mock_data_used": False,
            "trust_initialized": False, "ground_truth_status": "not_available",
            "resolution_accuracy": None, "stats": {"final_state_correct": None,
                "attack_success_rate": None}}


def test_valid_schema_fixture_is_accepted_but_not_empirical():
    assert validate_real_agent_artifact(valid_fixture())["valid"]


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(classification="diagnostic"),
    lambda a: a["model_invocations"].clear(),
    lambda a: a["model_invocations"].__setitem__(slice(1, None), []),
    lambda a: a["model_invocations"][0].update(raw_response="", response_sha256=None),
    lambda a: a["model_invocations"][0].update(structured_tool_calls=[]),
    lambda a: a["memory_readbacks"].clear(),
    lambda a: a.update(scripted_fallback_writes=True),
    lambda a: a.update(trials=2),
    lambda a: a.update(ground_truth_status="not_available", resolution_accuracy=.9),
])
def test_rejects_nonqualifying_artifacts(mutation):
    artifact = copy.deepcopy(valid_fixture())
    mutation(artifact)
    assert not validate_real_agent_artifact(artifact)["valid"]

