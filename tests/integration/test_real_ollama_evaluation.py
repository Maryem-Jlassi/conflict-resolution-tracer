"""Opt-in empirical integration test; never substitutes a fake agent."""

import os

import pytest

from tools import verify_release


@pytest.mark.real_ollama
def test_real_ollama_agents_commit_structured_tool_call(tmp_path):
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    available, reason = verify_release._probe_ollama(ollama_url, "llama3.1:8b")
    if not available:
        pytest.skip(reason)
    checks = verify_release.run_real_agent_evaluation(
        run_real_agent=True,
        real_agent_model="llama3.1:8b",
        real_agent_scenarios="basic",
        real_agent_trials=1,
        real_agent_timeout=600,
        real_agent_artifact_dir=str(tmp_path),
        ollama_url=ollama_url,
    )
    blockers = [c.skip_reason for c in checks if c.blocked]
    assert not blockers, blockers
    failures = [c.detail for c in checks if not c.passed and not c.skipped]
    assert not failures, failures
