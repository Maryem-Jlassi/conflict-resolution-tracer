"""
Unit & Integration tests for experiments/dynamic_multi_agent_scenarios.py
"""

import pytest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from experiments.dynamic_multi_agent_scenarios import (
    ALL_SCENARIOS,
    write_memory,
    read_memory,
    _reset_logs,
    _write_log,
    _conflict_log,
)


def test_scenario_configs_structure():
    """Verify that all current dynamic scenarios have required keys, agent lists, and shared paths."""
    scenarios = list(ALL_SCENARIOS.values())
    for sc in scenarios:
        assert "name" in sc
        assert "domain" in sc
        assert "shared_paths" in sc and len(sc["shared_paths"]) >= 2
        assert "oracle_rules" not in sc
        assert "agents" in sc and len(sc["agents"]) >= 2

        for ag in sc["agents"]:
            assert "id" in ag
            assert "prompt" in ag
            assert "input" in ag
            assert ag["id"] in ag["prompt"]
            assert "fallback_writes" not in ag, f"Agent '{ag['id']}' contains fallback_writes dictionary!"

    # Scenarios must not define a hard-coded ground-truth oracle
    for sc in scenarios:
        assert "oracle_rules" not in sc


def test_scenario_prompts_no_hardcoded_winning_strings():
    """Assert system prompts contain role + goal + paths instructions without literal winning template strings."""
    scenarios = list(ALL_SCENARIOS.values())
    forbidden_templates = [
        "Write 'treatment.safety' = 'UNSAFE: High risk",
        "Write 'research.key_finding' = 'Accuracy drops to 62%",
        "Write 'incident.root_cause' = 'Unindexed JOIN query",
        "fallback_writes",
    ]
    for sc in scenarios:
        for ag in sc["agents"]:
            prompt = ag["prompt"]
            for forbidden in forbidden_templates:
                assert forbidden not in prompt, f"Prompt for '{ag['id']}' contains hardcoded winning template string or fallback injection!"


@patch("experiments.dynamic_multi_agent_scenarios._lcm")
def test_write_memory_tool_with_evidence(mock_lcm):
    """Test write_memory tool behavior with default evidence fallback and conflict breakdown capture."""
    _reset_logs()

    # Mock normal committed write with default evidence fallback
    mock_lcm.write.return_value = {
        "status": "committed",
        "provenance_id": "prov_12345",
        "message": "Committed assertion successfully",
    }
    res = write_memory.invoke({
        "agent_id": "pharmacologist_critic",
        "path": "treatment.safety",
        "value": "Renal risk detected",
        "confidence": 0.93,
    })
    assert "Written to 'treatment.safety'" in res
    assert len(_write_log) == 1
    assert _write_log[0]["agent_id"] == "pharmacologist_critic"
    assert _write_log[0]["evidence_type"] == "agent_claim_default"

    # Verify LCMClient.write was called without evidence_records (middleware uses default fallback)
    call_kwargs = mock_lcm.write.call_args.kwargs
    assert call_kwargs["agent_id"] == "pharmacologist_critic"
    assert call_kwargs["confidence_score"] == 0.93
    assert call_kwargs["assertion_payload"] == {"treatment.safety": "Renal risk detected"}
    assert "evidence_records" not in call_kwargs  # Middleware uses default fallback

    # Mock conflict resolution write
    mock_lcm.write.return_value = {
        "status": "conflict_resolved",
        "winner_agent": "pharmacologist_critic",
        "loser_agent": "clinical_analyst",
        "message": "Resolved via higher verified confidence (Psi = 0.88 vs 0.72)",
        "unresolved": False,
        "psi_winner_breakdown": {"psi": 0.88},
        "psi_loser_breakdown": {"psi": 0.72},
    }
    res2 = write_memory.invoke({
        "agent_id": "pharmacologist_critic",
        "path": "treatment.safety",
        "value": "Renal risk detected",
        "confidence": 0.93,
    })
    assert "Conflict resolved at 'treatment.safety'" in res2
    assert len(_conflict_log) == 1
    assert _conflict_log[0]["winner"] == "pharmacologist_critic"
    assert _conflict_log[0]["loser"] == "clinical_analyst"
    assert _conflict_log[0]["psi_winner_breakdown"] == {"psi": 0.88}
    assert _conflict_log[0]["psi_loser_breakdown"] == {"psi": 0.72}


@patch("experiments.dynamic_multi_agent_scenarios._lcm")
def test_read_memory_tool(mock_lcm):
    """Test read_memory tool behavior on existing memory and empty memory."""
    mock_lcm.get_context.return_value = {
        "facts": [
            {
                "agent_id": "clinical_analyst",
                "confidence_score": 0.82,
                "assertion_payload": {"diagnosis.primary": "Hypertension"},
            }
        ]
    }
    res = read_memory.invoke({"path": "diagnosis.primary"})
    assert "'diagnosis.primary' = 'Hypertension'" in res
    assert "agent=clinical_analyst" in res

    mock_lcm.get_context.return_value = {"facts": []}
    res_empty = read_memory.invoke({"path": "empty.path"})
    assert "No memory committed at 'empty.path' yet." in res_empty


@pytest.mark.skip(reason="build_full_pdf_report module not present in this environment")
def test_build_full_pdf_and_latex_report():
    """Execute build_full_pdf_report and verify files exist."""
    import build_full_pdf_report
    assert build_full_pdf_report.TEX_FILE.exists()
    assert build_full_pdf_report.PDF_FILE.exists()
    assert build_full_pdf_report.PDF_FILE.stat().st_size > 0
