# MSM V1 Final OUT-OF-SAMPLE TEST Evaluation — Methodology (clean deterministic)

This document is the TEST-run snapshot of the frozen V1 methodology. The complete
canonical description lives in `msm/dev_v1_clean/MSM_V1_METHODOLOGY.md`; nothing in
that document or its artifacts was changed for this run. This snapshot records the
exact configuration executed on the official TEST split.

## 1. Scope

- Dataset: `multisource_memory` revision `5b428c8d6826a7dc73ac05f5239b089a6c631ac1`, seed `s20260321`.
- Population: **official TEST split** from `config/persona_splits.json` = **120 personas**,
  first `bench_stable_121_sam_bennett`, last `bench_stated_160_kai_garcia`.
- Grid: 18 questions × 120 personas = **2160 units** (1296-unit deviation vs the spec's
  864-unit assumption is a documented, user-confirmed deviation; see `MANIFEST.json` /
  results `population_deviation_note`).
- Execution: deterministic, no random seeds, Python `3.x` (see `MANIFEST.json`).

## 2. Frozen protocol (unchanged from DEV)

- Deriver: `research_evaluation/msm_v1_deriver.py` (v1-clean-1) — canonical
  deterministic readouts per `(qid, source)` from the 5 structural sources; TRAIN-fit
  rules E1 after-midnight mask (207/216), Ctrl2 `<6.0h` 7-day tail (185/216),
  G2 `social.supporting_other` only, A1/D1/E1/E2/F2/A3 objective rule, etc.
  `None` where a semantic is unobservable; no gold, no event_table, no source_knobs.
- Per-claim components: `C = authority(source) × coverage`
  (profile .90, objective .85, device .80, self-report .60, planner .50);
  `R = 0.5^(age_days/30)` against `REF_TIME = 2026-01-30 23:59:59`, profile anchored day 12;
  `T` = frozen TRAIN-only causal trust (`raw_trust_score`, prior 0.5, Wilson z=1.96).
- Policy: `Ψ = (R + C + T)/3` equal weights; resolution requires top-2 margin ≥ Θ=0.05
  (abstain otherwise); ties broken deterministically by `(obs_time, SOURCE index, idx)`.
- Arms (same as DEV, all 9): full_lcm, c_only, r_only, t_only, last_write_wins,
  fixed_neutral_trust, full_minus_recency, full_minus_confidence, full_minus_trust.
- Metrics (explicit denominators): `resolution_coverage = resolved/total`; `strict_accuracy =
  correct/total`; `selective_accuracy = correct_among_resolved/resolved`; `incorrect_overwrite`
  = gold-in-claims & resolved & wrong; counts for decisions/abstentions/resolved-unresolved/no-claim.
- Identifiability: argmax of the single component over claims picks a value;
  `component_identifiability = share of picks equal to gold`, computed on the 2078
  claim-bearing units. Interpretation caveat: R is near-uniform on question-level
  aggregates (e.g. A-counts), so r_only effectively abstains extremely often
  (coverage 0.056, only 120 resolved units on TEST).
- Statistical treatment: descriptive paired comparisons only; **no significance test** is
  part of the frozen methodology, so none is run or claimed (see LEAKAGE_AUDIT §6).

## 3. Principal results (TEST, n=2160 units)

| Policy | Coverage | Strict Acc | Selective Acc | Overwrite |
|---|---|---|---|---|
| full_lcm (Ψ) | 0.546 | 0.626 | 0.686 | 158 |
| last_write_wins | 0.962 | 0.557 | 0.579 | 511 |
| c_only | 0.820 | 0.545 | 0.583 | 439 |
| fixed_neutral_trust | 0.293 | 0.548 | 0.613 | 108 |
| r_only | 0.056 | 0.577 | 0.333 | 0 |
| t_only | 0.592 | 0.633 | 0.652 | 184 |
| full_minus_recency | 0.703 | 0.603 | 0.667 | 236 |
| full_minus_confidence | 0.518 | 0.633 | 0.649 | 170 |
| full_minus_trust | 0.529 | 0.548 | 0.578 | 260 |

- full_lcm achieves the top strict accuracy (0.626) among the four task-listed
  TEST arms (last_write_wins 0.557, c_only 0.545, fixed_neutral_trust 0.548).
- Paired (descriptive, win-rate excluding ties): full_lcm beats c_only .73,
  r_only .59, last_write_wins .78, fixed_neutral_trust .75, full_minus_recency .70,
  full_minus_trust .75; it is slightly out-performed by t_only and by
  full_minus_confidence (both 61 vs 75). These figures are descriptive; no
  significance is claimed and no new test was introduced.
- Component identifiability on claim-bearing units: T 1367/2078 > R 1203/2078 >
  C 1177/2078, matching the DEV ordering (T > R > C).

## 4. DEV → TEST generalization view

Full table in `MSM_V1_DEV_TEST_COMPARISON.md`. Headlines for full_lcm:

| Split | Coverage | Strict | Selective | Overwrite |
|---|---|---|---|---|
| DEV (864 u) | 0.569 | 0.635 | 0.703 | 60 |
| TEST (2160 u) | 0.546 | 0.626 | 0.686 | 158 |

Strict/selective accuracy generalize within ~1–2 points; coverage and overwrite differ
(overwrite total rises with the 2.5× larger TEST population and greater claim counts,
411 extra claim-bearing units).

## 5. Limitations

- Population label exceeds the task's stated 864-unit assumption (deviation documented).
- Descriptive-only statistics; paired win rates on TEST show two near-ties
  (t_only / full_minus_confidence) that are not significance-tested per protocol.
- Trust is TRAIN-only; any TEST-specific causal calibration was deliberately avoided.
- r_only coverage is near-zero because R is near-uniform on question-level aggregates;
  its 0.333 selective figure is computed on a tiny resolved set (120) and should not be
  read as competitive performance.