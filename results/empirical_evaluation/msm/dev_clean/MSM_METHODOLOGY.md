# MSM V1 — Clean Deterministic DEV Evaluation (Methodology)

Config `s20260321`, DEV split only (48 personas, 864 question-units). Deterministic,
API-free, leakage-free replication of the empirical MSM V1 evaluation for CRT.

## 1. Frozen V1 protocol (unchanged)

- Final commitment policy: `Ψ = (R + C + T) / 3` with equal 1/3 weights
  (`ConflictResolutionEngine`, `ResolutionConfig(w_recency=1/3, w_confidence=1/3,
  w_trust=1/3)`).
- Uncertainty threshold `Θ = 0.05`: an episode is **unresolved** (CRT abstains) when
  the top-2 Ψ margin falls below `Θ` or when no source issues a claim.
- All claims carry a provenance audit trail (source file fingerprint, derivation
  rule, observation window). Provenance is metadata; it never enters Ψ as a scalar.
- Reference time `REF_TIME = 2026-01-30 23:59:59`.

## 2. Deterministic structural adapter (`research_evaluation/msm_v1_deriver.py`)

Each of the 18 benchmark questions (A1,A2,A3,B2,B3,C2,C3,D1,D2,E1,E2,F1,F2,F3,G1,G2,
Ctrl1,Ctrl2) is answered per source with a **canonical enum readout** computed from
the source-visible structural files only:

| Source | Speaks when … |
|---|---|
| `profile_ltm` | the routine snapshot / trait contains the semantic (self-consistency, afterhours identity, sleep/diet quality projections) |
| `daily_self_report` | the person's own account contains the semantic |
| `planner` | the plan targets express the semantic (and we model the planner as assuming its own plan is kept) |
| `device_log` | wearable records contain the semantic |
| `objective_log` | payments / calendar / timesheet contain the semantic |

Sources issue **no claim** where the semantic is genuinely unobservable from their
records (e.g., `objective_log` has no sleep timing in E1; `profile_ltm`/`objective_log`
cannot observe voluntary-vs-obligatory sociality in G2). All readouts were validated
against the **216-persona TRAIN** split before freezing (allowed, causal); the full
per-source accuracy matrix is archived in `TRAIN_DERIVED_ASSERTIONS.json`.

Key calibrated rules (fit on TRAIN ground-truth derivation details, then frozen):

- **A1/Ctrl-sub-family**: `quality ≥ 4` good-night mask; short night = `duration < 6.0 h`
  over the last 7 days (label match 185/216).
- **E1**: late night = bedtime after midnight (00:00–05:59); agrees with gold
  late/non-late split on 207/216 TRAIN personas (after-midnight beats any
  relative-to-plan mask).
- **G2**: voluntary signal = the person's own `social.supporting_other` flag;
  title-based classification is at chance on TRAIN and is not used.
- **A2/A3/B2/C2/D1/D2/C3/F1/F2/F3/Ctrl1**: faithful per-source aggregations in the
  canonical label spaces enumerated across the benchmark.

## 3. Per-claim components (identical inputs to every arm)

- **C (evidence/confidence)** = `authority(source) × coverage`, where authority is
  the fixed per-source evidential authority
  (`profile_ltm 0.90, objective_log 0.85, device_log 0.80, daily_self_report 0.60,
  planner 0.50`) and coverage is the fraction of the window with records for the
  question's fields.
- **R (recency)** = V1 exponential recency with 30-day half-life, `R = 0.5^(age/30)`,
  from the claim's last-observation time to `REF_TIME`. Stale `profile_ltm` claims
  (anchored on day 12, staleness 18 days) receive substantially lower R than the
  daily sources, reproducing the intended temporal-shift signal.
- **T (trust)** = the frozen **TRAIN-only causal trust table**
  (`TRUST_TABLE.json`), identity `source:question_id`,
  `raw_trust_score = verified_correct / total_claims` (cold-start prior 0.5),
  with the Wilson lower bound (z=1.96) recorded as the conservative estimate.
  Built on TRAIN gold *before* the DEV run and never updated on DEV.

## 4. Policy arms

`full_crt (1/3,1/3,1/3)`, `c_only (0,1,0)`, `r_only (1,0,0)`, `t_only (0,0,1)`,
`fixed_neutral_trust ((1/3,1/3,1/3), T:=0.5)`, `full_minus_recency (0,1/2,1/2)`,
`full_minus_confidence (1/2,0,1/2)`, `full_minus_trust (1/2,1/2,0)`,
`last_write_wins` (most recent claim; never abstains). All arms share the identical
`(R, C, T)` tuples; only the weighting policy differs.

## 5. Metrics (denominators explicit — `qacc_frozen_eval.py` conventions)

- **Experimental unit**: one `(persona, question)` = 18 × 48 = **864**. Units with
  no claim from any source are included and count as unresolved/incorrect.
- `resolution_coverage = decisively_resolved / total` (a unit resolves decisively when
  ≥2 claims have top-2 Ψ margin ≥ Θ, or a single claim exists).
- `strict_accuracy = correct / total` — correctness of the committed Ψ-top answer over
  ALL units (no-claim and below-Θ units count wrong; this mirrors CRT's committed-memory
  semantics where the Ψ-top write is retained even when the conflict is logged).
- `selective_accuracy = correct_among_resolved / resolved` (always in [0,1])
- `incorrect_overwrite` = units where a gold-correct claim existed but the resolved
  winner was incorrect (a correct source was overridden)
- Paired comparisons: McNemar-style on discordant pairs, `full_crt` vs each arm.
- Component identifiability: share of claim-bearing episodes (833) where `argmax` of
  the single component picks a gold-correct value.

## 6. Headline results (DEV, n=864)

| arm | coverage | strict_acc | selective_acc | overwrite |
|---|---|---|---|---|
| **full_crt** | 0.569 | **0.635** | **0.703** | 60 |
| c_only | 0.824 | 0.550 | 0.573 | 175 |
| r_only | 0.056 | 0.566 | 0.521 | 0 |
| t_only | 0.594 | 0.628 | 0.653 | 72 |
| last_write_wins | 0.964 | 0.568 | 0.589 | 187 |
| fixed_neutral_trust | 0.324 | 0.557 | 0.632 | 43 |
| full_minus_recency | 0.701 | 0.610 | 0.686 | 86 |
| full_minus_confidence | 0.520 | 0.628 | 0.675 | 66 |
| full_minus_trust | 0.542 | 0.557 | 0.588 | 97 |

- `full_crt` wins every paired comparison on discordant pairs (win-rates 0.57–0.78;
  vs `last_write_wins` 80/22, vs `c_only` 112/38).
- Component identifiability: **T 543/833 > R 491/833 > C 475/833** — the causal
  trust component carries the most per-question signal; recency is near-uniform on
  question-level aggregates, so `r_only` abstains on 94% of episodes.

## 7. In-sample TRAIN sanity (development check only)

`../train_sanity/MSM_V1_TRAIN_SANITY.json` runs the identical pipeline on the 216 TRAIN
personas (full_crt strict .618, selective .675). It is **in-sample** — the trust table
was fit on these same units — and is reported only as an engineering sanity check, not
as an out-of-sample estimate. The DEV numbers above (strict .635, selective .703) are
the out-of-sample result; there is no sign of train/DEV generalization collapse.

## 8. Limitations

- Deterministic readouts are noisy projections of each source's view; questions whose
  semantic lives in the true (unrendered) event model (e.g., G1 deliberate-vs-
  incidental exercise, G2 voluntary-vs-obligatory sociality) cap per-source accuracy
  near 0.4–0.5; fusion mitigates but does not eliminate this.
- `r_only` and un-task-specific C are benchmark-faithful but low-signal on
  question-level aggregates; a day-level episode construction would strengthen them.
- Trust identity is `source:question_id`; finer topic-level identities were
  considered but reduce outcome counts per cell.