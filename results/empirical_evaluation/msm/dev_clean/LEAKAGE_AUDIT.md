# Leakage Audit — MSM V1 Clean DEV Evaluation

Status: **PASS** — no leakage of DEV/TEST gold, event tables, source knobs, frozen
LLM atoms, or DEV outcomes into derivation, trust, or policy.

## 1. Files read by the pipeline

| Pipeline step | Files read | Leakage check |
|---|---|---|
| Derivation (`msm_v1_deriver.py`) | `structural_sources/{profile_ltm,daily_self_report,planner,device_log,objective_log}.json` only | OK — source-visible projections only |
| Trust build (`build_trust.py`) | TRAIN personas' structural sources **and** TRAIN `ground_truth.json` | OK — TRAIN-only, causal |
| DEV run (`run_clean_dev.py`) | DEV structural sources + DEV `ground_truth.json` (scoring only) | OK — gold touched only in Stage-C scoring |
| Startup path/date constants | benchmark-wide `start_date=2026-01-03`, `REF_TIME` | OK — global constants, not persona-specific |

## 2. Never touched

- `event_table.json` — the ground-truth event log is **never opened** by any stage
  (verified by code inspection; the deriver reads only the five files above).
- `generation_metadata.json` `source_knobs` (conflict rate, under/over-report bias,
  planner optimism/behavior-gap, device/objective dropout and noise) — **not read**.
  No runtime input depends on a knob value; only question/aggregation semantics do.
- `extracted_atoms/` (the frozen TEST-split LLM extractions, personas 121–160) — **not
  read**; TEST personas are never loaded by any stage.
- DEV gold in derivation — the deriver exposes no gold parameter and is run on DEV
  claims identically to TRAIN.
- Zero LLM API calls (deriver is pure Python arithmetic; `run_v1_eval`'s LLM/Ollama
  paths are not invoked).

## 3. Causal ordering / freeze discipline

1. DEV list enumerated from `config/persona_splits.json` (48 personas: `bench_shift/
   stable/stated_073..088`).
2. TRAIN readouts scored against TRAIN gold → per-(source,qid) trust → `TRUST_TABLE.json`
   **written before** the DEV run.
3. Rule thresholds validated on TRAIN (`TRAIN_DERIVED_ASSERTIONS.json`), then frozen
   (`deriver_version = v1-clean-1`).
4. DEV run computes `(R, C, T)` per claim from structural data + frozen trust, applies
   the 9 arms, and scores against DEV gold in output generation only.
5. No DEV outcome was used to re-fit a rule, threshold, weight, or trust value.

## 4. Run-to-run determinism

Pure deterministic arithmetic with fixed ordering keys
(`obs_time`, source index, enumerated claim index). No RNG, no floating nondeterminism
beyond IEEE double arithmetic. Re-running `build_trust.py` then `run_clean_dev.py`
reproduces `MSM_V1_DEV_RESULTS.json` exactly.

## 5. Reproducibility artifacts

`TRUST_TABLE.json`, `TRAIN_DERIVED_ASSERTIONS.json`, `DEV_DERIVED_ASSERTIONS.json`,
`MSM_V1_DEV_RESULTS.json`, `MSM_V1_COMPONENT_IDENTIFIABILITY.json`,
`MSM_V1_POLICY_COMPARISON.json` (all open JSON, human-readable).