"""Step 3 - policy replay (unchanged mechanics).

Reads frozen multi-provider assertions and replays the SIX QACC policies over
each case's supported claims.  QACC provides no real trust/temporal signal, so:

  * R (recency) = 0.5 neutral for every claim,
  * T (trust)   = 0.5 neutral for every claim,
  * C (confidence) = authority_score from source_type (provider-BLIND).

Only C differentiates between claims; provider identity is never an input to any
score.  The winning source is returned per policy for the cross-model analysis.
"""
from __future__ import annotations

import json
from decimal import Decimal

from . import common

RESOLVE_THRESHOLD = 0.05
NEUTRAL_R = 0.5
NEUTRAL_T = 0.5


def policies():
    return [
        {"name": "full_crt",            "w_r": 1/3, "w_c": 1/3, "w_t": 1/3, "mode": "psi"},
        {"name": "c_only",              "w_r": 0.0, "w_c": 1.0, "w_t": 0.0, "mode": "psi"},
        {"name": "r_only",              "w_r": 1.0, "w_c": 0.0, "w_t": 0.0, "mode": "psi"},
        {"name": "t_only",              "w_r": 0.0, "w_c": 0.0, "w_t": 1.0, "mode": "psi"},
        {"name": "fixed_neutral_trust", "w_r": 1/3, "w_c": 1/3, "w_t": 1/3, "mode": "psi"},
        {"name": "last_write_wins",     "w_r": 0.0, "w_c": 0.0, "w_t": 0.0, "mode": "lww"},
    ]


def _psi(claim, pol, r=NEUTRAL_R, t=NEUTRAL_T):
    """Deterministic weighted total for one claim under one policy."""
    c = Decimal(str(claim["authority_score"]))
    w_r = Decimal(str(pol["w_r"]))
    w_c = Decimal(str(pol["w_c"]))
    w_t = Decimal(str(pol["w_t"]))
    return float(w_r * Decimal(str(r)) + w_c * c + w_t * Decimal(str(t)))


def resolve_case(claims, pol):
    """Return {resolved, winner|None, reason} for one policy over a claim list."""
    if not claims:
        return {"resolved": False, "winner": None, "reason": "no_supported_claim"}

    if pol["mode"] == "lww":
        winner = max(claims, key=lambda cl: cl["source_id"])
        return {"resolved": True, "winner": winner, "reason": "last_write_wins"}

    scored = sorted(claims, key=lambda cl: (-_psi(cl, pol), cl["source_id"]))
    top = scored[0]
    if len(scored) == 1:
        return {"resolved": True, "winner": top, "reason": "single_claim"}
    margin = _psi(scored[0], pol) - _psi(scored[1], pol)
    if margin < RESOLVE_THRESHOLD:
        return {"resolved": False, "winner": None,
                "reason": "below_threshold margin=%.4f" % margin}
    return {"resolved": True, "winner": top, "reason": "psi_top"}
def load_assertions(path):
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def group_claims_by_case(records):
    """{case_id: [claim,...]} over successful+supported extractions."""
    by_case = {}
    for r in records:
        if not r["success"] or r.get("support_status") != "supported":
            continue
        cand = r.get("answer_candidate")
        if not cand:
            continue
        cid = int(r["case_id"])
        by_case.setdefault(cid, []).append({
            "source_id": r["source_id"],
            "provider": r["provider"],
            "answer_candidate": cand,
            "authority_score": r.get("authority_score", 0.0),
            "source_type": r.get("source_type", "document"),
            "case_id": cid,
        })
    return by_case


def run(assertions_path, case_ids, gold_by_case=None):
    """Replay all six policies over the selected cases.

    ``case_ids`` = the deterministic 500 (or smoke) selection so that zero-claim
    cases count as unresolved in the denominators.
    """
    if gold_by_case is None:
        all_cases = {int(c["annotation_task_id"]): c for c in common.load_dataset()}
        gold_by_case = {cid: common.qacc_gold(all_cases.get(cid, {})) for cid in case_ids}

    records = load_assertions(assertions_path)
    by_case = group_claims_by_case(records)

    total = len(case_ids)
    results = {p["name"]: {
        "resolved": 0, "correct_resolved": 0, "correct_strict": 0,
        "overwrite": 0, "winners_by_provider": {},
    } for p in policies()}

    per_case_winners = {}
    for cid in case_ids:
        per_case_winners[cid] = {}
        claims = by_case.get(cid, [])
        gold = gold_by_case.get(cid, [])
        has_correct = any(common.answer_correct(cl["answer_candidate"], gold) for cl in claims)
        for pol in policies():
            name = pol["name"]
            out = resolve_case(claims, pol)
            if not out["resolved"]:
                per_case_winners[cid][name] = None
                continue
            rres = results[name]
            rres["resolved"] += 1
            w = out["winner"]
            win_correct = common.answer_correct(w["answer_candidate"], gold)
            if win_correct:
                rres["correct_resolved"] += 1
                rres["correct_strict"] += 1
            elif has_correct:
                rres["overwrite"] += 1
            rres["winners_by_provider"][w["provider"]] = \
                rres["winners_by_provider"].get(w["provider"], 0) + 1
            per_case_winners[cid][name] = {
                "provider": w["provider"],
                "source_id": w["source_id"],
                "answer_candidate": w["answer_candidate"],
                "correct": win_correct,
            }

    summary = {}
    for p in policies():
        name = p["name"]
        r = results[name]
        summary[name] = {
            "total": total,
            "resolved": r["resolved"],
            "resolution_coverage": round(r["resolved"] / total, 4) if total else 0.0,
            "strict_accuracy": round(r["correct_strict"] / total, 4) if total else 0.0,
            "selective_accuracy": round(
                r["correct_resolved"] / r["resolved"], 4) if r["resolved"] else 0.0,
            "overwrite": r["overwrite"],
            "winners_by_provider": dict(sorted(r["winners_by_provider"].items())),
        }
    return {"summary": summary, "per_case_winners": per_case_winners, "total": total}


def save(assertions_path, case_ids, out_path):
    out = run(assertions_path, case_ids)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out