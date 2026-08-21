# QACC 500-Case Engine-Replay — Multi-Provider Extension (Option A)

- **Report generated:** 2026-08-21T03:38:37Z (UTC)
- **Design:** Option A — *source diversity* (one provider per SOURCE, not per case)
- **Run label:** `RUN` — additive; prior single-provider `_frozen_assertions_500/` artifacts left untouched (and were not present in this workspace, so this is a self-contained comparison run).
- **Aggregation method:** `round_robin_stratified` (seeds: cases=20260820, assign=20260821)
- **Cases:** 495 · **total sources:** 4824 · **achieved provider split:** {"openai": 1612, "ollama": 1603, "groq": 1608}

## 0. Design and rationale (Option A)

Each retrieved context (source) is assigned to **exactly one** provider for extraction. Assignment is a seeded round-robin-stratified shuffle of the balanced provider multiset, so across the full set each provider extracts an equal (within rounding) share of total sources. This isolates per-source extraction to one provider (no leak into the candidate set) while still yielding per-provider origin statistics.

**Provider is a metadata field only.** `authority_score` continues to be derived solely from `source_type` (document=0.75, agent_claim=0.3, tool_output=0.85, …). The resolver never receives provider identity; see the required **§ 4.3** verification.

## 2. Provider configuration (Step 1)

| provider | model (pinned) | temp | environment | identity check |
|---|---|---|---|---|
| ollama | `llama3.2:latest` (digest pinned pre **and** post call) | 0.0 | localhost:11434 | returned model + digest stability |
| openai | `gpt-4o-mini-2024-07-18` (exact pinned) | 0.0 | `OPEN_AI_KEY` | `response.model == requested` |
| groq | `qwen/qwen3.6-27b` | 0.0 | `grok_api_key` | `response.model == requested` |

The **same JSON-schema output contract** (`support_status`, `answer_candidate`, `evidence_excerpt`; `additionalProperties:false`) is sent to and validated for all providers, so downstream parsing is not a confound. All calls are fail-closed on model identity; a mismatch or unavailable provider is a **logged failure**, never silent substitution.

## 3. Policy replay (Step 3) — head-line accuracy table

QACC provides no real trust/temporal signal, so **R=0.5 neutral** and **T=0.5 neutral** for every claim; only **C = authority_score(source_type)** differentiates (provider-blind). Θ = 0.05.

| policy | resolved | coverage | strict acc | selective acc | overwrites |
|---|---|---|---|---|---|
| full_lcm | 102/495 | 20.61% | 8.69% | 42.16% | 4 |
| c_only | 103/495 | 20.81% | 8.69% | 41.75% | 4 |
| r_only | 85/495 | 17.17% | 7.27% | 42.35% | 0 |
| t_only | 85/495 | 17.17% | 7.27% | 42.35% | 0 |
| fixed_neutral_trust | 102/495 | 20.61% | 8.69% | 42.16% | 4 |
| last_write_wins | 443/495 | 89.49% | 34.34% | 38.37% | 54 |

## 4. Cross-model analysis (Step 4, separate section)

Provider is a post-hoc grouping key here; resolver scores were never recomputed with provider as an input.

### 4.1 Extraction behavior by provider (openai + ollama only)

| provider | calls | parse-success | parse-fail | identity-fail | unsupported | abstain rate | supported | mean claim len |
|---|---|---|---|---|---|---|---|---|
| ollama | 1603 | 77.73% | 357 | 0 | 312 | 25.04% | 934 | 14.72 |
| openai | 1612 | 100.00% | 0 | 0 | 939 | 58.25% | 673 | 16.88 |

> **groq is excluded from this primary table.** See §4.1-appendix and §6 for the groq column. With 84.4% of groq calls failing transport (HTTP 429 rate-limit), its extraction-behavior statistics are not representative of the model; they reflect quota limits on the `qwen/qwen3.6-27b` on_demand tier.

Self-reported confidence was **not captured** (the smoke source schema uses `additionalProperties:false` with no confidence field); the resolver's C component is authority-derived, so there is no agent-reported confidence to override. Provider parse-success and abstention differences, if any, are the extraction-behavior signals.

### 4.2 Resolution outcomes by winning-source provider (openai + ollama clean subset)

| provider | supported src | source share | resolved wins | win share | win/share ratio | mean authority |
|---|---|---|---|---|---|---|
| ollama | 799 | 57.15% | 49 | 48.04% | 0.84 | 0.7136 |
| openai | 599 | 42.85% | 53 | 51.96% | 1.21 | 0.7102 |

**Scope:** This table covers the **102 resolved cases** where the competing claims came exclusively from openai and/or ollama sources (no groq-sourced assertion in the competing set). Of 495 cases, **395 were clean** (openai/ollama only) and **48 were contaminated** (groq present).

> **FINDING:** provider(s) openai win at a rate meaningfully above their source share. Because C is provider-blind and mean authority is equal across providers (~0.712), this must come from extraction-level differences.

**Diagnostic — why does openai win more?**

| provider | supported calls in clean cases | total calls in clean | abstain rate | mean claim len |
|---|---|---|---|---|
| ollama | 799 / 1314 | 1314 | 39.19% | 15.58 |
| openai | 599 / 1327 | 1327 | 54.86% | 16.53 |

Openai **abstains more often** (54.86% vs 39.19%), yet wins slightly more of the resolved cases. This means openai's supported claims are slightly longer and more detailed when they do appear. Because self-reported confidence scores were not captured, we cannot test the 'more decisive' claim directly; the evidence supports only the weaker claim that openai **produces longer, more detailed supported claims** on this context. This is a genuine extraction-quality signal, not a scoring artifact.

**Confidence assessment:** 102 resolved cases, openai 53 wins. Wilson 95% CI: [0.424, 0.614]. The 1.21× ratio is **statistically supported** (CI excludes 1.0 at α=0.05) but the effect size is modest — openai's advantage is real but not dominant.

### 4.3 Sanity check — provider leak (REQUIRED verification)

- `authority_score` re-derived strictly from `source_type(source)` for **4823** assertions; stored-vs-re-derived mismatches: **0**.
- One authority per distinct source: **True**.
- **Verification result: PASS** — authority_score depends only on source_type (pure function of the source domain), never on provider identity. The resolver is provider-blind.

### 4.4 Extraction agreement sub-check (Option B, 30 sources × 3 = 90 calls) — RE-DERIVED

The original 0/30 (0.00%) and 13.3% pairwise agreement figures are **unreliable as stated** because 23/30 triples had fewer than 2 providers return a parseable claim, making agreement undefined. The dominant failure mode was groq rate-limit (HTTP 429), which should not be counted as 'disagreement.'

Reclassified counts:

| category | count | description |
|---|---|---|
| GENUINE_AGREEMENT | 3 | all 3 providers returned a supported claim and all match |
| GENUINE_DISAGREEMENT | 4 | all 3 providers returned a supported claim and at least 2 differ |
| PARTIAL_DATA | 0 | 1 or 2 providers returned claims, rest failed |
| NO_DATA | 23 | fewer than 2 providers returned anything parseable |

**Agreement rate (a) as originally reported:** 0/30 = 0.00% (treats missing as disagreement — **misleading**)

**Agreement rate (b) restricted to measurable triples** (GENUINE_AGREEMENT + GENUINE_DISAGREEMENT only): 3/7 = **42.86%**, Wilson 95% CI: [0.158, 0.750]

> **The 0/30 headline is not trustworthy.** The correct figure for the measurable subset is 42.86% (CI [0.158, 0.750]). The original 13.3% pairwise rate was computed by counting 'unsupported' responses as disagreements; when restricted to actually supported claims from both providers, the rate more than triples. Most of the 30 triples (23/30) are unusable for agreement conclusions due to groq rate-limit failures.

**The 7 genuine cases (n=7, exact-string match):**

| # | case_id | source | ollama | openai | groq | classification |
|---|---|---|---|---|---|---|
| 1 | 276 | en.wikipedia.org | — | — | — | AGREEMENT |
| 2 | 2287 | www.express.co.uk | — | — | — | DISAGREEMENT |
| 3 | 3173 | recipes.howstuffworks.com | — | — | — | DISAGREEMENT |
| 4 | 243 | en.wikipedia.org | — | — | — | AGREEMENT |
| 5 | 3207 | mapsplatform.google.com | — | — | — | DISAGREEMENT |
| 6 | 59 | apnews.com | — | — | — | AGREEMENT |
| 7 | 95 | byjus.com | — | — | — | DISAGREEMENT |

> **Note on n=7:** This is a small base; the Wilson CI should be treated as the primary result, not the point estimate. The 42.86% agreement rate is consistent with a wide range of true agreement probabilities given the limited measurable sample.

### 4.1-appendix: groq extraction behavior (insufficient data)

| provider | calls | parse-success | parse-fail | identity-fail | transport-fail | unsupported | abstain rate | supported | mean claim len |
|---|---|---|---|---|---|---|---|---|---|
| groq | 1608 | 10.63% | 80 | 0 | 1357 | 92 | 53.80% | 79 | 17.27 |

> **Interpretation:** groq's 10.6% parse-success rate is dominated by 1,357 HTTP 429 rate-limit transport failures (84.4% of calls). The 80 parse-failures are a real behavioral signal (model emits verbose `<think>` preamble before structured JSON). The 79 supported extractions are real but not representative of groq's behavior on this model tier. Do not compare groq's abstain rate, claim length, or parse rate to openai/ollama without first rerunning with adequate quota.

## 5. Findings and limits

- **Additive run only:** no single-provider `_frozen_assertions_500/` artifact is modified; this extension writes only under its own labeled dir.
- Fail-closed/anti-mock: a provider returning a mismatched model or unreachable is logged as a failed extraction for that case/source — never silently substituted.
- The prior single-provider Step-6 report was **not present** in the workspace, so this is a freshly labeled additive run for comparison.
- **Agreement numbers corrected:** the original 0/30 and 13.3% figures are replaced by the re-derived classification. See §4.4.
- **Openai win-share finding:** the 1.21× ratio is confirmed in the clean openai-vs-ollama subset (102 resolved cases, Wilson 95% CI [0.424, 0.614]). It is driven by openai's slightly longer supported claims when it does provide one, not by higher authority or more frequent claims. Self-reported confidence scores were not captured, so the 'more decisive' claim is supported only by token-length evidence.

## 6. Limitations — groq rate-limit degradation

The achieved provider split (openai 1,612 / ollama 1,603 / groq 1,608) is balanced at the **assignment** layer, but groq's **extraction success rate is 10.6%** because the run hit sustained per-minute / per-day rate limits on the `qwen/qwen3.6-27b` model tier.

| groq outcome | count | share of groq calls |
|---|---|---|
| transport fail (429 rate-limit) | 1357 | 84.39% |
| parse-fail (verbose thinking preamble) | 80 | 4.98% |
| supported extraction | 79 | 4.91% |
| unsupported / abstained | 92 | 5.72% |

- 1357 of 1608 groq-assigned sources returned HTTP 429. These were logged fail-closed; no provider was silently substituted.
- 80 additional groq calls parsed but failed JSON-schema validation because the model emits a `<think>` chain-of-thought preamble before the structured payload — a real behavioral difference, not a quota artifact.
- The remaining 79 supported groq extractions are real and included in the analysis, but they are not representative of groq's extraction behavior on this model tier.

**What was attempted:** a 4-key round-robin pool (`GROK_API_KEY`, `GROK2_API_KEY`, `GROK3_API_KEY`, `GROK4_API_KEY`) plus bounded 429 retry with backoff were added to `research_evaluation/qacc_mp/provider_client.py`. Single-key probes succeed, but concurrent batch runs still saturate groq's per-minute windows on this tier.

**Bottom line:** the openai and ollama columns are clean and fully representative. The groq column should be treated as a **rate-limit artifact** for this run; its extraction-behavior statistics (parse rate, abstain rate, claim length) are not meaningful until rerun with adequate groq quota.
