# MSM V1 LEAKAGE AUDIT — FINAL TEST EVALUATION (official 120-persona split)

Run: `results/empirical_evaluation/msm/run_clean_test.py`
Date: 2026-08-19 (manifest `execution_timestamp`; deterministic, reproducible).

## 1. Derivation input scope (what the frozen deriver may see on TEST)

TEST derived claims (`TEST_DERIVED_ASSERTIONS.json`, n=5558) were produced by the
unchanged `research_evaluation/msm_v1_deriver.py` via `build_claims`. For each of the
120 TEST personas it read only:

- `structural_sources/profile_ltm.json`
- `structural_sources/daily_self_report.json`
- `structural_sources/planner.json`
- `structural_sources/device_log.json`
- `structural_sources/objective_log.json`

No other data was consumed during derivation.

## 2. Forbidden inputs checklist

| Forbidden source | Consulted on TEST? |
|---|---|
| TEST `ground_truth.json` (any persona) | NO during derivation; read ONLY in final scoring stage (see §4) |
| TEST `event_table.json` | NO |
| `generation_metadata.json` source_knobs | NO |
| `extracted_atoms/` (any persona) | NO |
| Any LLM / model readouts | NO (fully deterministic code) |
| Manual or TEST-specific extraction rules | NO (deriver identical to DEV/TRAIN) |
| TRAIN/DEV `ground_truth.json` inside the 120 TEST personas | NO |
| Any recalculation of trust on TEST | NO (see §3) |

## 3. Trust is frozen TRAIN-only (no TEST gold for trust)

`T` comes exclusively from `dev_v1_clean/TRUST_TABLE.json`, built earlier from the
216 TRAIN personas using `raw_trust_score = verified_correct/total_claims` with prior
0.5 and Wilson low bound (z=1.96) at low counts. It was **not recomputed, not extended,
not thresholded** for the TEST run. SHA-256 of the reused trust artifact is recorded
in `MANIFEST.json` (`frozen_trust_table`).

## 4. TEST ground-truth consumption (scoring only, Stage C)

TEST gold is loaded inside `run_clean_test.py` only to build `gold_map` used by
`score_episodes`/`arm_metrics`/`identifiability` for final accuracy accounting.
Gold does not influence claim derivation, trust, policy weights, thresholds,
arm set, or grid construction. This is identical to the DEV run's Stage-C flow.

## 5. Grid / unit-of-analysis

- Grid = all 18 questions × 120 official TEST personas = **2160 units** (per user
  decision to follow `persona_splits.json`, not the spec's 864-unit assumption).
- 2078 units carried at least one claim; 82 units had no claims and are scored as
  unresolved/incorrect in strict accuracy with explicit counts (`n_units_no_claims`).
- Experimental unit = `(persona, question)`; no per-claim reweighting.

## 6. Statistical discipline

Paired comparisons are reported exactly as in DEV: descriptive McNemar-style binned
counts (`both_correct / full_lcm_only / baseline_only / both_wrong / discordant /
win-rate-excluding-ties`). The frozen methodology defines **no significance test**;
therefore no result — including the TEST reversal described in the results — is
claimed statistically significant, and no new test was added for this run.

## 7. DEV artifacts integrity

No file under `msm/dev_v1_clean/` was created, modified, or deleted by this run
(reads only). Hash of the DEV results artifact is recorded in `MANIFEST.json`
(`frozen_dev_results`). DEV baseline numbers used in `MSM_V1_DEV_TEST_COMPARISON.md`
are read from the frozen `MSM_V1_DEV_RESULTS.json`, not recomputed.