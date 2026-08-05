import pytest
from research_evaluation.policies import (DEPLOYABLE_POLICIES, ORACLE_ANALYSIS_ONLY,
    PolicyInput, get_deployable_policy, replay)

def sample():
    return PolicyInput("c1",{"source_id":"a","asserted_at":"2026-01-01","lcm_score":.2},
        {"source_id":"b","asserted_at":"2026-01-02","lcm_score":.8},(),("t",),
        ({"source_id":"a","trust":.4},{"source_id":"b","trust":.8}),{"domain":"d"},0)

def test_every_policy_receives_identical_input():
    output=replay([sample()],list(DEPLOYABLE_POLICIES))
    assert len(output["rows"]) == len(DEPLOYABLE_POLICIES)
    assert len({row["input_fingerprint"] for row in output["rows"]}) == 1

def test_one_row_per_case_policy():
    cases=[sample(),PolicyInput(**{**sample().__dict__,"case_id":"c2","case_order":1})]
    assert replay(cases,["keep_incumbent","always_abstain"])["row_count"] == 4

def test_oracle_is_not_deployable_or_registered():
    assert "oracle_analysis_only" not in DEPLOYABLE_POLICIES and not ORACLE_ANALYSIS_ONLY.deployable
    with pytest.raises(PermissionError): get_deployable_policy("oracle_analysis_only")

def test_policy_resolution_has_no_ground_truth_field():
    assert "ground_truth" not in sample().__dict__
