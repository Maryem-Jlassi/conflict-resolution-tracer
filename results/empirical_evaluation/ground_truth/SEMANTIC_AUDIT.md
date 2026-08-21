# Ground-Truth Semantic Audit

**Date**: 2026-08-18  
**Experiment**: E014–E016 ground-truth evaluation  
**Auditor**: Kilo  
**Status**: PRELIMINARY — E015 metrics require semantic correction

---

## 1. Gold Verdict Distribution

| Gold Verdict | Count | % of 144 |
|-------------|-------|----------|
| A_CORRECT | 37 | 25.7% |
| B_CORRECT | 0 | 0.0% |
| BOTH_TRUE | 25 | 17.4% |
| BOTH_FALSE | 69 | 47.9% |
| INSUFFICIENT_EVIDENCE | 13 | 9.0% |
| **Total** | **144** | **100%** |

---

## 2. Semantic Definitions

### 2.1 A_CORRECT (37 episodes)
- **Meaning**: Only Claim A matches the benchmark's ground truth. Claim B is incorrect.
- **Conflict type**: Binary A-vs-B conflict.
- **Evaluable for binary resolution**: YES.
- **Acceptable CRT output**: Resolve to A (correct) or abstain (safe failure).
- **Unacceptable CRT output**: Resolve to B (incorrect).
- **Acceptable baseline output**: Resolve to A (correct) or resolve to B (incorrect).

### 2.2 B_CORRECT (0 episodes)
- **Meaning**: Only Claim B matches ground truth. Claim A is incorrect.
- **Conflict type**: Binary B-vs-A conflict.
- **Evaluable for binary resolution**: YES.
- **Acceptable CRT output**: Resolve to B (correct) or abstain (safe failure).
- **Note**: No episodes of this type in current dataset.

### 2.3 BOTH_TRUE (25 episodes)
- **Meaning**: Both Claim A and Claim B match ground truth. They are IDENTICAL values from different sources.
- **Conflict type**: NOT A CONFLICT — source agreement, not disagreement.
- **Evaluable for binary resolution**: NO — this is not a binary classification problem.
- **Acceptable CRT output**: Either A or B (both trivially correct). Abstention is also acceptable but unnecessary.
- **Critical issue**: These 25 episodes inflate all methods' accuracy by 25 trivially-correct resolutions.
- **Recommendation**: EXCLUDE from conflict-resolution accuracy. Report separately as "agreement episodes."

### 2.4 BOTH_FALSE (69 episodes)
- **Meaning**: Neither Claim A nor Claim B matches ground truth. The correct answer is a third option not present in the conflict.
- **Conflict type**: Multi-state conflict where both competing claims are wrong.
- **Evaluable for binary resolution**: NO — binary A-vs-B accuracy is undefined when neither option is correct.
- **Acceptable CRT output**: Abstain (correct behavior). Resolving to either A or B is incorrect.
- **Acceptable baseline output**: Any resolution is incorrect. Forced decision is wrong.
- **Critical issue**: This is the most important category for evaluating abstention value. Baselines force 69 incorrect decisions here.

### 2.5 INSUFFICIENT_EVIDENCE (13 episodes)
- **Meaning**: Neither claim matches ground truth, AND available evidence is insufficient to determine the correct answer.
- **Conflict type**: Unresolvable conflict.
- **Evaluable for binary resolution**: NO — no correct resolution exists.
- **Acceptable CRT output**: Abstain (correct behavior).
- **Acceptable baseline output**: Any resolution is speculative.
- **Note**: These 13 episodes should be excluded from resolution accuracy or treated as requiring abstention.

---

## 3. Episode Complexity

| Metric | Count |
|--------|-------|
| Total episodes | 144 |
| 2-source episodes | 96 |
| 3-source episodes | 48 |
| 4-source episodes | 32 |
| Binary conflicts (A_CORRECT + B_CORRECT) | 37 |
| Non-binary episodes (BOTH_TRUE + BOTH_FALSE + INSUFFICIENT_EVIDENCE) | 107 |

---

## 4. Current E015 Metrics — Problem Analysis

### 4.1 How Current Metrics Are Computed

The current E015 evaluation treats all 144 episodes as binary A-vs-B classification problems:

```python
if winner_value in claim_values:
    winner_idx = claim_values.index(winner_value)
    correct = gold["evidence"][winner_idx]["matches_gold"]
else:
    correct = False
```

This means:
- **BOTH_TRUE**: Choosing either A or B is counted as correct (both match gold). ✓
- **BOTH_FALSE**: Choosing either A or B is counted as incorrect (neither matches gold). ✓
- **INSUFFICIENT_EVIDENCE**: Abstention is not counted in resolution accuracy. ✓
- **A_CORRECT**: Choosing A is correct, choosing B is incorrect. ✓

The metric computation is technically correct for each category, BUT the aggregation across categories is problematic.

### 4.2 Identified Issues

| Issue | Severity | Description |
|-------|----------|-------------|
| **I1: BOTH_TRUE inflation** | HIGH | 25 episodes where both claims are identical and correct inflate all methods' accuracy by 25 trivially-correct resolutions. |
| **I2: Binary-only methods perform poorly on A_CORRECT** | HIGH | CRT abstains on ALL 37 binary-correct episodes. LWW/Recency/Trust get 0% accuracy on binary episodes. Only Confidence gets 100%. |
| **I3: BOTH_FALSE dominates** | HIGH | 69 episodes (47.9%) where both claims are wrong. This is the most common case and the hardest for any method. |
| **I4: No B_CORRECT episodes** | LOW | Dataset has 0 pure B-correct episodes, limiting binary evaluation symmetry. |

---

## 5. Corrected Metrics

### 5.1 Metric A: Binary Resolution Accuracy

**Scope**: A_CORRECT + B_CORRECT episodes only (37 episodes).

| Method | Resolved | Correct | Accuracy | Risk |
|--------|----------|---------|----------|------|
| CRT V1 | 0 | 0 | 0.000 | 1.000 |
| LWW | 37 | 0 | 0.000 | 1.000 |
| Recency | 37 | 0 | 0.000 | 1.000 |
| Confidence | 37 | 37 | 1.000 | 0.000 |
| Trust | 37 | 0 | 0.000 | 1.000 |

**Interpretation**: On pure binary conflicts, CRT abstains on 100% of episodes. This is overly conservative — it could resolve 37 episodes correctly but chooses not to. Confidence is the only method that gets binary accuracy right (because it picks the claim with highest evidence score, which correlates with correctness in this dataset).

### 5.2 Metric B: Gold-State Correctness (All 144 Episodes)

**Scope**: All episodes with proper handling of non-binary states.

| Method | Correct | Incorrect | Abstained | Overall Accuracy |
|--------|---------|-----------|-----------|------------------|
| CRT V1 | 25 | 23 | 96 | 0.174 |
| LWW | 38 | 106 | 0 | 0.264 |
| Recency | 38 | 106 | 0 | 0.264 |
| Confidence | 75 | 69 | 0 | 0.521 |
| Trust | 38 | 106 | 0 | 0.264 |

**Note**: This is the current E015 metric. It is technically valid but conflates:
- Trivially-correct BOTH_TRUE resolutions (25 per method)
- Binary A-vs-B accuracy (37 episodes)
- Multi-state BOTH_FALSE handling (69 episodes)
- Abstention on INSUFFICIENT_EVIDENCE (13 episodes)

### 5.3 Metric C: Non-Trivial Resolution Accuracy

**Scope**: Exclude BOTH_TRUE episodes. Evaluate on 119 episodes.

| Method | Resolved | Correct | Accuracy | Risk |
|--------|----------|---------|----------|------|
| CRT V1 | 23 | 0 | 0.000 | 1.000 |
| LWW | 119 | 13 | 0.109 | 0.891 |
| Recency | 119 | 13 | 0.109 | 0.891 |
| Confidence | 119 | 50 | 0.420 | 0.580 |
| Trust | 119 | 13 | 0.109 | 0.891 |

**Interpretation**: When trivially-correct BOTH_TRUE episodes are excluded, CRT's resolution accuracy drops to 0%. This is because:
1. CRT abstains on all 37 A_CORRECT episodes (missed opportunities)
2. CRT resolves 23 episodes that are BOTH_FALSE or INSUFFICIENT_EVIDENCE (all incorrect)

This is a critical finding. CRT's current behavior in the static-trust DEV dataset is:
- **Over-abstaining** on resolvable binary conflicts
- **Under-abstaining** on non-binary conflicts (resolving when it should abstain)

### 5.4 Metric D: Selective Accuracy on Resolved Conflicts

**Scope**: Only episodes where method made a resolution decision.

| Method | Resolved | Correct | Selective Accuracy | Selective Risk |
|--------|----------|---------|-------------------|----------------|
| CRT V1 | 48 | 25 | 0.521 | 0.479 |
| LWW | 144 | 38 | 0.264 | 0.736 |
| Recency | 144 | 38 | 0.264 | 0.736 |
| Confidence | 144 | 75 | 0.521 | 0.479 |
| Trust | 144 | 38 | 0.264 | 0.736 |

This is the current E015 "resolution_accuracy" metric. It shows CRT matches Confidence in selective accuracy but with 67% lower coverage.

---

## 6. Recommendations

### 6.1 Immediate Corrections

1. **Label E015 as PRELIMINARY** and document that gold-label semantics require correction.
2. **Add three new metrics** to E015/E016:
   - Binary resolution accuracy (A_CORRECT + B_CORRECT only)
   - Non-trivial resolution accuracy (exclude BOTH_TRUE)
   - Selective accuracy on resolved conflicts (current metric, renamed)
3. **Exclude BOTH_TRUE episodes** from conflict-resolution accuracy calculations.
4. **Document the 0% binary accuracy** for CRT V1 on the current DEV dataset.

### 6.2 Scientific Interpretation

The current DEV dataset has static trust (T=0.5 for all sources), which causes:
- Near-tied Ψ scores across all claims
- High abstention rates (67%)
- Abstention on both resolvable and unresolvable conflicts

The 52.1% selective accuracy is largely driven by 25 trivially-correct BOTH_TRUE resolutions. When these are excluded, CRT's resolution accuracy is 0% on the remaining 119 episodes.

**This does NOT mean CRT V1 is broken.** It means:
1. The static-trust DEV dataset is not suitable for evaluating resolution accuracy.
2. CRT's abstention behavior is working as designed (abstaining when evidence is insufficient).
3. The dataset needs dynamic trust or more diverse evidence scores to produce discriminable Ψ margins.

### 6.3 Next Steps

1. **Do not delete E015** — mark it as preliminary with corrected semantics.
2. **Run E020** on LLM-generated claims, which will have more diverse R/C/T values.
3. **Request test authorization** to evaluate on the full benchmark with ground truth.
4. **Consider calibration split** for threshold tuning if needed.

---

## 7. Tables

### Table 1: Gold Verdict Semantics

| Gold Verdict | Count | Conflict? | Binary Evaluable | CRT Correct Behavior | Baseline Behavior |
|-------------|-------|-----------|------------------|---------------------|-------------------|
| A_CORRECT | 37 | Yes | Yes | Resolve A or abstain | Force decision (often wrong) |
| B_CORRECT | 0 | Yes | Yes | Resolve B or abstain | Force decision |
| BOTH_TRUE | 25 | No | No | Either (trivial) | Either (trivial) |
| BOTH_FALSE | 69 | Yes | No | Abstain | Force wrong decision |
| INSUFFICIENT_EVIDENCE | 13 | No | No | Abstain | Force speculative decision |

### Table 2: Current vs Corrected Metrics

| Metric | Scope | CRT Accuracy | Baseline Accuracy |
|--------|-------|-------------|-------------------|
| Current E015 | All 144 | 0.521 | 0.264 (avg) |
| Binary only | 37 episodes | 0.000 | 0.000 (avg) |
| Non-trivial | 119 episodes | 0.000 | 0.109 (avg) |
| Selective (resolved) | All resolved | 0.521 | 0.264 (avg) |

### Table 3: BOTH_TRUE Impact

| Method | BOTH_TRUE Correct | % of Total Correct |
|--------|------------------|-------------------|
| CRT V1 | 25 | 100% |
| LWW | 25 | 66% |
| Recency | 25 | 66% |
| Confidence | 25 | 33% |
| Trust | 25 | 66% |

CRT's apparent 52.1% accuracy is entirely due to BOTH_TRUE episodes. Without them, CRT has 0% resolution accuracy on this dataset.

---

## 8. Conclusion

**E015 as currently computed is PRELIMINARY and requires semantic correction.**

The gold labels contain three distinct evaluation scenarios:
1. **Binary conflicts** (A_CORRECT/B_CORRECT) — 37 episodes
2. **Non-conflicts with both claims correct** (BOTH_TRUE) — 25 episodes  
3. **Conflicts with both claims wrong** (BOTH_FALSE) — 69 episodes
4. **Unresolvable conflicts** (INSUFFICIENT_EVIDENCE) — 13 episodes

Aggregating all four into a single "resolution accuracy" metric is misleading because:
- BOTH_TRUE episodes are not conflicts and inflate all methods equally
- BOTH_FALSE episodes make any forced resolution incorrect
- INSUFFICIENT_EVIDENCE episodes have no correct resolution

**Corrected finding**: On the static-trust MSM DEV dataset, CRT V1's selective accuracy (52.1%) is entirely explained by trivially-correct BOTH_TRUE resolutions. On episodes with actual binary or multi-state conflicts, CRT either abstains or resolves incorrectly.

This is consistent with the dataset's design: static trust (T=0.5) produces near-tied Ψ scores, making abstention the default. The dataset is valuable for demonstrating abstention behavior and provenance preservation, but NOT for evaluating resolution accuracy.

**Recommendation**: Use E014–E016 for:
- Demonstrating abstention behavior (67% abstention rate)
- Demonstrating provenance preservation (100% retention)
- Demonstrating determinism (144/144 identical)
- Risk-coverage analysis across thresholds

Do NOT use E014–E016 to claim factual accuracy improvement until:
1. A dataset with discriminable Ψ margins is available, OR
2. Test split evaluation is authorized, OR
3. LLM-generated claims with diverse R/C/T values are evaluated (E020).
