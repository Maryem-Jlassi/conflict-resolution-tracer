import pytest
from research_evaluation.metrics import aurc, calibration, evaluate_predictions, require_accuracy_eligible, risk_coverage
from research_evaluation.statistics import clustered_bootstrap, effect_sizes, holm, mcnemar, paired_permutation, wilcoxon_signed_rank

MANIFEST={"ground_truth":{"available":True,"independently_adjudicated":True},"research_classification":"research","dataset":{"split":"pilot"}}

def rows(): return [
    {"independent_unit_id":"a","prediction":"existing","label":"existing","confidence":.9},
    {"independent_unit_id":"a","prediction":"existing","label":"existing","confidence":.9},
    {"independent_unit_id":"b","prediction":"unresolved","label":"incoming","confidence":.2},
    {"independent_unit_id":"c","prediction":"incoming","label":"unresolved","confidence":.8},]

def test_metric_denominators_and_repeats():
    result=evaluate_predictions(rows(),MANIFEST)
    assert result["strict_accuracy"]["denominator"] == 3
    assert result["strict_accuracy"]["independent_cases"] == 3
    assert result["strict_accuracy"]["repeated_observations"] == 1
    assert result["coverage"]["numerator"] == 2

def test_ground_truth_gating_all_reasons():
    with pytest.raises(ValueError) as exc:
        require_accuracy_eligible({"ground_truth":{},"research_classification":"diagnostic","dataset":{}},False)
    text=str(exc.value)
    for reason in ("ground_truth_missing","labels_not_independently_adjudicated","diagnostic_dataset","split_unknown","artifact_hash_failed"): assert reason in text

def test_risk_coverage_and_aurc_arithmetic():
    curve=risk_coverage([{"prediction":"x","label":"x","confidence":.9},{"prediction":"x","label":"y","confidence":.5}])
    assert curve == [{"coverage":.5,"risk":0,"threshold":.9},{"coverage":1,"risk":.5,"threshold":.5}]
    assert aurc(curve) == pytest.approx(.125)

def test_calibration_outputs():
    value=calibration([.9,.1],[1,0],bins=2)
    assert value["brier_score"]["estimate"] == pytest.approx(.01)
    assert value["ece"]["estimate"] == pytest.approx(.1)
    assert value["log_loss"]["estimate"] > 0
    assert len(value["reliability_diagram"]["estimate"]) == 2

def test_statistical_procedures():
    assert mcnemar([1,1,0],[1,0,0],[1,1,1])["independent_cases"] == 3
    assert 0 <= paired_permutation([1,2,-1],100,1)["p_value"] <= 1
    assert 0 <= wilcoxon_signed_rank([1,2,-1])["p_value"] <= 1
    boot=clustered_bootstrap([{"c":"a","v":1},{"c":"a","v":1},{"c":"b","v":0}],"c","v",100,1)
    assert boot["independent_cases"] == 2 and boot["repeated_observations"] == 1
    assert effect_sizes([2,3],[1,1])["mean_difference"] == 1.5
    adjusted=holm([.01,.04,.03]); assert all(a>=p for a,p in zip(adjusted,[.01,.04,.03]))
