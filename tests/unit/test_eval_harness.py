"""
Evaluation harness tests (Phase 10).

Verifies the deterministic offline eval runs the core scenarios through the
real pipeline and scores final-state correctness, and that the real-LLM mode
honestly skips/blocks when no live servers exist (never fabricated).
"""

import pytest

from experiments import eval_harness
from experiments.result_schema import validate_result_schema


class TestOfflineEval:
    def test_all_scenarios_correct_and_schema_valid(self):
        results = eval_harness.run_offline_eval()
        assert len(results) == len(eval_harness.ALL_SCENARIOS)
        for r in results:
            assert validate_result_schema(r)
            assert r["agent_mode"] == "deterministic"
            assert r["llm_available"] is False
            assert r["stats"]["final_state_correct"] == 1.0

    def test_blood_type_user_input_wins(self):
        results = eval_harness.run_offline_eval()
        r = next(x for x in results if x["scenario"] == "blood_type_conflict")
        assert r["final_memory"]["patient.blood_type"] == "O+"
        assert r["stats"]["conflict_resolved"] >= 1
        # The attested user_input write must have won (directly or via conflict).
        assert any(
            w["agent_id"] == "patient_user"
            and w["status"] in ("committed", "conflict_resolved")
            for w in r["write_log"]
        )

    def test_mandela_attacker_loses_verified_document(self):
        results = eval_harness.run_offline_eval()
        r = next(x for x in results if x["scenario"] == "mandela_injection")
        assert r["final_memory"]["quote.origin"] == "No, I am your father"
        assert r["stats"]["attacker_conflict_loss_rate"] == 1.0
        assert r["stats"]["verification_agent_win_rate"] == 1.0

    def test_two_frameworks_signed_tool_output_wins(self):
        results = eval_harness.run_offline_eval()
        r = next(x for x in results if x["scenario"] == "two_frameworks")
        assert r["final_memory"]["market.rate"] == "4.25"
        assert r["stats"]["attacker_conflict_loss_rate"] == 1.0

    def test_offline_is_deterministic(self):
        a = eval_harness.run_offline_eval()
        b = eval_harness.run_offline_eval()
        for ra, rb in zip(a, b):
            assert [w["status"] for w in ra["write_log"]] == [w["status"] for w in rb["write_log"]]


class TestRealLlmMode:
    def test_skips_when_servers_down(self, monkeypatch):
        monkeypatch.setattr(eval_harness, "_server_available", lambda url: False)
        result = eval_harness.run_real_llm_eval()
        assert result["skipped"] is True
        assert result["agent_mode"] == "real_llm"
        assert result["llm_available"] is False
        assert "blocked" in result["skip_reason"]

    def test_never_fabricates_live_run_even_when_up(self, monkeypatch):
        monkeypatch.setattr(eval_harness, "_server_available", lambda url: True)
        result = eval_harness.run_real_llm_eval()
        assert result["skipped"] is True
        assert result["llm_available"] is True
        # Even with servers up, the harness does not invent live agent output.
        assert result["trials"] == 0
        assert result["stats"]["total_writes"] == 0
        assert "delegated" in result["skip_reason"]


class TestScenarioDefinitions:
    def test_scenarios_are_named_and_ground_truth_declared(self):
        for sc in eval_harness.ALL_SCENARIOS:
            assert sc.name
            assert sc.steps
            assert sc.ground_truth
