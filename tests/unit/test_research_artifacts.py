import json

import pytest

from lcm_core.research_artifacts import (
    adapt_historical, build_artifact, canonical_sha256, validate_artifact,
    write_immutable,
)


def _manifest(**updates):
    value = {
        "state": "completed", "research_classification": "research", "row_count": 1,
        "dataset": {"split": "held_out"},
        "ground_truth": {"available": True, "independently_adjudicated": True},
        "source_provenance": {"git_commit": "a" * 40, "worktree_dirty": True,
                              "source_manifest_sha256": "b" * 64},
    }
    value.update(updates)
    return value


def test_canonical_hash_ignores_mapping_order():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_valid_headline_artifact():
    artifact = build_artifact(_manifest(), [{"independent_unit_id": "u1", "correct": True}])
    assert artifact["manifest"]["headline_eligible"]
    assert validate_artifact(artifact) == []


@pytest.mark.parametrize("change,reason", [
    ({"ground_truth": {"available": False, "independently_adjudicated": False}}, "ground_truth_absent"),
    ({"dataset": {"split": None}}, "dataset_split_unknown"),
    ({"research_classification": "synthetic"}, "diagnostic_or_synthetic"),
    ({"real_agent_execution": True}, "real_agent_without_model_call_evidence"),
])
def test_ineligible_reasons(change, reason):
    artifact = build_artifact(_manifest(**change), [{"independent_unit_id": "u1"}])
    assert not artifact["manifest"]["headline_eligible"]
    assert reason in artifact["manifest"]["headline_exclusion_reasons"]


def test_hash_and_row_count_tampering_rejected():
    artifact = build_artifact(_manifest(), [{"independent_unit_id": "u1"}])
    artifact["rows"].append({"independent_unit_id": "u2"})
    assert "artifact_hash_failed" in validate_artifact(artifact)


def test_atomic_no_overwrite(tmp_path):
    artifact = build_artifact(_manifest(), [{"independent_unit_id": "u1"}])
    path = write_immutable(tmp_path / "run.json", artifact)
    assert json.loads(path.read_text("utf-8"))["artifact_sha256"] == artifact["artifact_sha256"]
    with pytest.raises(FileExistsError):
        write_immutable(path, artifact)


def test_historical_adapter_is_honestly_ineligible():
    artifact = adapt_historical({"accuracy": 1.0}, "old.json")
    assert not artifact["manifest"]["headline_eligible"]
    assert validate_artifact(artifact) == []
