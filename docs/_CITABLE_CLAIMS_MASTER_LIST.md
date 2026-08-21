# Citable Claims Master List — QACC Evaluation Phase

**Date:** 2026-08-21  
**Purpose:** Single source of truth for all claims that may appear in README, docs, or publications.  
**Rule:** Every entry must trace to an already-validated artifact in `results/`. No claim may be added without an artifact pointer. No claim may be softened or omitted because it is unfavorable.

---

## MSM Track — Synthetic Structural

### MSM-1: Theta (θ) Sensitivity
- **Claim:** θ = 0.05 is not near a defensible knee-point. The curve is monotonic; strict accuracy decreases gradually as θ increases. No inflection point or elbow exists in [0.00, 0.10].
- **Data:** Accuracy range 0.6418–0.6441 across all half-life values at θ=0.05; coverage drops linearly with θ.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A1; `A1_theta_sensitivity.json`

### MSM-2: C/T Weight-Ratio Flatness
- **Claim:** The C/T ratio sweep shows a broad, near-flat plateau, not a sharp peak. The formula is robustly indifferent to C/T balance within reasonable bounds. Peak strict accuracy at C/T=0.2 is only 0.0151 above the worst point (C/T=10.0).
- **Data:** Peak 0.6429 (C/T=0.2) vs worst 0.6278 (C/T=10.0); accuracy varies by <1.5 percentage points across full grid.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A2.4; `A2_full_sweep_curve.json`

### MSM-3: C-vs-T Contradiction Reversal
- **Claim:** The "confident but wrong together" explanation for high full_crt/c_only agreement is NOT supported. Joint full_crt/c_only accuracy (0.6943) exceeds t_only accuracy (0.6853) on the exact same episodes (+0.0091 delta).
- **Data:** 1,433 agreed episodes; joint accuracy 995/1,433 vs t_only 982/1,433.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A2.1–A2.3; `A2_c_vs_t_evidence.json`
- **Revised framing:** Do NOT revise the default 1/3-1/3-1/3 weights based on this dataset. The sweep does not provide evidence that a different weighting would improve accuracy meaningfully.

### MSM-4: G-Subweight Structural Flatness
- **Claim:** The current code has NO G-subweight decomposition. EVIDENCE_AUTHORITY is flat per source. C variance comes only from coverage differences (device_log and objective_log have variable missing-day rates).
- **Data:** profile_ltm, daily_self_report, planner: C std=0.0000 (degenerate). device_log std=0.0750, objective_log std=0.0714.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A3; `A3_g_subweight_variance.json`

### MSM-5: Agent-Claim Authority Gap
- **Claim:** Agent-claim cannot compete given the actual R/T distributions observed in this corpus. The 0/3,337 result is empirically robust but NOT arithmetically guaranteed. It holds because favorable conditions (R=1.0, T=1.0) do not occur in this corpus.
- **Data:** 0/3,337 agent wins across all grounded tiers. Max observed R≈0.69, T≈0.86. Real agent Ψ≈1.85 vs document Ψ≈1.96.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A4; `A4_agent_claim_gap.json`
- **Do NOT phrase as:** "agent_claim cannot compete under any circumstances."

### MSM-6: Seed Pooling Does Not Resolve Identifiability
- **Claim:** Pooling 4 seeds (s20260321–s20260324) instead of 1 produced only tighter confidence intervals around the same conclusions. Component identifiability is unchanged (R=0.588→0.588, C=0.570→0.578, T=0.652→0.662). The identifiability ceiling is a structural property of the benchmark generator, not a sampling artifact.
- **Data:** 192 pooled personas, 3,456 units, 8,919 claims. Structural variance identical across seeds.
- **Artifact:** `results/empirical_evaluation/msm/_seed_pooling/00_SEED_POOLING_REPORT.md`

### MSM-7: Component Identifiability (Pooled)
- **Claim:** On pooled 4-seed DEV (3,337 episodes with claims): R identifies in 58.8% of episodes, C in 57.8%, T in 66.2%. T is the most identifiable component.
- **Data:** R=1963/3337, C=1927/3337, T=2209/3337.
- **Artifact:** `results/empirical_evaluation/msm/_seed_pooling/00_SEED_POOLING_REPORT.md` §3.3

### MSM-8: Guardrail Firing Rate
- **Claim:** The high-confidence-untrusted guardrail (C≥0.90, T<0.30) fired 0 times in 1,995 resolved episodes. This is a guardrail-with-rate, not an active guardrail.
- **Data:** 0/1,995 violations.
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` §A6; `A6_guardrail_firing.json`

---

## PHEME Track — Real-World Generalization

### PHEME-1: Underperformance vs Simple Baselines
- **Claim:** crt_v1 strict accuracy (13.0%) underperforms last_write_wins (16.0%), recency_only (16.0%), and majority_independent_source (15.7%) on PHEME TEST. Coverage is also lower (73.7% vs 100% for baselines).
- **Data:** n=1,950 episodes. crt_v1: strict=0.130, coverage=0.737. last_write_wins: strict=0.160, coverage=1.0.
- **Artifact:** `results/empirical_evaluation/pheme_test/PHEME_FINAL_EVALUATION_REPORT.md` §TEST Results

### PHEME-2: Component Discrimination on Real-World Data
- **Claim:** On PHEME, C is the most active component (changes decision in 30.41% of episodes), followed by T (10.36%) and R (7.44%). This contrasts with MSM where R and T were structurally invariant.
- **Data:** R: 145/1950, C: 593/1950, T: 202/1950.
- **Artifact:** `results/empirical_evaluation/pheme_test/PHEME_FINAL_EVALUATION_REPORT.md` §Component Discrimination

### PHEME-3: Pending Conflict in Real-Agents Track
- **Claim:** Concurrent mode produces spurious pending_conflict outcomes on distinct paths due to a middleware race. This was found in the real_agents component evaluation track, not in PHEME provenance artifacts.
- **Data:** 5 occurrences in 3 files (10_REAL_AGENT_VALIDATION_REPORT.json, 11_REAL_AGENT_EVALUATION_REPORT.md, _harness/run_real_eval.py).
- **Artifact:** `results/empirical_evaluation/msm/_sweep_results/B2_pending_conflict_evidence.json`; `results/empirical_evaluation/component_evaluation/real_agents/10_REAL_AGENT_VALIDATION_REPORT.json`

---

## QACC Track — Real-Agent Multi-Provider

### QACC-1: Neutral Trust/Recency Structural Limitation
- **Claim:** QACC provides no real trust/temporal signal. R and T are structurally neutral (0.5) for every claim. Only C = authority_score(source_type) differentiates. This is a property of the dataset, not a bug.
- **Data:** 500 cases, 4,824 sources, all R=0.5, T=0.5.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §0, §3

### QACC-2: Policy Accuracy Table (OpenAI + Ollama Clean Subset)
- **Claim:** On the clean 395-case subset (no groq in competing claims), full_crt resolves 102/495 cases with 8.69% strict accuracy, 42.16% selective accuracy, 20.61% coverage.
- **Data:** full_crt: 102 resolved, 8.69% strict, 42.16% selective, 20.61% coverage. c_only: 103 resolved, 8.69% strict, 41.75% selective.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §3

### QACC-3: OpenAI Win-Share Finding
- **Claim:** In the clean openai+ollama subset (102 resolved cases), openai wins 53/102 (51.96%) vs its 42.85% source share, giving a win/share ratio of 1.21 (Wilson 95% CI [0.424, 0.614]). The effect is driven by longer supported claims (mean 16.53 vs 15.58 tokens), not higher authority or more frequent claims.
- **Data:** openai: 53 wins/599 supported sources; ollama: 49 wins/799 supported sources. Mean authority equal (~0.712).
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §4.2; `RUN/analysis.json` `4_2_resolution_outcomes.clean_subset_openai_ollama`

### QACC-4: Agreement Rate on Measurable Triples
- **Claim:** The original 0/30 agreement figure is unreliable because 23/30 triples had fewer than 2 providers return a parseable claim. On the 7 measurable triples, agreement is 3/7 = 42.86% (Wilson 95% CI [0.158, 0.750]).
- **Data:** GENUINE_AGREEMENT=3, GENUINE_DISAGREEMENT=4, NO_DATA=23.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §4.4; `RUN/analysis.json` `4_4_agreement_reclassified`

### QACC-5: Provider Blindness Verification
- **Claim:** authority_score has zero correlation with provider identity. Re-derived from source_type for 4,823 assertions; 0 mismatches. One authority per distinct source.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §4.3; `RUN/analysis.json` `4_3_provider_blindness`

### QACC-6: Groq Rate-Limit Data Quality Issue
- **Claim:** 84.4% of groq-assigned sources (1,357/1,608) failed with HTTP 429 rate-limit transport errors. Groq's extraction-behavior statistics are not meaningful for this run.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §6; `RUN/analysis.json` `4_1_extraction_behavior.appendix_groq`

---

## Cross-Cutting Findings

### X-1: PHEME vs QACC Complementary Scope
- **Claim:** PHEME and QACC are complementary. PHEME showed V1 collapses to C-only when R and T are invariant (MSM); PHEME tests whether V1 can leverage R, C, and T when they genuinely vary. QACC provides real conflicting web evidence and real extraction via LLM agents, but structurally neutral R/T.
- **Artifact:** `results/empirical_evaluation/pheme_test/PHEME_FINAL_EVALUATION_REPORT.md` §Conclusion; `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §0

### X-2: Anti-Mock / Fail-Closed Verification
- **Claim:** All provider calls are fail-closed on model identity. A mismatch or unavailable provider is a logged failure, never silent substitution. The independent validator passed 48/48 checks against the final analysis.json.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` §2, §5; `research_evaluation/_validator.py`

### X-3: Frozen Assertion Principle
- **Claim:** Each source's extraction happens exactly once and is reused identically across every policy compared downstream. No assertion is regenerated between policy runs.
- **Artifact:** `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/RUN/_assertions_500_multiprovider.jsonl` (single file, reused by all policies)

---

## Unfavorable Findings (Must Not Be Softened)

1. **PHEME underperformance:** crt_v1 (13.0%) loses to last_write_wins (16.0%) and recency_only (16.0%) on real-world rumor verification.
2. **QACC low coverage:** full_crt resolves only 20.61% of cases (102/495). The mechanism is highly abstentious on open-domain conflicting contexts.
3. **MSM flat C/T sensitivity:** The C/T weight ratio is not a high-leverage parameter on MSM. Sweeping it produces <1.5pp accuracy variation.
4. **QACC groq data quality:** 84.4% of groq calls failed transport. No cross-provider comparison can include groq.
5. **Pending conflict:** Concurrent mode produces spurious pending_conflict outcomes on distinct paths (middleware race) in the real_agents track.
6. **No G-subweight decomposition:** The current implementation has no provenance-aware G-component. EVIDENCE_AUTHORITY is flat per source type.
7. **Guardrail inactivity:** The high-confidence-untrusted guardrail fired 0 times in 1,995 resolved episodes. It provides no safety value on current datasets.

---

## Artifact Index

| Artifact | Path |
|----------|------|
| MSM sweep results | `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md` |
| MSM evidence addendum | `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_EVIDENCE_ADDENDUM.md` |
| Seed pooling report | `results/empirical_evaluation/msm/_seed_pooling/00_SEED_POOLING_REPORT.md` |
| PHEME final report | `results/empirical_evaluation/pheme_test/PHEME_FINAL_EVALUATION_REPORT.md` |
| QACC 500-case report | `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md` |
| QACC analysis JSON | `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/RUN/analysis.json` |
| QACC assertions | `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/RUN/_assertions_500_multiprovider.jsonl` |
| MSM sweep JSONs | `results/empirical_evaluation/msm/_sweep_results/A1_theta_sensitivity.json`, `A2_full_sweep_curve.json`, `A2_ct_ratio_sweep.json`, `A4_agent_claim_gap.json`, etc. |
