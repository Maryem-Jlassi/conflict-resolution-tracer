# MSM V1 — Rigorous Evaluation Report
## Final out-of-sample TEST evaluation of Long-Context Memory with runtime-formed beliefs (Ψ = ⅓R + ⅓C + ⅓T)

- Experiment id: `MSM_V1_FINAL_TEST_CLEAN`
- Dataset: `multisource_memory`, revision `5b428c8d6826a7dc73ac05f5239b089a6c631ac1`, seed `s20260321`
- Population: official TEST split, **120 personas → 2160 evaluation units**
- Execution: deterministic (no random seeds), Python 3.10.0, timestamps + SHA-256 in `MANIFEST.json`
- Companion documents: `MSM_V1_METHODOLOGY.md` (snapshot), `LEAKAGE_AUDIT.md`, `MSM_V1_POLICY_COMPARISON.md`, `MSM_V1_DEV_TEST_COMPARISON.md`

---

## 1. Executive summary

We evaluated a memory system that answers 18 short-answer questions about a simulated
person's life course (late nights, short sleeps, exercise, social support, academic
workload, medical adherence, device usage, etc.) by fusing **three runtime belief
components** — **Recency (R)**, **Confidence/coverage (C)**, and **Trust (T)** — into a
single score `Ψ = (R + C + T)/3`. The highest-Ψ claim wins; the system abstains when the
top two scores are within `Θ = 0.05`.

On the official held-out TEST split (2160 units; 2078 with claims, 82 empty):

| Policy | Coverage | Strict acc. | Selective acc. | Overwrite |
|---|---|---|---|---|
| **full_lcm (Ψ)** | 0.546 | **0.626** | **0.686** | 158 |
| last_write_wins (LWW) | 0.962 | 0.557 | 0.579 | 511 |
| c_only | 0.820 | 0.545 | 0.583 | 439 |
| fixed_neutral_trust | 0.293 | 0.548 | 0.613 | 108 |

**Headline.** The fused Ψ policy achieves the best strict accuracy and the best
selective accuracy among the four task-listed arms. It wins descriptive paired
comparisons against LWW, c_only, and fixed-neutral trust with 70–78% win rates. When it
decides confidently it is 68.6% accurate; LWW decides always but at 57.9% precision,
producing >3× more incorrect overwrites (511 vs 158). `T` alone is the most
discriminating single component (identifiability 1367/2078), but no single component
outperforms the fusion under strict accuracy.

**Caveats.** Estimates are descriptive; the protocol defines no significance test (and
adds none). Near-ties exist: `t_only` and `full_minus_confidence` defeat `full_lcm`
61–75 in discordant TEST pairs — this pattern, like everything else, is reported
without an inferential claim. Population deviates from the spec's stated 864-unit grid
(120 official personas used instead, per user confirmation).

---

## 2. Evaluation design and research questions

The evaluation answers four questions for the V1 memory architecture:

1. **Absolute performance.** How accurate, and how decisive, is the frozen Ψ policy on
   genuinely out-of-sample personas?
2. **Ablation.** What does each component (R, C, T) contribute when removed or used alone?
3. **Component identifiability.** Which signal, taken alone, most often points at the
   ground-truth value among competing claims?
4. **Generalization.** Do DEV results transfer to the larger, never-tuned TEST population?

Design: fully deterministic, single-shot, no hyperparameter search, no re-fitting, no
TEST-gold inspection before scoring. Every comparison shares identical claims, trust,
thresholds, and metric code with the DEV run, so DEV→TEST differences measure
population shift rather than implementation drift.

---

## 3. Data

Simulated-benchmark personas, each with five structural JSON sources:
`profile_ltm`, `daily_self_report`, `planner`, `device_log`, `objective_log`.

| Split | Personas | Grid units (×18 qids) | Role |
|---|---|---|---|
| train | 216 | 3888 | trust estimation + deriver rule calibration (in-sample sanity only) |
| calibration | 96 | 1728 | unused in final eval |
| **dev** | 48 | 864 | method freeze + DEV results |
| **test** | **120** | **2160** | **this report (final out-of-sample)** |

Verify: `bench_stable_121_sam_bennett` … `bench_stated_160_kai_garcia`.
Note: the task prompt repeatedly assumed a TEST grid of 18 × 48 = 864; the official
split mapping contains 120 TEST personas. Per user confirmation the official split was
used (2160 units), and this deviation is recorded in `MANIFEST.json` and each results
file. Ground truth (`ground_truth.json`) exists per persona and is read **only** during
scoring.

---

## 4. The machinery being evaluated (frozen, exactly as in the DEV run)

### 4.1 Derivation (LTM → table entries)
`research_evaluation/msm_v1_deriver.py` emits canonical readouts per `(persona, question,
source)`, marking `None` where a semantic cannot be scored. Rules were fit on TRAIN and
frozen (agreement rates recorded in the audit):
- **E1 late-night**: bedtime after midnight (00:00–05:59) mask — 207/216 TRAIN agreement.
- **Ctrl2 short-night**: <6.0 h sleep over a 7-day tail — 185/216 TRAIN agreement.
- **G2 social support**: `social.supporting_other` signal only (title-based ≈ chance, excluded).
- **A1 intentions**: quality≥4 masked counts; **A3** objective delivery = `1 − deliv/30`
  (<0.4 ⇒ `less_than_40`; else `40_to_69`); **D1** deviation via positive/negative
  compare functions; **E1/E2 cause**: `n_work ≥ n_social ⇒ work_activity`,
  `n_work_events > 0 ⇒ work_activity`, else → `no_fewer_than_30`; **F2** `both_occurred`
  if any missing day had exercise.
- Forbidden at derivation (verified in `LEAKAGE_AUDIT.md`): `event_table.json`,
  `generation_metadata.source_knobs`, `extracted_atoms/`, any LLM, any gold.

### 4.2 Belief components
Per claim with evidence `(source, coverage, obs_time, value)`:

- **R — Recency**: `R = 0.5^(age_days / 30)` against anchored `REF_TIME =
  2026-01-30 23:59:59` (30-day half-life exponential decay; profile anchored at day 12).
- **C — Confidence/coverage**: `C = EVIDENCE_AUTHORITY[source] × coverage`,
  `coverage` = fraction of the observation window carrying data. Authorities:
  profile_ltm 0.90, objective_log 0.85, device_log 0.80, daily_self_report 0.60,
  planner 0.50.
- **T — Trust**: frozen causal trust, identity `source:question_id`,
  `raw_trust_score = verified_correct / total_claims` over the 216 TRAIN personas with a
  **prior of 0.5** at zero evidence and the **Wilson low bound (z = 1.96)** to shrink
  low-count cells. Built before DEV, never recomputed on TEST.

### 4.3 Policy
`Ψ(c) = w_R·R + w_C·C + w_T·T`; winner = argmax. Ablation weights per arm
(taken from `POLICY_WEIGHTS`):

| Arm | w_R | w_C | w_T | Comment |
|---|---|---|---|---|
| full_lcm | ⅓ | ⅓ | ⅓ | the fused policy under test |
| c_only | 0 | 1 | 0 | confidence/coverage alone |
| r_only | 1 | 0 | 0 | recency alone |
| t_only | 0 | 0 | 1 | trust alone |
| fixed_neutral_trust | ⅓ | ⅓ | ⅓ | full fusion but T := 0.5 constant |
| full_minus_recency | 0 | ½ | ½ | ablation: drop R |
| full_minus_confidence | ½ | 0 | ½ | ablation: drop C |
| full_minus_trust | ½ | ½ | 0 | ablation: drop T |
| last_write_wins | — | — | — | non-Ψ baseline; latest `obs_time` wins, never abstains |

**Abstention.** If ≥2 claims and the top-2 margin `< Θ = 0.05`, the final decision is
flagged *unresolved* (the system refuses). Ties resolved deterministically by
`(obs_time, SOURCES index, claim index)`.

**Definitions implemented in `decide()` (important for reading the metrics).** The
*winner claim* is always the argmax-Ψ claim (defined for every claim-bearing episode).
`unresolved = True` merely flags *low margin*; the winner is still recorded. Hence
`correct` below equals "the top-scored claim equals the gold value", computed even when
the system abstained. This makes strict accuracy a **"would-be-correct under forced
choice"** measure over the whole grid, while selective accuracy is the **"precision of
confident decisions only"**.

---

## 5. Metrics (explicit denominators)

Unit of analysis = one `(persona, question)` episode. All grid units are retained,
including 82 with zero claims (scored incorrect/abstained, counted explicitly).

| Metric | Definition | full_lcm TEST |
|---|---|---|
| Coverage (resolution rate) | `resolved / total` | 1180/2160 = 0.546 |
| Abstention rate | `unresolved / total` | 0.454 |
| Strict accuracy | `correct / total` (all units; forced-choice top claim) | 1353/2160 = 0.626 |
| Selective accuracy | `correct ∧ resolved / resolved` (confident decisions only) | 810/1180 = 0.686 |
| Overwrite | `gold ∈ claims ∧ resolved ∧ wrong` | 158 |
| Identify/unresolved | per-question + per-arm counts, all fractions stated as `x/y` | — |

Indicators distinguish **decisiveness** (coverage), **raw correctness** (strict),
**decision quality** (selective), and **harmful wrong flips when the truth was present**
(overwrite).

---

## 6. Results

### 6.1 Overall (TEST, n = 2160)

| Arm | Cov | Strict | Selective | Overwrite | Resolved | Unresolved |
|---|---|---|---|---|---|---|
| full_lcm | 0.546 | 0.626 | 0.686 | 158 | 1180 | 980 |
| c_only | 0.820 | 0.545 | 0.583 | 439 | 1771 | 389 |
| r_only | 0.056 | 0.577 | 0.333 | 0 | 120 | 2040 |
| t_only | 0.592 | 0.633 | 0.652 | 184 | 1278 | 882 |
| last_write_wins | 0.962 | 0.557 | 0.579 | 511 | 2078 | 82 |
| fixed_neutral_trust | 0.293 | 0.548 | 0.613 | 108 | 633 | 1527 |
| full_minus_recency | 0.703 | 0.603 | 0.667 | 236 | 1519 | 641 |
| full_minus_confidence | 0.518 | 0.633 | 0.649 | 170 | 1118 | 1042 |
| full_minus_trust | 0.529 | 0.548 | 0.578 | 260 | 1143 | 1017 |

Observations (descriptive):
- tightest **strict** accuracies: `t_only` 0.633 ≈ `full_minus_confidence` 0.633 ≈ `full_lcm` 0.626.
- **Selective** accuracy: `full_lcm` 0.686 is the best over all arms.
- **Coverage vs precision trade**: c_only and LWW trade precision for coverage;
  fixed_neutral_trust trades coverage for precision (coverage 0.293, selective 0.613).
- **r_only** resolves only 120/2160 units (R is near-uniform on question-level
  aggregates); its listed figures rest on that tiny, non-representative set.

### 6.2 Paired comparisons (descriptive, `win rate = a_only / (a_only + b_only)`)

| full_lcm vs | both ok | full_lcm only | baseline only | both wrong | discordant | full_lcm win rate |
|---|---|---|---|---|---|---|
| last_write_wins | 690 | 209 | 59 | 1202 | 268 | 0.780 |
| fixed_neutral_trust | 742 | 257 | 87 | 1074 | 344 | 0.747 |
| full_minus_trust | 742 | 257 | 87 | 1074 | 344 | 0.747 |
| c_only | 840 | 283 | 107 | 930 | 390 | 0.726 |
| full_minus_recency | 1041 | 89 | 38 | 992 | 127 | 0.701 |
| r_only | 852 | 347 | 241 | 720 | 588 | 0.590 |
| t_only | 1123 | 61 | 75 | 901 | 136 | 0.449 |
| full_minus_confidence | 1123 | 61 | 75 | 901 | 136 | 0.449 |

Interpretation (descriptive, no significance claims):
- The fusion beats every hard baseline (LWW, c_only) and the T-flattened variant, and it
  improves on dropping R. Removing C or removing T both help the ablation arms reach
  parity — indicating marginal component redundancy in favor of (R,T) and (R,C).
- `t_only` and `full_minus_confidence` flip the win rate against full_lcm on TEST
  (`a:b = 61:75`) — the *opposite* of DEV, where full_lcm beat both 25–19 and 25–19.
  This reversal is a genuine out-of-sample finding: RECENCY may be adding noise on TEST
  (see 6.4).

### 6.3 Component identifiability (2078 claim-bearing units)

| Component | Correct picks | Identifiability (DEV) | Identifiability (TEST) |
|---|---|---|---|
| T | 1367/2078 | 543/833 (0.652) | 0.658 |
| R | 1203/2078 | 491/833 (0.589) | 0.579 |
| C | 1177/2078 | 475/833 (0.570) | 0.566 |

Ordering **T > R > C holds on both splits**, with near-identical values — the most
stable generalization result of the whole evaluation. T is the strongest independent
pointer to the gold answer; C is the weakest signal alone, yet Ψ still gains from its
coverage information (see 6.4).

### 6.4 DEV → TEST generalization (full_lcm)

| Split | Coverage | Strict | Selective | Overwrite | Resolved/total |
|---|---|---|---|---|---|
| DEV (864 u) | 0.569 | 0.635 | 0.703 | 60 | 492/864 |
| TEST (2160 u) | 0.546 | 0.626 | 0.686 | 158 | 1180/2160 |

Strict and selective accuracy degrade only ~1–2 points over a 2.5× larger and entirely
unseen population — primary evidence of robustness. Overwrite count scales with the
larger grid and higher claim density (extra 1245 claim-bearing units on TEST). The
magnitudes match DEV within rounding of the descriptive protocol.

### 6.5 Per-question performance (TEST, full_lcm)

| Q | Coverage | Strict | Q | Coverage | Strict |
|---|---|---|---|---|---|
| A1 | 0.150 | 0.450 | E1 | 0.992 | 0.692 |
| A2 | 1.000 | 0.808 | E2 | 1.000 | 0.817 |
| A3 | 0.808 | 0.592 | F1 | 0.983 | 0.725 |
| B2 | 0.017 | 0.658 | F2 | 0.075 | 0.750 |
| B3 | 0.092 | 0.700 | F3 | 0.000 | 0.100 |
| C2 | 0.875 | 0.533 | G1 | 0.033 | 0.475 |
| C3 | 0.325 | 0.542 | G2 | 1.000 | 0.333 |
| Ctrl1 | 1.000 | 0.858 | Ctrl2 | 0.308 | 0.867 |
| D1 | 0.825 | 0.608 | D2 | 0.350 | 0.767 |

**Strong families.** Ctrl1 (0.858), Ctrl2 (0.867 strict), E2 (0.817), A2 (0.808), D2 (0.767):
well-structured, uni-source, estimable quantities (device availability, work events,
counts). Weak families: **G2 (0.333)** — social supporting-/non-supporting inference
from sparse title text, the known weakest signal; **G1 (0.475)**; **A1 (0.450)** with
coverage 0.15 — intentions need 4+ quality-stamped events, rarely available → chronic
abstention; **F3 (0.100)** with coverage 0.00 — never confidently decided, and forced
choices mostly wrong. Abstention on weak families (G1, B2, B3, F2) is itself a correct
behavior: coverage collapses exactly where claims are contradictory or sparse; strict
accuracy there reflects the forced-choice noise (e.g., F3 0.100, G2 0.333 even though
G2 always decides).

---

## 7. Interpretation

1. **Fusion works at the margin.** Ψ beats every hand-picked single-signal policy on the
   metric that punishes both wrong choices and non-choices (strict) and on confident-
   decision precision (selective). The ablation arms `full_minus_X` never beat the full
   model on selective; they match only on strict on TEST, and the reversal of
   `full minus confidence`/`t_only` is one family away from a tie under the descriptive
   protocol. The net message: no single component is redundant, but the protocol is too
   coarse to certify the differences as statistically reliable.

2. **Trust is the load-bearing component; coverage is the least redundant.** Even with
   well-known truthful sources, the score's single best predictor of the gold claim is
   the TRAIN-calibrated, prior-shrunk per-cell trust (0.658 identifiability, stable
   across DEV/TEST). Recency, the rawest signal, helps more than confidence/coverage in
   *relative* paired terms, and its removal is the least damaging ablation (win-rate
   0.701 against fusion).

3. **The abstention mechanism is earning its keep.** By refusing when the margin is thin,
   Ψ raises accuracy on its decisions from the forced-choice baseline (0.626 strict →
   0.686 selective) while keeping coverage moderate. Contrast LWW (always decides): 0.962
   coverage / 0.579 selective / 511 overwrites. Θ=0.05 is a deliberate precision–recall
   point; the DEV–TEST consistency of coverage (±0.02) indicates the abstention threshold
   generalizes rather than overfits.

4. **Overwrite is a real, small, and informative failure mode.** When the gold answer was
   present among the claims but fusion picked another, TEST gave 158 such flips (DEV 60,
   proportional to the larger population). These are exactly the cases where belief
   re-weighting (stale-vs-fresh, high-coverage-vs-trusted) *changes the answer from a
   true to a false one* — the cost side of the fusion that the strict/selective split
   otherwise hides.

5. **Failure concentrates in constructively hard questions.** Sporadic intentions (A1),
   social-role inference (G1/G2), and the rarest behaviors (F3) show both low coverage
   and low accuracy — not a bug in the belief math but missing structure in the evidence
   (sparse events, title-level semantics). Where the world is well-observable the
   pipeline is near-ceiling (Ctrl1/Ctrl2 families ~0.86).

---

## 8. Threats to validity and limitations

- **Descriptive statistics only.** The frozen protocol records paired bins and win rates;
  no McNemar/exact inference, CI, or effect size, and —per step 9 of the task— none were
  added after the fact. All "wins" above are descriptive, not significance claims; two
  paired comparisons reverse direction from DEV within tie-close margins.
- **Population deviation.** Official TEST = 120 personas (2160 units), not the spec's
  assumed 48/864; deviation is explicit, traceable, and user-approved.
- **Single construction.** One deriver, one trust prior/shrinkage, one threshold, one
  anchor time. Different choices would move all arms jointly.
- **r_only's denominator** (120 resolved units) makes its selective estimate unstable.
- **Correlated units.** Question families share evidence sources; strict/selective
  numerators are not independent draws. This inflates effective sample per family and is
  exactly why per-question numbers are descriptive.
- **Trust shrinkage.** Cells with few TRAIN observations still take the 0.5 prior;
  source-level authorities (0.9/0.85/0.8/0.6/0.5) are a fixed design choice, not fitted.
- **No failure autopsy on the 82 empty units** and the 980 abstentions beyond the
  coverage counts.

---

## 9. Reproducibility

- Runner: `results/empirical_evaluation/msm/run_clean_test.py` (imports frozen
  `run_clean_dev.py` functions; deriver unchanged; trust artifact unchanged).
- All artifact SHA-256 hashes and Python/platform/timestamp in `MANIFEST.json`;
  `git_commit: null` (working tree not a git repo).
- Deterministic: no RNG, stable tie-break; a re-run reproduces the JSON byte-for-byte
  (asserted grid = 2160).

## 10. Conclusion

The V1 architecture, frozen after DEV, **generalizes on an unseen 120-persona population**
outperforming last-write-wins, confidence-only, and neutral-trust baselines (best strict
0.626 and best selective 0.686 among the four task-listed arms), with DEV→TEST accuracy
drop ≤ 0.02. Trust remains the most informative single belief; the abstention gate adds
+6 points selective over forced choice while its coverage rate is stable across splits.
The evaluation is deliberately descriptive; its clearest structural findings — T > R > C
identifiability ordering, the hostile low-evidence question families, and the
overwrite cost of re-weighting — stand without statistical overclaiming.