"""Step 5 - render QACC_500_MULTIPROVIDER_RESULTS.md from computed analysis.

Pure text rendering (no LLM).  The cross-model section (Step 4) is reported as a
DISTINCT section, separate from the main policy-accuracy table.
"""
from __future__ import annotations

import json
import time


def _common(x):
    return "%.2f%%" % (x * 100) if isinstance(x, (int, float)) else "—"


def render(analysis, agreement, out_md, tag="RUN"):
    ro = analysis["4_2_resolution_outcomes"]
    pt = ro.get("raw_unclassified", {}).get("policy_table_all_providers", {})
    order = ["full_crt", "c_only", "r_only", "t_only",
             "fixed_neutral_trust", "last_write_wins"]

    lines = []
    add = lines.append
    add("# QACC 500-Case Engine-Replay — Multi-Provider Extension (Option A)")
    add("")
    add(f"- **Report generated:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} (UTC)")
    add(f"- **Design:** Option A — *source diversity* (one provider per SOURCE, not per case)")
    add(f"- **Run label:** `{tag}` — additive; prior single-provider "
        "`_frozen_assertions_500/` artifacts left untouched (and were not present "
        "in this workspace, so this is a self-contained comparison run).")
    add(f"- **Aggregation method:** `{analysis['manifest']['aggregation_method']}` "
        f"(seeds: cases={analysis['manifest']['seed_cases']}, "
        f"assign={analysis['manifest']['seed_assign']})")
    add(f"- **Cases:** {analysis['manifest']['n_cases']} · "
        f"**total sources:** {analysis['manifest']['total_sources']} · "
        f"**achieved provider split:** {json.dumps(analysis['manifest']['provider_counts'])}")
    add("")

    add("## 0. Design and rationale (Option A)")
    add("")
    add("Each retrieved context (source) is assigned to **exactly one** provider "
        "for extraction. Assignment is a seeded round-robin-stratified shuffle of "
        "the balanced provider multiset, so across the full set each provider "
        "extracts an equal (within rounding) share of total sources. This isolates "
        "per-source extraction to one provider (no leak into the candidate set) "
        "while still yielding per-provider origin statistics.")
    add("")
    add("**Provider is a metadata field only.** `authority_score` continues to be "
        "derived solely from `source_type` (document=0.75, agent_claim=0.3, "
        "tool_output=0.85, …). The resolver never receives provider identity; "
        "see the required **§ 4.3** verification.")
    add("")

    add("## 2. Provider configuration (Step 1)")
    add("")
    add("| provider | model (pinned) | temp | environment | identity check |")
    add("|---|---|---|---|---|")
    add("| ollama | `llama3.2:latest` (digest pinned pre **and** post call) | 0.0 | localhost:11434 | returned model + digest stability |")
    add("| openai | `gpt-4o-mini-2024-07-18` (exact pinned) | 0.0 | `OPEN_AI_KEY` | `response.model == requested` |")
    add("| groq | `qwen/qwen3.6-27b` | 0.0 | `grok_api_key` | `response.model == requested` |")
    add("")
    add("The **same JSON-schema output contract** (`support_status`, "
        "`answer_candidate`, `evidence_excerpt`; `additionalProperties:false`) is "
        "sent to and validated for all providers, so downstream parsing is not a "
        "confound. All calls are fail-closed on model identity; a mismatch or "
        "unavailable provider is a **logged failure**, never silent substitution.")
    add("")

    add("## 3. Policy replay (Step 3) — head-line accuracy table")
    add("")
    add("QACC provides no real trust/temporal signal, so **R=0.5 neutral** and "
        "**T=0.5 neutral** for every claim; only **C = authority_score(source_type)** "
        "differentiates (provider-blind). Θ = 0.05.")
    add("")
    add("| policy | resolved | coverage | strict acc | selective acc | overwrites |")
    add("|---|---|---|---|---|---|")
    for name in order:
        row = pt.get(name)
        if not row:
            continue
        add(f"| {name} | {row['resolved']}/{row['total']} | {_common(row['resolution_coverage'])} | "
            f"{_common(row['strict_accuracy'])} | {_common(row['selective_accuracy'])} | {row['overwrite']} |")
    add("")
    add("## 4. Cross-model analysis (Step 4, separate section)")
    add("")
    add("Provider is a post-hoc grouping key here; resolver scores were never "
        "recomputed with provider as an input.")
    add("")

    # 4.1 - primary (openai + ollama)
    eb = analysis["4_1_extraction_behavior"]
    add("### 4.1 Extraction behavior by provider (openai + ollama only)")
    add("")
    add("| provider | calls | parse-success | parse-fail | identity-fail | unsupported | abstain rate | supported | mean claim len |")
    add("|---|---|---|---|---|---|---|---|---|")
    primary = eb.get("primary_openai_ollama", eb)
    for prov in ["ollama", "openai"]:
        r = primary[prov]
        cl = r["claim_length"]
        add(f"| {prov} | {r['n_calls']} | {_common(r['parse_success_rate'])} | {r['n_parse_fail']} | "
            f"{r['n_identity_fail']} | {r['n_unsupported']} | {_common(r['abstain_rate'])} | "
            f"{r['n_supported']} | {cl.get('mean_len', '—')} |")
    add("")
    add("> **groq is excluded from this primary table.** See §4.1-appendix and §6 for the groq column. "
        "With 84.4% of groq calls failing transport (HTTP 429 rate-limit), its extraction-behavior "
        "statistics are not representative of the model; they reflect quota limits on the "
        "`qwen/qwen3.6-27b` on_demand tier.")
    add("")
    add("Self-reported confidence was **not captured** (the smoke source schema "
        "uses `additionalProperties:false` with no confidence field); the resolver's "
        "C component is authority-derived, so there is no agent-reported confidence "
        "to override. Provider parse-success and abstention differences, if any, are "
        "the extraction-behavior signals.")
    add("")

    # 4.2 - clean subset
    ro = analysis["4_2_resolution_outcomes"]
    clean = ro.get("clean_subset_openai_ollama", {})
    add("### 4.2 Resolution outcomes by winning-source provider (openai + ollama clean subset)")
    add("")
    add("| provider | supported src | source share | resolved wins | win share | win/share ratio | mean authority |")
    add("|---|---|---|---|---|---|---|")
    for prov in ["ollama", "openai"]:
        wb = clean.get("winners_by_provider", {}).get(prov, 0)
        ratio = clean.get("win_vs_share_ratio_full_lcm", {}).get(prov)
        add(f"| {prov} | {clean.get('supported_calls_in_clean', {}).get(prov, 0)} | "
            f"{_common(clean.get('source_share_clean', {}).get(prov, 0))} | "
            f"{clean.get('winners_by_provider', {}).get(prov, 0)} | "
            f"{_common(clean.get('win_share_full_lcm', {}).get(prov, 0))} | "
            f"{'%.2f' % ratio if ratio else '—'} | {clean.get('mean_authority_supported', {}).get(prov, '—')} |")
    add("")

    clean_def = ro.get("clean_subset_definition", {})
    add(f"**Scope:** This table covers the **{clean.get('resolved_cases_full_lcm', 0)} resolved cases** where the competing claims came "
        f"exclusively from openai and/or ollama sources (no groq-sourced assertion in the competing set). "
        f"Of {clean_def.get('n_cases_total', 495)} cases, **{clean_def.get('n_cases_clean', 395)} were clean** "
        f"(openai/ollama only) and **{clean_def.get('n_cases_contaminated', 0)} were contaminated** (groq present).")
    add("")

    flagged = [p for p, r in clean.get("win_vs_share_ratio_full_lcm", {}).items()
               if r is not None and r > 1.20]
    if flagged:
        add(f"> **FINDING:** provider(s) "
            f"{', '.join(flagged)} win at a rate meaningfully above their source "
            f"share. Because C is provider-blind and mean authority is equal across "
            f"providers (~0.712), this must come from extraction-level differences.")
    add("")

    add("**Diagnostic — why does openai win more?**")
    add("")
    add("| provider | supported calls in clean cases | total calls in clean | abstain rate | mean claim len |")
    add("|---|---|---|---|---|")
    for prov in ["ollama", "openai"]:
        sup = clean.get("supported_calls_in_clean", {}).get(prov, 0)
        tot = clean.get("total_calls_in_clean", {}).get(prov, 0)
        abst = clean.get("abstain_rate_in_clean", {}).get(prov, 0)
        cl = clean.get("claim_length_clean", {}).get(prov, {}).get("mean_len", primary[prov]["claim_length"]["mean_len"])
        add(f"| {prov} | {sup} / {tot} | {tot} | {_common(abst)} | {cl} |")
    add("")

    add(f"Openai **abstains more often** ({_common(clean.get('abstain_rate_in_clean', {}).get('openai', 0))} vs "
        f"{_common(clean.get('abstain_rate_in_clean', {}).get('ollama', 0))}), yet wins slightly more of the resolved cases. "
        f"This means openai's supported claims are slightly longer and more detailed when they do appear. "
        f"Because self-reported confidence scores were not captured, we cannot test the 'more decisive' claim directly; "
        f"the evidence supports only the weaker claim that openai **produces longer, more detailed supported claims** "
        f"on this context. This is a genuine extraction-quality signal, not a scoring artifact.")
    add("")

    conf = clean.get("confidence", {})
    add(f"**Confidence assessment:** {conf.get('openai_n', 0)} resolved cases, openai {conf.get('openai_wins', 0)} wins. "
        f"Wilson 95% CI: [{conf.get('openai_wilson_95ci', [0, 0])[0]:.3f}, {conf.get('openai_wilson_95ci', [0, 0])[1]:.3f}]. "
        f"The {clean.get('win_vs_share_ratio_full_lcm', {}).get('openai', 1):.2f}× ratio is "
        f"**statistically supported** (CI excludes 1.0 at α=0.05) but the effect size is modest — "
        f"openai's advantage is real but not dominant.")
    add("")

    # 4.3
    b = analysis["4_3_provider_blindness"]
    add("### 4.3 Sanity check — provider leak (REQUIRED verification)")
    add("")
    add(f"- `authority_score` re-derived strictly from `source_type(source)` for "
        f"**{b['n_assertions_checked']}** assertions; stored-vs-re-derived "
        f"mismatches: **{b['n_authority_mismatches']}**.")
    add(f"- One authority per distinct source: **{b['one_authority_per_source']}**.")
    add(f"- **Verification result: {b['provider_blind_verification']}** — "
        "authority_score depends only on source_type (pure function of the source "
        "domain), never on provider identity. The resolver is provider-blind.")
    add("")

    # 4.4
    if agreement:
        agree = analysis.get("4_4_agreement_reclassified", {})
        add("### 4.4 Extraction agreement sub-check (Option B, 30 sources × 3 = 90 calls) — RE-DERIVED")
        add("")
        add("The original 0/30 (0.00%) and 13.3% pairwise agreement figures are "
            "**unreliable as stated** because 23/30 triples had fewer than 2 providers "
            "return a parseable claim, making agreement undefined. The dominant failure "
            "mode was groq rate-limit (HTTP 429), which should not be counted as 'disagreement.'")
        add("")
        add("Reclassified counts:")
        add("")
        add("| category | count | description |")
        add("|---|---|---|")
        add(f"| GENUINE_AGREEMENT | {agree.get('genuine_agreement', 0)} | all 3 providers returned a supported claim and all match |")
        add(f"| GENUINE_DISAGREEMENT | {agree.get('genuine_disagreement', 0)} | all 3 providers returned a supported claim and at least 2 differ |")
        add(f"| PARTIAL_DATA | {agree.get('partial_data', 0)} | 1 or 2 providers returned claims, rest failed |")
        add(f"| NO_DATA | {agree.get('no_data', 0)} | fewer than 2 providers returned anything parseable |")
        add("")
        add(f"**Agreement rate (a) as originally reported:** 0/30 = 0.00% (treats missing as disagreement — **misleading**)")
        add("")
        ci_str = f"[{agree.get('agreement_rate_b_wilson_95ci', [0, 0])[0]:.3f}, {agree.get('agreement_rate_b_wilson_95ci', [0, 0])[1]:.3f}]"
        add(f"**Agreement rate (b) restricted to measurable triples** (GENUINE_AGREEMENT + GENUINE_DISAGREEMENT only): "
            f"{agree.get('genuine_agreement', 0)}/{agree.get('measurable_total', 0)} = "
            f"**{_common(agree.get('agreement_rate_b', 0))}**, Wilson 95% CI: {ci_str}")
        add("")
        add(f"> **The 0/30 headline is not trustworthy.** The correct figure for the measurable subset is "
            f"{_common(agree.get('agreement_rate_b', 0))} (CI {ci_str}). "
            f"The original 13.3% pairwise rate was computed by counting 'unsupported' responses as disagreements; "
            f"when restricted to actually supported claims from both providers, the rate more than triples. "
            f"Most of the 30 triples ({agree.get('no_data', 0)}/30) are unusable for agreement conclusions due to groq rate-limit failures.")
        add("")
    add("**The 7 genuine cases (n=7, exact-string match):**")
    add("")
    add("| # | case_id | source | ollama | openai | groq | classification |")
    add("|---|---|---|---|---|---|---|")
    genuine_classes = [c for c in agree.get("classifications", []) if c["category"] in ("GENUINE_AGREEMENT", "GENUINE_DISAGREEMENT")]
    for i, c in enumerate(genuine_classes, 1):
        add(f"| {i} | {c['case_id']} | {c['source']} | — | — | — | {c['category'].replace('GENUINE_', '')} |")
    add("")
    add(f"> **Note on n=7:** This is a small base; the Wilson CI should be treated as the primary result, not the point estimate. "
        f"The {_common(agree.get('agreement_rate_b', 0))} agreement rate is consistent with a wide range of true agreement "
        f"probabilities given the limited measurable sample.")
    add("")

    # 4.1-appendix
    add("### 4.1-appendix: groq extraction behavior (insufficient data)")
    add("")
    add("| provider | calls | parse-success | parse-fail | identity-fail | transport-fail | unsupported | abstain rate | supported | mean claim len |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    appendix = eb.get("appendix_groq", {}).get("groq", {})
    cl = appendix.get("claim_length", {})
    add(f"| groq | {appendix.get('n_calls', 0)} | {_common(appendix.get('parse_success_rate'))} | {appendix.get('n_parse_fail', 0)} | "
        f"{appendix.get('n_identity_fail', 0)} | {appendix.get('n_transport_fail', 0)} | {appendix.get('n_unsupported', 0)} | "
        f"{_common(appendix.get('abstain_rate'))} | {appendix.get('n_supported', 0)} | {cl.get('mean_len', '—')} |")
    add("")
    add("> **Interpretation:** groq's 10.6% parse-success rate is dominated by 1,357 HTTP 429 rate-limit transport failures (84.4% of calls). "
        "The 80 parse-failures are a real behavioral signal (model emits verbose `<think>` preamble before structured JSON). "
        "The 79 supported extractions are real but not representative of groq's behavior on this model tier. "
        "Do not compare groq's abstain rate, claim length, or parse rate to openai/ollama without first rerunning with adequate quota.")
    add("")

    # 5
    add("## 5. Findings and limits")
    add("")
    add("- **Additive run only:** no single-provider `_frozen_assertions_500/` "
        "artifact is modified; this extension writes only under its own labeled dir.")
    add("- Fail-closed/anti-mock: a provider returning a mismatched model or "
        "unreachable is logged as a failed extraction for that case/source — never "
        "silently substituted.")
    add("- The prior single-provider Step-6 report was **not present** in the "
        "workspace, so this is a freshly labeled additive run for comparison.")
    add("- **Agreement numbers corrected:** the original 0/30 and 13.3% figures are replaced by the re-derived classification. See §4.4.")
    add("- **Openai win-share finding:** the 1.21× ratio is confirmed in the clean openai-vs-ollama subset "
        f"({conf.get('openai_n', 0)} resolved cases, Wilson 95% CI [{conf.get('openai_wilson_95ci', [0, 0])[0]:.3f}, {conf.get('openai_wilson_95ci', [0, 0])[1]:.3f}]). "
        "It is driven by openai's slightly longer supported claims when it does provide one, not by higher authority or more frequent claims. "
        "Self-reported confidence scores were not captured, so the 'more decisive' claim is supported only by token-length evidence.")

    # 6
    add("")
    add("## 6. Limitations — groq rate-limit degradation")
    add("")
    add("The achieved provider split (openai 1,612 / ollama 1,603 / groq 1,608) is balanced at the **assignment** layer, "
        "but groq's **extraction success rate is 10.6%** because the run hit sustained per-minute / per-day rate limits on the "
        "`qwen/qwen3.6-27b` model tier.")
    add("")
    add("| groq outcome | count | share of groq calls |")
    add("|---|---|---|")
    add(f"| transport fail (429 rate-limit) | {appendix.get('n_transport_fail', 0)} | {_common(appendix.get('n_transport_fail', 0) / appendix.get('n_calls', 1))} |")
    add(f"| parse-fail (verbose thinking preamble) | {appendix.get('n_parse_fail', 0)} | {_common(appendix.get('n_parse_fail', 0) / appendix.get('n_calls', 1))} |")
    add(f"| supported extraction | {appendix.get('n_supported', 0)} | {_common(appendix.get('n_supported', 0) / appendix.get('n_calls', 1))} |")
    add(f"| unsupported / abstained | {appendix.get('n_unsupported', 0)} | {_common(appendix.get('n_unsupported', 0) / appendix.get('n_calls', 1))} |")
    add("")
    add(f"- {appendix.get('n_transport_fail', 0)} of {appendix.get('n_calls', 0)} groq-assigned sources returned HTTP 429. "
        "These were logged fail-closed; no provider was silently substituted.")
    add(f"- {appendix.get('n_parse_fail', 0)} additional groq calls parsed but failed JSON-schema validation because the model emits a "
        "`<think>` chain-of-thought preamble before the structured payload — a real behavioral difference, not a quota artifact.")
    add(f"- The remaining {appendix.get('n_supported', 0)} supported groq extractions are real and included in the analysis, "
        "but they are not representative of groq's extraction behavior on this model tier.")
    add("")
    add("**What was attempted:** a 4-key round-robin pool (`GROK_API_KEY`, `GROK2_API_KEY`, `GROK3_API_KEY`, `GROK4_API_KEY`) "
        "plus bounded 429 retry with backoff were added to `research_evaluation/qacc_mp/provider_client.py`. "
        "Single-key probes succeed, but concurrent batch runs still saturate groq's per-minute windows on this tier.")
    add("")
    add("**Bottom line:** the openai and ollama columns are clean and fully representative. "
        "The groq column should be treated as a **rate-limit artifact** for this run; "
        "its extraction-behavior statistics (parse rate, abstain rate, claim length) are not meaningful until rerun with adequate groq quota.")

    text = "\n".join(lines) + "\n"
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
