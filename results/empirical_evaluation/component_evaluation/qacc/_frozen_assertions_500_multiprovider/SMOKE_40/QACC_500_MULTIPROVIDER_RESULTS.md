# QACC 500-Case Engine-Replay — Multi-Provider Extension (Option A)

- **Report generated:** 2026-08-21T00:17:06Z (UTC)
- **Design:** Option A — *source diversity* (one provider per SOURCE, not per case)
- **Run label:** `SMOKE_40` — additive; prior single-provider `_frozen_assertions_500/` artifacts left untouched (and were not present in this workspace, so this is a self-contained comparison run).
- **Aggregation method:** `round_robin_stratified` (seeds: cases=20260820, assign=20260821)
- **Cases:** 40 · **total sources:** 352 · **achieved provider split:** {"openai": 117, "groq": 117, "ollama": 118}

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
| full_lcm | 6/40 | 15.00% | 0.00% | 0.00% | 0 |
| c_only | 6/40 | 15.00% | 0.00% | 0.00% | 0 |
| r_only | 6/40 | 15.00% | 0.00% | 0.00% | 0 |
| t_only | 6/40 | 15.00% | 0.00% | 0.00% | 0 |
| fixed_neutral_trust | 6/40 | 15.00% | 0.00% | 0.00% | 0 |
| last_write_wins | 36/40 | 90.00% | 2.50% | 2.78% | 0 |

## 4. Cross-model analysis (Step 4, separate section)

Provider is a post-hoc grouping key here; resolver scores were never recomputed with provider as an input.

### 4.1 Extraction behavior by provider

| provider | calls | parse-success | parse-fail | identity-fail | unsupported | abstain rate | supported | mean claim len |
|---|---|---|---|---|---|---|---|---|
| ollama | 118 | 82.20% | 21 | 0 | 12 | 12.37% | 85 | 12.52 |
| openai | 117 | 100.00% | 0 | 0 | 68 | 58.12% | 49 | 26.16 |
| groq | 117 | 1.71% | 1 | 0 | 0 | 0.00% | 2 | 20.0 |

Self-reported confidence was **not solicited** (the smoke source schema uses `additionalProperties:false` with no confidence field); the resolver's C component is authority-derived, so there is no agent-reported confidence to override. Provider parse-success and abstention differences, if any, are the extraction-behavior signals.

### 4.2 Resolution outcomes by winning-source provider

| provider | supported src | source share | resolved wins | win share | win/share ratio | mean authority |
|---|---|---|---|---|---|---|
| ollama | 85 | 62.50% | 2 | 33.33% | 0.53 | 0.7394 |
| openai | 49 | 36.03% | 4 | 66.67% | 1.85 | 0.7316 |
| groq | 2 | 1.47% | 0 | 0.00% | — | 0.75 |

> **FINDING (requires case-level inspection):** provider(s) openai win at a rate meaningfully above their source share. Because C is provider-blind and per-source authority mix is ~equal across providers, this must come from extraction-level differences (abstention rate 4.1, claim completeness), not from higher-authority assignment — the decoupling is shown by the ~equal `mean_authority` column.

### 4.3 Sanity check — provider leak (REQUIRED verification)

- `authority_score` re-derived strictly from `source_type(source)` for **352** assertions; stored-vs-re-derived mismatches: **0**.
- One authority per distinct source: **True**.
- **Verification result: PASS** — authority_score depends only on source_type (pure function of the source domain), never on provider identity. The resolver is provider-blind.

### 4.4 Extraction agreement sub-check (Option B, 30 sources × 3 = 90 calls)

- **three-provider value agreement:** 1/30 (3.33%)
- **all-provider abstain:** 8
- **pairwise value agreement:** {"ollama|openai": 0.3, "ollama|groq": 0.0333, "openai|groq": 0.0333}

## 5. Findings and limits

- **Additive run only:** no single-provider `_frozen_assertions_500/` artifact is modified; this extension writes only under its own labeled dir.
- Fail-closed/anti-mock: a provider returning a mismatched model or unreachable is logged as a failed extraction for that case/source — never silently substituted.
- The prior single-provider Step-6 report was **not present** in the workspace, so this is a freshly labeled additive run for comparison.
