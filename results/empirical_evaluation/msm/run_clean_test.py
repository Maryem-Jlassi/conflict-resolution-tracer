"""FINAL OUT-OF-SAMPLE MSM V1 TEST evaluation.

Reuses the exact frozen DEV methodology by importing the identical
implementation functions from run_clean_dev.py and the identical frozen
TRAIN-only trust table. Population = official MSM TEST split (120 personas,
18 x 120 = 2160 grid units) per persona_splits.json, as confirmed by the user.

No methodology, deriver, thresholds, trust, policy, or metric is changed or
retuned. TEST ground truth is consumed only during final scoring (Stage C).
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from results.empirical_evaluation.msm.run_clean_dev import (
    SCRIPT, OUT, DATA, SPLITS, SOURCES, QIDS, ARM_ORDER, ARMS_BASELINES,
    REF_TIME, THETA, POLICY_WEIGHTS, load_trust, build_claims, episodes,
    score_episodes, arm_metrics, paired, identifiability,
)

SEED = "s20260321"
TEST_OUT = Path(__file__).resolve().parent / "test_clean"
TEST_OUT.mkdir(parents=True, exist_ok=True)

DEV_RESULTS = OUT / "MSM_V1_DEV_RESULTS.json"
TRUST_ARTIFACT = OUT / "TRUST_TABLE.json"
TRAIN_CLAIMS_ARTIFACT = OUT / "TRAIN_DERIVED_ASSERTIONS.json"
TEST_CLAIMS_ARTIFACT = TEST_OUT / "TEST_DERIVED_ASSERTIONS.json"
MANIFEST = TEST_OUT / "MANIFEST.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print(f"RUN_FROZEN_METHODOLOGY -> population=official TEST (120 personas, {18 * 120} units)")

    # ---- Step 1: official TEST population from the dataset config ----
    test_personas = sorted(pid for pid, split in SPLITS.items() if split == "test")
    assert len(test_personas) == 120, f"expected 120 test personas, got {len(test_personas)}"
    n_units = 18 * len(test_personas)

    # ---- Step 2: frozen deriver on TEST ----
    test_claims = build_claims(test_personas, load_trust())
    eps = episodes(test_claims, test_personas)
    assert len(eps) == n_units, f"grid mismatch: {len(eps)} != {n_units}"
    n_with = sum(1 for e in eps if e["claims"])

    with open(TEST_CLAIMS_ARTIFACT, "w", encoding="utf-8") as f:
        serial = []
        for cl in test_claims:
            c2 = dict(cl)
            c2["obs_time"] = c2["obs_time"].isoformat()
            serial.append(c2)
        json.dump({"seed": SEED, "split": "test", "n_personas": len(test_personas),
                   "n_claims": len(serial), "claims": serial}, f, indent=1)

    # ---- Step 4/5: gold ONLY for scoring; identical policy implementation ----
    gold_map = {}
    for pid in test_personas:
        gt = json.load(open(DATA / "seeds" / SEED / pid / "ground_truth.json", encoding="utf-8"))
        for qid, rec in gt.items():
            gold_map[(pid, qid)] = rec

    results = {}
    rows_by_arm = {}
    for arm in ARM_ORDER:
        rows = score_episodes(eps, gold_map, arm)
        rows_by_arm[arm] = rows
        results[arm] = arm_metrics(rows)

    # ---- Step 8/9: baseline comparison (identical to DEV: descriptive only) ----
    paired_comparisons = {}
    full_rows = rows_by_arm["full_lcm"]
    for base in [a for a in ARM_ORDER if a != "full_lcm"]:
        paired_comparisons[f"full_lcm_vs_{base}"] = paired(full_rows, rows_by_arm[base])

    # ---- Step 7: component identifiability ----
    ident = identifiability(eps, None, gold_map)

    # ---- per-question full_lcm breakdown ----
    per_qid = {}
    for qid in QIDS:
        qrows = [r for r in full_rows if r["qid"] == qid]
        n = len(qrows)
        res = sum(1 for r in qrows if not r["unresolved"])
        corr = sum(1 for r in qrows if r["correct"])
        per_qid[qid] = {"n_units": n, "n_resolved": res,
                        "coverage": round(res / n, 3),
                        "strict_accuracy": round(corr / n, 3),
                        "fraction": f"{corr}/{n}"}

    results_artifact = {
        "experiment_id": "MSM_V1_FINAL_TEST_CLEAN",
        "execution_type": "REAL_DATA_DETERMINISTIC",
        "config": SEED,
        "split": "test",
        "population": "official test split from config/persona_splits.json (120 personas)",
        "population_deviation_note": ("Task spec asserted 18 x 48 = 864 units; user confirmed using the "
                                      "official 120-persona test split, so the grid is 18 x 120 = 2160 units."),
        "n_personas": len(test_personas),
        "N_questions": 18,
        "N_TEST_units": n_units,
        "N_TEST_units_with_claims": n_with,
        "N_TEST_units_without_claims": n_units - n_with,
        "psi_formula": "(R + C + T) / 3",
        "theta": THETA,
        "recency_half_life_days": 30.0,
        "trust_identity": "source:question_id (TRAIN-only causal, frozen; identical to DEV)",
        "trust_artifact": "dev_clean/TRUST_TABLE.json (frozen, reused as-is)",
        "evidence_C": "authority(source) * coverage (identical to DEV)",
        "deriver": "research_evaluation.msm_deriver.py (frozen v1-clean-1, unchanged)",
        "policy": "frozen DEV policy implementation (run_clean_dev.py, unchanged)",
        "arms": ARM_ORDER,
        "metrics": results,
        "per_question_metrics": {"full_lcm": per_qid},
        "paired_comparisons": paired_comparisons,
        "component_identifiability": ident,
        "statistical_note": ("Frozen methodology defines paired comparisons as descriptive McNemar-style "
                             "binned counts; NO significance test is part of the protocol, therefore no "
                             "significance claims are made."),
    }
    with open(TEST_OUT / "MSM_V1_TEST_RESULTS.json", "w", encoding="utf-8") as f:
        json.dump(results_artifact, f, indent=2, default=str)

    with open(TEST_OUT / "MSM_V1_COMPONENT_IDENTIFIABILITY.json", "w", encoding="utf-8") as f:
        json.dump({"experiment_id": "MSM_V1_COMPONENT_IDENTIFIABILITY_TEST",
                   "config": SEED, "split": "test", "population": "official 120-persona test split",
                   "method": "argmax of the single component among claim-bearing units; identifiability = share equal to gold; identical to DEV",
                   "n_units": n_units, "components": ident}, f, indent=2)

    cmp_artifact = {"experiment_id": "MSM_V1_POLICY_COMPARISON_TEST", "config": SEED, "split": "test",
                    "method": "McNemar-style paired comparison on discordant pairs (full_lcm vs each baseline); descriptive only",
                    "paired": paired_comparisons,
                    "metrics": {a: results[a] for a in ARM_ORDER}}
    with open(TEST_OUT / "MSM_V1_POLICY_COMPARISON.json", "w", encoding="utf-8") as f:
        json.dump(cmp_artifact, f, indent=2, default=str)

    cmp_md = ["| Comparison | both_correct | full_lcm only | baseline only | both_wrong | discordant | full_lcm win rate |",
              "|---|---|---|---|---|---|---|"]
    for k, v in paired_comparisons.items():
        cmp_md.append(f"| {k} | {v['both_correct']} | {v['full_lcm_correct_only']} | {v['baseline_correct_only']} | "
                      f"{v['both_wrong']} | {v['discordant_pairs']} | {v['full_lcm_win_rate_excluding_ties']} |")
    (TEST_OUT / "MSM_V1_POLICY_COMPARISON.md").write_text(
        "# MSM V1 policy comparison (official TEST split, clean deterministic)\n\n"
        + "\n".join(cmp_md) + "\n", encoding="utf-8")

    md_rows = ["| Policy | Coverage | Strict Accuracy | Selective Accuracy | Overwrite | Decisions |",
               "|---|---|---|---|---|---|"]
    for arm in ARM_ORDER:
        m = results[arm]
        md_rows.append(f"| {arm} | {m['resolution_coverage']} | {m['strict_accuracy']} "
                       f"| {m['selective_accuracy']} | {m['incorrect_overwrite']} | {m['n_units']} |")
    (TEST_OUT / "MSM_V1_TEST_RESULTS.md").write_text(
        "# MSM V1 final TEST results (clean deterministic)\n\n"
        f"- N_TEST_units = {n_units}\n- N_TEST_units_with_claims = {n_with}\n"
        f"- N_TEST_units_without_claims = {n_units - n_with}\n\n" + "\n".join(md_rows) + "\n",
        encoding="utf-8")

    # ---- DEV vs TEST comparison (DEV artifacts read, never modified) ----
    dev = json.load(open(DEV_RESULTS, encoding="utf-8"))
    dev_lines = ["# MSM V1 DEV vs TEST comparison (clean deterministic)", "",
                 "Population: DEV = 48 personas (864 units), TEST = 120 personas (2160 units).",
                 "Identical frozen deriver, trust, policy, thresholds, and metrics on both splits.",
                 "| split | policy | coverage | strict | selective | overwrite | resolved/total |",
                 "|---|---|---|---|---|---|---|"]
    for split_name, art in (("dev", dev), ("test", results_artifact)):
        for arm in ARM_ORDER:
            m = art["metrics"][arm]
            dev_lines.append(
                f"| {split_name} | {arm} | {m['resolution_coverage']} | {m['strict_accuracy']} "
                f"| {m['selective_accuracy']} | {m['incorrect_overwrite']} | {m['n_resolved']}/{m['n_units']} |")
    dev_lines.append("")
    dev_lines.append("Component identifiability (claim-bearing units):")
    dev_lines.append("| split | component | identifiability |")
    dev_lines.append("|---|---|---|")
    for split_name, ident_obj in (("dev", dev["component_identifiability"]),
                                  ("test", ident)):
        for comp in ("R", "C", "T"):
            v = ident_obj[comp]
            dev_lines.append(f"| {split_name} | {comp} | {v['fraction']} |")
    (TEST_OUT / "MSM_V1_DEV_TEST_COMPARISON.md").write_text("\n".join(dev_lines) + "\n", encoding="utf-8")

    # ---- manifest / hashing ----
    methods_md = architecture_summary()
    manifest = {
        "experiment_id": "MSM_V1_FINAL_TEST_MANIFEST",
        "execution_timestamp": datetime.utcnow().isoformat() + "Z",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dataset": {"id": "multisource_memory", "revision": "5b428c8d6826a7dc73ac05f5239b089a6c631ac1",
                    "seed": SEED, "split": "test"},
        "random_seeds": "none (deterministic)",
        "policy_configuration": POLICY_WEIGHTS,
        "arm_order": ARM_ORDER,
        "git_commit": None,
        "sha256": {
            "dataset_split_config": sha256(DATA / "seeds" / SEED / "config" / "persona_splits.json"),
            "frozen_deriver": sha256(ROOT / "research_evaluation" / "msm_deriver.py"),
            "frozen_trust_table": sha256(TRUST_ARTIFACT),
            "frozen_train_claims": sha256(TRAIN_CLAIMS_ARTIFACT),
            "frozen_dev_results": sha256(DEV_RESULTS),
            "test_derived_claims": sha256(TEST_CLAIMS_ARTIFACT),
            "test_results": sha256(TEST_OUT / "MSM_V1_TEST_RESULTS.json"),
            "identifiability": sha256(TEST_OUT / "MSM_V1_COMPONENT_IDENTIFIABILITY.json"),
            "policy_comparison": sha256(TEST_OUT / "MSM_V1_POLICY_COMPARISON.json"),
            "dev_test_comparison": sha256(TEST_OUT / "MSM_V1_DEV_TEST_COMPARISON.md"),
        },
        "population": {"N_personas": len(test_personas), "N_units": n_units,
                       "N_with_claims": n_with, "N_without_claims": n_units - n_with},
        "methodology": methods_md,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"TEST claims derived: {len(test_claims)}  grid units: {len(eps)}  "
          f"with claims: {n_with}  without claims: {n_units - n_with}")
    print(f"{'arm':<24}{'cov':>6}{'strict':>8}{'selective':>10}{'overwrite':>10}{'decisions':>10}")
    for a in ARM_ORDER:
        m = results[a]
        print(f"{a:<24}{m['resolution_coverage']:>6.3f}{m['strict_accuracy']:>8.3f}"
              f"{m['selective_accuracy']:>10.3f}{m['incorrect_overwrite']:>10}{m['n_units']:>10}")
    print("\ncomponent identifiability (claims-bearing units):")
    for k, v in ident.items():
        print(f"  {k}: {v['fraction']}")
    print("\npaired win rates (full_lcm vs baseline):")
    for k, v in paired_comparisons.items():
        print(f"  {k}: a={v['full_lcm_correct_only']} b={v['baseline_correct_only']} "
              f"rate={v['full_lcm_win_rate_excluding_ties']}")
    print("\nartifacts written to", TEST_OUT)


def architecture_summary() -> dict:
    return {
        "psu": "one (persona, question) episode; grid = all 18 questions x all TEST personas (2160)",
        "no_claim_handling": "units without any claim are kept in the grid; scored unresolved/as incorrect for strict accuracy; explicitly counted",
        "trust_source": "dev_clean/TRUST_TABLE.json (TRAIN-only, frozen)",
        "deriver": "research_evaluation/msm_deriver.py",
        "policy": "run_clean_dev.py POLICY_WEIGHTS / decide / score_episodes / arm_metrics (unchanged)",
        "metrics": "resolution_coverage = resolved/total; strict = correct/total; selective = correct_among_resolved/resolved; overwrite = gold-in-claims & resolved & wrong",
        "identifiable_units": "units with at least one claim whose value equals gold",
    }


if __name__ == "__main__":
    main()