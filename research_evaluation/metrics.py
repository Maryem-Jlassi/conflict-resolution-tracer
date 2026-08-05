from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Any, Callable, Optional


@dataclass
class MetricResult:
    numerator: float
    denominator: int
    estimate: Optional[float]
    confidence_interval: Optional[tuple[float, float]]
    independent_cases: int
    repeated_observations: int
    exclusion_count: int
    exclusion_reasons: dict[str, int]


def _result(num, den, independent, repeats=0, exclusions=None, ci=None):
    if ci is None and den:
        p=num/den; z=1.96; scale=1+z*z/den
        center=(p+z*z/(2*den))/scale
        margin=z*math.sqrt(p*(1-p)/den+z*z/(4*den*den))/scale
        ci=(max(0.0,center-margin),min(1.0,center+margin))
    return MetricResult(num, den, num / den if den else None, ci, independent, repeats,
                        sum((exclusions or {}).values()), exclusions or {})


def require_accuracy_eligible(manifest: dict[str, Any], artifact_hash_valid: bool = True):
    reasons = []
    gt = manifest.get("ground_truth", {})
    if not gt.get("available"): reasons.append("ground_truth_missing")
    if not gt.get("independently_adjudicated"): reasons.append("labels_not_independently_adjudicated")
    if manifest.get("research_classification") == "diagnostic": reasons.append("diagnostic_dataset")
    if not manifest.get("dataset", {}).get("split"): reasons.append("split_unknown")
    if not artifact_hash_valid: reasons.append("artifact_hash_failed")
    if reasons: raise ValueError("accuracy blocked: " + ", ".join(reasons))


def evaluate_predictions(rows: list[dict[str, Any]], manifest: dict[str, Any], artifact_hash_valid=True):
    require_accuracy_eligible(manifest, artifact_hash_valid)
    grouped, exclusions = defaultdict(list), Counter()
    for row in rows:
        if not row.get("independent_unit_id"): exclusions["missing_independent_unit_id"] += 1
        else: grouped[row["independent_unit_id"]].append(row)
    canonical = [values[0] for values in grouped.values()]
    repeats = sum(max(0, len(values) - 1) for values in grouped.values())
    resolved = [r for r in canonical if r["prediction"] != "unresolved"]
    correct = [r for r in canonical if r["prediction"] == r["label"]]
    incorrect_overwrite = [r for r in canonical if r["prediction"] == "incoming" and r["label"] != "incoming"]
    false_resolution = [r for r in resolved if r["label"] == "unresolved"]
    false_abstention = [r for r in canonical if r["prediction"] == "unresolved" and r["label"] != "unresolved"]
    tp = sum(r["prediction"] == r["label"] == "unresolved" for r in canonical)
    predicted_u = sum(r["prediction"] == "unresolved" for r in canonical)
    actual_u = sum(r["label"] == "unresolved" for r in canonical)
    precision = _result(tp, predicted_u, len(canonical), repeats, exclusions)
    recall = _result(tp, actual_u, len(canonical), repeats, exclusions)
    p, q = precision.estimate or 0, recall.estimate or 0
    f1 = MetricResult(2*p*q, 1, 2*p*q/(p+q) if p+q else None, None, len(canonical), repeats,
                      sum(exclusions.values()), dict(exclusions))
    return {
        "strict_accuracy": asdict(_result(len(correct), len(canonical), len(canonical), repeats, exclusions)),
        "coverage": asdict(_result(len(resolved), len(canonical), len(canonical), repeats, exclusions)),
        "selective_accuracy": asdict(_result(sum(r["prediction"] == r["label"] for r in resolved), len(resolved), len(canonical), repeats, exclusions)),
        "selective_risk": asdict(_result(sum(r["prediction"] != r["label"] for r in resolved), len(resolved), len(canonical), repeats, exclusions)),
        "incorrect_overwrite_rate": asdict(_result(len(incorrect_overwrite), len(canonical), len(canonical), repeats, exclusions)),
        "false_resolution_rate": asdict(_result(len(false_resolution), actual_u, len(canonical), repeats, exclusions)),
        "false_abstention_rate": asdict(_result(len(false_abstention), len(canonical)-actual_u, len(canonical), repeats, exclusions)),
        "unresolved_precision": asdict(precision), "unresolved_recall": asdict(recall), "unresolved_f1": asdict(f1),
    }


def risk_coverage(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    ordered = sorted(rows, key=lambda row: row["confidence"], reverse=True)
    n, wrong, curve = len(ordered), 0, []
    for index, row in enumerate(ordered, 1):
        wrong += row["prediction"] != row["label"]
        curve.append({"coverage": index/n, "risk": wrong/index, "threshold": row["confidence"]})
    return curve


def aurc(curve: list[dict[str, float]]) -> float:
    if not curve: raise ValueError("non-empty curve required")
    area, last_c, last_r = 0.0, 0.0, 0.0
    for point in curve:
        area += (point["coverage"]-last_c)*(point["risk"]+last_r)/2
        last_c, last_r = point["coverage"], point["risk"]
    return area


def calibration(probabilities: list[float], outcomes: list[int], bins=10):
    if len(probabilities) != len(outcomes) or not probabilities: raise ValueError("aligned non-empty vectors required")
    eps = 1e-15; n = len(outcomes)
    brier = sum((p-y)**2 for p,y in zip(probabilities,outcomes))/n
    logloss = -sum(y*math.log(max(eps,p))+(1-y)*math.log(max(eps,1-p)) for p,y in zip(probabilities,outcomes))/n
    data=[]; ece=0.0
    for i in range(bins):
        members=[(p,y) for p,y in zip(probabilities,outcomes) if i/bins <= p < (i+1)/bins or (i==bins-1 and p==1)]
        if members:
            conf=sum(p for p,_ in members)/len(members); acc=sum(y for _,y in members)/len(members)
            ece += len(members)/n*abs(acc-conf)
            data.append({"bin":i,"count":len(members),"mean_confidence":conf,"accuracy":acc})
    def record(numerator, estimate):
        return {"numerator":numerator,"denominator":n,"estimate":estimate,
                "confidence_interval":None,"independent_cases":n,
                "repeated_observations":0,"exclusion_count":0,"exclusion_reasons":{}}
    return {"brier_score":record(brier*n,brier),"ece":record(ece*n,ece),
            "log_loss":record(logloss*n,logloss),
            "reliability_diagram":{"numerator":sum(x["count"] for x in data),
                "denominator":n,"estimate":data,"confidence_interval":None,
                "independent_cases":n,"repeated_observations":0,
                "exclusion_count":0,"exclusion_reasons":{}}}
