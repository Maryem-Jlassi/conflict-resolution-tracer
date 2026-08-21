"""Clean deterministic MSM V1 DEV evaluation (s20260321, DEV split only).

Pipeline
--------
1. Deterministic structural readouts for every (DEV persona, question, source)
   via `research_evaluation.msm_deriver` (no event_table, no
   generation_metadata.source_knobs, no extracted_atoms, no LLM, no gold).
2. Per-claim components:
     C = evidence authority(source) * coverage(fraction of window with data)
     R = V1 recency against REF_TIME (30-day half-life exponential decay)
     T = frozen TRAIN-only causal trust (build_trust.py), raw_trust_score
3. 9 policy arms consume the identical (R, C, T) tuples:
     full_lcm (1/3,1/3,1/3), c_only, r_only, t_only, fixed_neutral_trust,
     full_minus_recency, full_minus_confidence, full_minus_trust, last_write_wins.
   Resolution: winner = argmax(w.R + w.C + w.T); unresolved if top-2 margin
   < THETA (0.05). last_write_wins selects the most recent claim and never
   abstains.
4. DEV ground truth is touched ONLY in scoring (never in derivation/trust).

Metrics are defined inline. All denominators explicit.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research_evaluation.msm_deriver import (
    DATA, SOURCES, QIDS, EVIDENCE_AUTHORITY, derive_all, load_persona,
)

SEED = "s20260321"
SCRIPT = Path(__file__).resolve().parent
OUT = SCRIPT / "dev_clean"
OUT.mkdir(parents=True, exist_ok=True)

REF_TIME = datetime(2026, 1, 30, 23, 59, 59)
THETA = 0.05
HALF_LIFE_DAYS = 30.0

POLICY_WEIGHTS = {
    "full_lcm":              {"w_r": 1 / 3, "w_c": 1 / 3, "w_t": 1 / 3},
    "c_only":                {"w_r": 0.0,   "w_c": 1.0,   "w_t": 0.0},
    "r_only":                {"w_r": 1.0,   "w_c": 0.0,   "w_t": 0.0},
    "t_only":                {"w_r": 0.0,   "w_c": 0.0,   "w_t": 1.0},
    "fixed_neutral_trust":   {"w_r": 1 / 3, "w_c": 1 / 3, "w_t": 1 / 3},
    "full_minus_recency":    {"w_r": 0.0,   "w_c": 1 / 2, "w_t": 1 / 2},
    "full_minus_confidence": {"w_r": 1 / 2, "w_c": 0.0,   "w_t": 1 / 2},
    "full_minus_trust":      {"w_r": 1 / 2, "w_c": 1 / 2, "w_t": 0.0},
}
ARM_ORDER = ["full_lcm", "c_only", "r_only", "t_only", "last_write_wins",
             "fixed_neutral_trust", "full_minus_recency",
             "full_minus_confidence", "full_minus_trust"]

SPLITS = json.load(open(DATA / "seeds" / SEED / "config" / "persona_splits.json", encoding="utf-8"))["mapping"]


def load_trust() -> dict:
    with open(OUT / "TRUST_TABLE.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v["raw_trust_score"] for k, v in data["table"].items()}


def recency(obs: datetime) -> float:
    age_days = max(0.0, (REF_TIME - obs).total_seconds() / 86400.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def build_claims(personas: list[str], trust) -> list[dict]:
    claims = []
    for pid in personas:
        b = load_persona(pid, SEED)
        for c in derive_all(b):
            claims.append({
                "persona": c.persona, "qid": c.qid, "source": c.source,
                "value": c.value,
                "obs_time": c.obs_time,
                "C": EVIDENCE_AUTHORITY[c.source] * c.coverage,
                "R": recency(c.obs_time),
                "T": trust.get(f"{c.source}:{c.qid}", 0.5),
            })
    return claims


def episodes(claims: list[dict], all_personas: list[str]) -> list[dict]:
    grouped = defaultdict(list)
    for cl in claims:
        grouped[(cl["persona"], cl["qid"])].append(cl)
    eps = []
    for pid in all_personas:
        for qid in QIDS:
            cls = grouped.get((pid, qid), [])
            cls.sort(key=lambda x: (x["obs_time"], SOURCES.index(x["source"])))
            eps.append({"persona": pid, "qid": qid, "claims": cls})
    eps.sort(key=lambda x: (x["persona"], x["qid"]))
    return eps


def decide(ep, arm):
    claims = ep["claims"]
    if not claims:
        return {"winner": None, "unresolved": True, "margin": None,
                "n_claims": 0, "n_sources": 0, "winner_source": None}
    if arm == "last_write_wins":
        w = claims[-1]
        return {"winner": w, "unresolved": False, "margin": None,
                "n_claims": len(claims), "n_sources": len({c["source"] for c in claims}),
                "winner_source": w["source"]}
    w = POLICY_WEIGHTS[arm]
    scored = [
        {"claim": c, "idx": i,
         "psi": w["w_r"] * c["R"] + w["w_c"] * c["C"]
         + w["w_t"] * (0.5 if arm == "fixed_neutral_trust" else c["T"])}
        for i, c in enumerate(claims)
    ]
    scored.sort(key=lambda s: (s["psi"], -s["idx"]), reverse=True)
    margin = scored[0]["psi"] - scored[1]["psi"] if len(scored) >= 2 else None
    unresolved = (len(claims) >= 2) and (margin is not None and margin < THETA)
    w0 = scored[0]["claim"]
    return {"winner": w0, "unresolved": unresolved, "margin": margin,
            "n_claims": len(claims), "n_sources": len({c["source"] for c in claims}),
            "winner_source": w0["source"]}


def score_episodes(eps, gold_map, arm):
    rows = []
    for ep in eps:
        gold = (gold_map.get((ep["persona"], ep["qid"])) or {}).get("answer")
        d = decide(ep, arm)
        final_value = d["winner"]["value"] if d["winner"] else None
        correct = (gold is not None and final_value is not None and final_value == gold)
        gold_in_claims = any(c["value"] == gold for c in ep["claims"]) if gold is not None else False
        rows.append({
            "persona": ep["persona"], "qid": ep["qid"],
            "final_value": final_value, "gold": gold,
            "correct": correct, "unresolved": d["unresolved"],
            "n_claims": d["n_claims"], "n_sources": d["n_sources"],
            "winner_source": d["winner_source"], "margin": d["margin"],
            "gold_in_claims": gold_in_claims,
        })
    return rows


def arm_metrics(rows):
    total = len(rows)
    resolved = sum(1 for r in rows if not r["unresolved"])
    unresolved = total - resolved
    claimed = sum(1 for r in rows if r["n_claims"] > 0)
    n_correct = sum(1 for r in rows if r["correct"])
    n_correct_resolved = sum(1 for r in rows if r["correct"] and not r["unresolved"])
    n_claimed_units = claimed
    identifiable = sum(1 for r in rows if r["gold_in_claims"])
    # incorrect_overwrite: gold was in the claim set but the winner is wrong.
    n_overwrite = sum(1 for r in rows
                      if r["gold_in_claims"] and not r["unresolved"] and not r["correct"])
    return {
        "n_units": total,
        "n_units_with_claims": claimed,
        "n_units_no_claims": total - claimed,
        "n_identifiable_units": identifiable,
        "n_resolved": resolved,
        "n_unresolved": unresolved,
        "resolution_coverage": round(resolved / total, 4) if total else 0.0,
        "abstention_rate": round(unresolved / total, 4) if total else 0.0,
        "strict_accuracy": round(n_correct / total, 4) if total else 0.0,
        "strict_accuracy_fraction": f"{n_correct}/{total}",
        "selective_accuracy": round(n_correct_resolved / resolved, 4) if resolved else 0.0,
        "selective_accuracy_fraction": f"{n_correct_resolved}/{resolved}",
        "incorrect_overwrite": n_overwrite,
        "n_correct_unresolved": sum(1 for r in rows if r["correct"] and r["unresolved"]),
    }


def identifiability(eps, rows_by_persona_qid, gold_map):
    """For each resolving component, how often its max picks a gold-correct value."""
    comp = {"R": [], "C": [], "T": []}
    for ep in eps:
        gold = (gold_map.get((ep["persona"], ep["qid"])) or {}).get("answer")
        if gold is None:
            continue
        claims = ep["claims"]
        if not claims:
            continue
        for name, key in (("R", "R"), ("C", "C"), ("T", "T")):
            best = max(claims, key=lambda c: (c[key], claims.index(c)))
            comp[name].append({"episode": f"{ep['persona']}/{ep['qid']}",
                               "gold": gold, "winner": best["value"],
                               "winner_source": best["source"],
                               "correct": best["value"] == gold})
    out = {}
    for name, lst in comp.items():
        n = len(lst)
        corr = sum(1 for x in lst if x["correct"])
        out[name] = {
            "n_episodes_with_claims": n,
            "component_identifiability": round(corr / n, 4) if n else None,
            "fraction": f"{corr}/{n}",
        }
    return out


def paired(full_rows, base_rows):
    res = {}
    for a, b in zip(full_rows, base_rows):
        key = (a["correct"], b["correct"])
        res[key] = res.get(key, 0) + 1
    both_c = res.get((True, True), 0)
    a_only = res.get((True, False), 0)
    b_only = res.get((False, True), 0)
    both_w = res.get((False, False), 0)
    discordant = a_only + b_only
    return {
        "n_units_compared": len(full_rows),
        "both_correct": both_c,
        "full_lcm_correct_only": a_only,
        "baseline_correct_only": b_only,
        "both_wrong": both_w,
        "discordant_pairs": discordant,
        "full_lcm_win_rate_excluding_ties": round(a_only / discordant, 4) if discordant else None,
    }


def main():
    trust_table = load_trust()
    dev_personas = [pid for pid, split in SPLITS.items() if split == "dev"]
    dev_claims = build_claims(dev_personas, trust_table)
    eps = episodes(dev_claims, dev_personas)

    gold_map = {}
    for pid in dev_personas:
        gt = json.load(open(DATA / "seeds" / SEED / pid / "ground_truth.json", encoding="utf-8"))
        for qid, rec in gt.items():
            gold_map[(pid, qid)] = rec

    results = {}
    rows_by_arm = {}
    for arm in ARM_ORDER:
        rows = score_episodes(eps, gold_map, arm)
        rows_by_arm[arm] = rows
        results[arm] = arm_metrics(rows)

    # Paired comparisons (full_lcm vs each other arm)
    paired_comparisons = {}
    full_rows = rows_by_arm["full_lcm"]
    for base in ARMS_BASELINES:
        paired_comparisons[f"full_lcm_vs_{base}"] = paired(full_rows, rows_by_arm[base])

    # Identifiability
    ident = identifiability(eps, None, gold_map)

    # Per-question breakdown for full_lcm
    per_qid = {}
    for qid in QIDS:
        qrows = [r for r in full_rows if r["qid"] == qid]
        if not qrows:
            continue
        n = len(qrows)
        res = sum(1 for r in qrows if not r["unresolved"])
        corr = sum(1 for r in qrows if r["correct"])
        per_qid[qid] = {
            "n_units": n,
            "n_resolved": res,
            "coverage": round(res / n, 3),
            "strict_accuracy": round(corr / n, 3),
            "fraction": f"{corr}/{n}",
        }

    artifact = {
        "experiment_id": "MSM_V1_DEV_CLEAN",
        "execution_type": "REAL_DATA_DETERMINISTIC",
        "config": SEED,
        "split": "dev",
        "n_personas": len(dev_personas),
        "n_question_units": 18 * len(dev_personas),
        "n_units_with_claims": sum(1 for e in eps if e["claims"]),
        "psi_formula": "(R + C + T) / 3",
        "theta": THETA,
        "recency_half_life_days": HALF_LIFE_DAYS,
        "trust_identity": "source:question_id (TRAIN-only causal, frozen)",
        "evidence_C": "authority(source) * coverage",
        "deriver": "research_evaluation.msm_deriver (deterministic structural readouts)",
        "arms": ARM_ORDER,
        "metrics": results,
        "per_question_metrics": {"full_lcm": per_qid},
        "paired_comparisons": paired_comparisons,
        "component_identifiability": ident,
        "lineage": {
            "deriver_version": "v1-clean-1",
            "trust_table": "TRUST_TABLE.json",
            "train_claims": "TRAIN_DERIVED_ASSERTIONS.json",
            "dev_claims": "DEV_DERIVED_ASSERTIONS.json",
            "gold_usage": "DEV ground truth used ONLY in scoring (Stage C)",
        },
    }
    write = OUT / "MSM_V1_DEV_RESULTS.json"
    with open(write, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, default=str)

    with open(OUT / "DEV_DERIVED_ASSERTIONS.json", "w", encoding="utf-8") as f:
        serializable = []
        for cl in dev_claims:
            c2 = dict(cl)
            c2["obs_time"] = c2["obs_time"].isoformat()
            serializable.append(c2)
        json.dump({"seed": SEED, "split": "dev", "n_claims": len(serializable), "claims": serializable},
                  f, indent=1)

    with open(OUT / "MSM_V1_COMPONENT_IDENTIFIABILITY.json", "w", encoding="utf-8") as f:
        json.dump({
            "experiment_id": "MSM_V1_COMPONENT_IDENTIFIABILITY",
            "config": SEED, "split": "dev",
            "method": "for each claim-bearing episode, argmax of the component selects the claim; component_identifiability = share of selections equal to gold",
            "components": ident,
        }, f, indent=2)

    cmp = {"experiment_id": "MSM_V1_POLICY_COMPARISON", "config": SEED, "split": "dev",
           "method": "McNemar-style paired comparison on discordant pairs, full_lcm vs each baseline",
           "paired": paired_comparisons,
           "metrics": {a: results[a] for a in ARM_ORDER}}
    with open(OUT / "MSM_V1_POLICY_COMPARISON.json", "w", encoding="utf-8") as f:
        json.dump(cmp, f, indent=2, default=str)

    md = ["| Comparison | both_correct | full_lcm only | baseline only | both_wrong | discordant | full_lcm win rate |",
          "|---|---|---|---|---|---|---|"]
    for k, v in paired_comparisons.items():
        md.append(f"| {k} | {v['both_correct']} | {v['full_lcm_correct_only']} | {v['baseline_correct_only']} | "
                  f"{v['both_wrong']} | {v['discordant_pairs']} | {v['full_lcm_win_rate_excluding_ties']} |")
    (OUT / "MSM_V1_POLICY_COMPARISON.md").write_text(
        "# MSM V1 policy comparison (DEV, clean deterministic)\n\n" + "\n".join(md) + "\n", encoding="utf-8")

    print(f"DEV personas: {len(dev_personas)}  units: {18 * len(dev_personas)}  claims: {len(dev_claims)}")
    print(f"{'arm':<24}{'cov':>6}{'strict':>8}{'selective':>10}{'overwrite':>10}")
    for a in ARM_ORDER:
        m = results[a]
        print(f"{a:<24}{m['resolution_coverage']:>6.3f}{m['strict_accuracy']:>8.3f}"
              f"{m['selective_accuracy']:>10.3f}{m['incorrect_overwrite']:>10}")
    print("\ncomponent identifiability:")
    for k, v in ident.items():
        print(f"  {k}: {v['fraction']}")
    print("\npaired full_lcm wins (discordant):")
    for k, v in paired_comparisons.items():
        print(f"  {k}: a_win={v['full_lcm_correct_only']} b_win={v['baseline_correct_only']} rate={v['full_lcm_win_rate_excluding_ties']}")
    print("\nDONE ->", write)


ARMS_BASELINES = [a for a in ARM_ORDER if a != "full_lcm"]

if __name__ == "__main__":
    main()