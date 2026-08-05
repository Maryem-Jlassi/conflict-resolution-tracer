import pytest
from research_evaluation.power import power_analysis

def test_blocked_without_qualifying_pilot():
    result=power_analysis(None)
    assert result["state"] == "blocked" and result["message"].startswith("BLOCKED")

def test_does_not_invent_missing_values():
    with pytest.raises(ValueError): power_analysis({"independently_annotated":True})

def test_qualified_pilot_interface():
    result=power_analysis({"independently_annotated":True,"baseline_error":.3,"lcm_error":.2},intracluster_correlation=.1,mean_cluster_size=2)
    assert result["state"] == "completed" and result["absolute_effect"] == pytest.approx(.1)
    assert result["estimated_required_independent_cases"] > 0 and len(result["sensitivity"]) == 5
