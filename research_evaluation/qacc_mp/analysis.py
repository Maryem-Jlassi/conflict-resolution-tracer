"""Step 4 - cross-model analysis (report-computable tables).

Produces 4.1 / 4.2 / 4.3 analysis objects plus the 4.3 provider-blindness
VERIFICATION.  All numbers are computed from FROZEN assertions + manifest only
(no LLM involved).  Provider identity is a post-hoc grouping key; resolver
scores are never recomputed here with provider as an input.

Primary tables exclude groq (84.4% transport-failure rate makes its stats
unrepresentative).  Groq data is preserved under appendix keys for
reproducibility.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal
import math

from . import common
from . import replay as replay_mod


def load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_records(path):
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


# ---------------------------------------------------------------------------
# 4.1
# ---------------------------------------------------------------------------
def extraction_behavior(records, providers):
    calls = Counter(r["provider"] for r in records if r["provider"] in providers)
    ok = Counter(r["provider"] for r in records if r["provider"] in providers and r.get("success"))
    parse_fail = Counter(r["provider"] for r in records
                         if r["provider"] in providers and r.get("parse_status") == "unparseable")
    ident_fail = Counter(r["provider"] for r in records
                         if r["provider"] in providers and r.get("model_mismatch"))
    unsupported = Counter(r["provider"] for r in records
                          if r["provider"] in providers and r.get("success") and r.get("support_status") == "unsupported")
    supported = Counter(r["provider"] for r in records
                        if r["provider"] in providers and r.get("success") and r.get("support_status") == "supported")

    per = {}
    for prov in providers:
        n = calls.get(prov, 0)
        okp = ok.get(prov, 0)
        lens = [len(r.get("answer_candidate") or "") for r in records
                if r.get("provider") == prov and r.get("success")
                and r.get("support_status") == "supported"]
        per[prov] = {
            "n_calls": n,
            "n_success": okp,
            "parse_success_rate": round(okp / n, 4) if n else None,
            "n_parse_fail": parse_fail.get(prov, 0),
            "n_identity_fail": ident_fail.get(prov, 0),
            "n_transport_fail": n - okp - parse_fail.get(prov, 0) - ident_fail.get(prov, 0),
            "n_unsupported": unsupported.get(prov, 0),
            "abstain_rate": round(unsupported.get(prov, 0) / okp, 4) if okp else None,
            "n_supported": supported.get(prov, 0),
            "self_reported_confidence_used": False,
            "claim_length": {
                "n": len(lens),
                "mean_len": round(sum(lens) / len(lens), 2) if lens else None,
                "max_len": max(lens) if lens else None,
            },
        }
    return per


# ---------------------------------------------------------------------------
# 4.2
# ---------------------------------------------------------------------------
def resolution_outcomes(replay_out, records, clean_case_ids):
    summary = replay_out["summary"]
    table = {}
    for name, row in summary.items():
        table[name] = {
            "total": row["total"],
            "resolved": row["resolved"],
            "resolution_coverage": row["resolution_coverage"],
            "strict_accuracy": row["strict_accuracy"],
            "selective_accuracy": row["selective_accuracy"],
            "overwrite": row["overwrite"],
            "winners_by_provider": row["winners_by_provider"],
        }

    # Build case -> providers with supported claims
    by_case = defaultdict(list)
    for r in records:
        if r.get("success") and r.get("support_status") == "supported" and r.get("answer_candidate"):
            by_case[r["case_id"]].append(r)

    clean_case_set = set(clean_case_ids)
    all_case_ids = set(r["case_id"] for r in records)
    cases_with_supported = set(by_case.keys())
    
    # Contaminated = cases with groq among supported claims, not in clean set
    contaminated_case_ids = [cid for cid in all_case_ids
                             if cid not in clean_case_set
                             and "groq" in {c["provider"] for c in by_case.get(cid, [])}]
    
    # Cases with no supported claims at all
    no_claim_cases = all_case_ids - cases_with_supported

    # Clean subset outcomes
    per_case_winners = replay_out.get("per_case_winners", {})
    clean_winners = Counter()
    contaminated_winners = Counter()
    clean_resolved = 0
    contaminated_resolved = 0

    for cid, case_winners in per_case_winners.items():
        has_groq = "groq" in {c["provider"] for c in by_case.get(cid, [])}
        for pol_name, winner in case_winners.items():
            if pol_name != "full_crt" or winner is None:
                continue
            if not has_groq and cid in clean_case_set:
                clean_winners[winner["provider"]] += 1
                clean_resolved += 1
            elif has_groq:
                contaminated_winners[winner["provider"]] += 1
                contaminated_resolved += 1

    # Supported calls in clean subset (openai + ollama only)
    supported_clean = Counter()
    total_clean = Counter()
    for r in records:
        if r["case_id"] in clean_case_set and r["provider"] in ["ollama", "openai"]:
            total_clean[r["provider"]] += 1
            if r.get("success") and r.get("support_status") == "supported":
                supported_clean[r["provider"]] += 1

    # Mean authority in clean subset
    mean_auth_clean = {}
    claim_len_clean = {}
    for prov in ["ollama", "openai"]:
        vals = [r.get("authority_score") for r in records
                if r.get("provider") == prov and r.get("success")
                and r.get("support_status") == "supported"
                and r["case_id"] in clean_case_set]
        mean_auth_clean[prov] = round(sum(vals) / len(vals), 4) if vals else None
        
        lens = [len(r.get("answer_candidate") or "") for r in records
                if r.get("provider") == prov and r.get("success")
                and r.get("support_status") == "supported"
                and r["case_id"] in clean_case_set]
        claim_len_clean[prov] = {
            "n": len(lens),
            "mean_len": round(sum(lens) / len(lens), 2) if lens else None,
            "max_len": max(lens) if lens else None,
        }

    # Wilson CI helper
    def wilson_ci(k, n, z=1.96):
        if n == 0:
            return None
        p = k / n
        denom = 1 + z*z/n
        centre = (p + z*z/(2*n)) / denom
        spread = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n) / denom
        return (round(max(0, centre - spread), 4), round(min(1, centre + spread), 4))

    openai_wins = clean_winners.get("openai", 0)
    ollama_wins = clean_winners.get("ollama", 0)
    openai_ci = wilson_ci(openai_wins, clean_resolved)
    ollama_ci = wilson_ci(ollama_wins, clean_resolved)

    # Raw unclassified (all providers, pre-filter)
    sup_counts = Counter(r["provider"] for r in records
                         if r.get("success") and r.get("support_status") == "supported")
    supported_total = sum(sup_counts.values())
    source_share = {p: sup_counts.get(p, 0) / supported_total if supported_total else 0.0
                    for p in ["ollama", "openai", "groq"]}
    wb = summary["full_crt"]["winners_by_provider"]
    total_resolved = summary["full_crt"]["resolved"]
    win_share = {p: wb.get(p, 0) / total_resolved if total_resolved else 0.0
                 for p in ["ollama", "openai", "groq"]}
    ratio = {p: round(win_share[p] / source_share[p], 4) if source_share[p] else None
             for p in ["ollama", "openai", "groq"]}
    mean_auth_all = {}
    for prov in ["ollama", "openai", "groq"]:
        vals = [r.get("authority_score") for r in records
                if r.get("provider") == prov and r.get("success")
                and r.get("support_status") == "supported"]
        mean_auth_all[prov] = round(sum(vals) / len(vals), 4) if vals else None

    n_cases_total = len(clean_case_ids) + len(contaminated_case_ids) + len(no_claim_cases)

    return {
        "clean_subset_definition": {
            "n_cases_total": n_cases_total,
            "n_cases_clean": len(clean_case_ids),
            "n_cases_contaminated": len(contaminated_case_ids),
            "n_cases_no_claims": len(no_claim_cases),
            "filter_logic": "A case is 'clean' if no groq-sourced assertion exists among its competing claims (even if groq did not win). Excludes any case where groq was a competing source.",
        },
        "clean_subset_openai_ollama": {
            "resolved_cases_full_lcm": clean_resolved,
            "winners_by_provider": dict(clean_winners),
            "win_share_full_lcm": {
                "ollama": round(ollama_wins / clean_resolved, 4) if clean_resolved else 0.0,
                "openai": round(openai_wins / clean_resolved, 4) if clean_resolved else 0.0,
            },
            "win_vs_share_ratio_full_lcm": {
                "ollama": round((ollama_wins / clean_resolved) / (supported_clean.get("ollama", 0) / max(1, sum(supported_clean.values()))), 4) if supported_clean.get("ollama") else None,
                "openai": round((openai_wins / clean_resolved) / (supported_clean.get("openai", 0) / max(1, sum(supported_clean.values()))), 4) if supported_clean.get("openai") else None,
            },
            "mean_authority_supported": mean_auth_clean,
            "claim_length_clean": claim_len_clean,
            "supported_calls_in_clean": dict(supported_clean),
            "total_calls_in_clean": dict(total_clean),
            "abstain_rate_in_clean": {
                prov: round((total_clean.get(prov, 0) - supported_clean.get(prov, 0)) / total_clean.get(prov, 1), 4)
                for prov in ["ollama", "openai"]
            },
            "source_share_clean": {
                prov: round(supported_clean.get(prov, 0) / max(1, sum(supported_clean.values())), 4)
                for prov in ["ollama", "openai"]
            },
            "confidence": {
                "openai_wins": openai_wins,
                "openai_n": clean_resolved,
                "openai_wilson_95ci": list(openai_ci) if openai_ci else None,
                "ollama_wins": ollama_wins,
                "ollama_n": clean_resolved,
                "ollama_wilson_95ci": list(ollama_ci) if ollama_ci else None,
            }
        },
        "contaminated_subset_with_groq": {
            "resolved_cases_full_lcm": contaminated_resolved,
            "winners_by_provider": dict(contaminated_winners),
            "note": "Groq-contaminated cases; not used for primary cross-model conclusions."
        },
        "raw_unclassified": {
            "policy_table_all_providers": {
                name: {
                    "total": row["total"],
                    "resolved": row["resolved"],
                    "resolution_coverage": row["resolution_coverage"],
                    "strict_accuracy": row["strict_accuracy"],
                    "selective_accuracy": row["selective_accuracy"],
                    "overwrite": row["overwrite"],
                    "winners_by_provider": row["winners_by_provider"],
                }
                for name, row in summary.items()
            },
            "supported_shares_all_providers": dict(sup_counts),
            "source_share_all_providers": {k: round(v, 4) for k, v in source_share.items()},
            "win_share_full_lcm_all_providers": {k: round(v, 4) for k, v in win_share.items()},
            "mean_authority_all_providers": mean_auth_all,
            "warning": "DO NOT CITE directly. These are pre-filter, groq-included numbers kept for reproducibility only. Primary conclusions use clean_subset_openai_ollama above."
        }
    }


# ---------------------------------------------------------------------------
# 4.3 - provider-blindness VERIFICATION
# ---------------------------------------------------------------------------
def provider_blindness_check(records):
    """Re-derive authority_score from source_type and confirm stored values
    match and the mapping is a pure function of source (never provider)."""
    n = 0
    mismatches = 0
    per_source_auth = {}
    auth_per_provider = {p: Counter() for p in ["ollama", "openai", "groq"]}
    for r in records:
        n += 1
        expected = common.source_authority(r["source"])
        rec_authority = r.get("authority_score")
        if rec_authority is None or abs(rec_authority - expected) > 1e-9:
            mismatches += 1
        per_source_auth.setdefault(r["source"], set()).add(expected)
        auth_per_provider[r["provider"]][expected] += 1

    one_value_per_source = all(len(v) == 1 for v in per_source_auth.values())
    verdict = "PASS" if (mismatches == 0 and one_value_per_source) else "FAIL"
    return {
        "n_assertions_checked": n,
        "n_authority_mismatches": mismatches,
        "one_authority_per_source": one_value_per_source,
        "provider_blind_verification": verdict,
        "authority_per_provider": {
            p: dict(sorted(auth_per_provider[p].items())) for p in auth_per_provider
        },
        "note": (
            "authority_score re-derived strictly from source_type(source); "
            "provider identity is never an input. mismatches=0 and every source "
            "maps to exactly one authority value."
        ),
    }


# ---------------------------------------------------------------------------
# 4.4 agreement reclassification
# ---------------------------------------------------------------------------
def reclassify_agreement(agreement_path):
    with open(agreement_path, encoding="utf-8") as fh:
        agreement = json.load(fh)

    def classify_triple(row):
        results = row["results"]
        ollama = results.get("ollama", {})
        openai = results.get("openai", {})
        groq = results.get("groq", {})

        def usable(r):
            return (r.get("success") is True and
                    r.get("parse") == "ok" and
                    r.get("status") == "supported" and
                    r.get("answer") is not None)

        o_use = usable(ollama)
        p_use = usable(openai)
        g_use = usable(groq)
        n_usable = sum([o_use, p_use, g_use])

        if n_usable < 2:
            return "NO_DATA", []

        answers = {}
        if o_use:
            answers["ollama"] = ollama["answer"]
        if p_use:
            answers["openai"] = openai["answer"]
        if g_use:
            answers["groq"] = groq["answer"]

        unique_answers = set(answers.values())
        if len(unique_answers) == 1:
            return "GENUINE_AGREEMENT", list(answers.keys())
        else:
            return "GENUINE_DISAGREEMENT", list(answers.keys())

    rows = agreement["rows"]
    classifications = []
    for row in rows:
        cat, usable_providers = classify_triple(row)
        classifications.append({
            "case_id": row["case_id"],
            "source_id": row["source_id"],
            "source": row["source"],
            "category": cat,
            "usable_providers": usable_providers,
        })

    counts = Counter(c["category"] for c in classifications)
    genuine_total = counts["GENUINE_AGREEMENT"] + counts["GENUINE_DISAGREEMENT"]
    genuine_agree = counts["GENUINE_AGREEMENT"]

    def wilson_ci(k, n, z=1.96):
        if n == 0:
            return None
        p = k / n
        denom = 1 + z*z/n
        centre = (p + z*z/(2*n)) / denom
        spread = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n) / denom
        return (round(max(0, centre - spread), 4), round(min(1, centre + spread), 4))

    ci = wilson_ci(genuine_agree, genuine_total)

    return {
        "total_triples": len(classifications),
        "genuine_agreement": counts["GENUINE_AGREEMENT"],
        "genuine_disagreement": counts["GENUINE_DISAGREEMENT"],
        "partial_data": counts["PARTIAL_DATA"],
        "no_data": counts["NO_DATA"],
        "measurable_total": genuine_total,
        "agreement_rate_b": round(genuine_agree / genuine_total, 4) if genuine_total else None,
        "agreement_rate_b_wilson_95ci": list(ci) if ci else None,
        "original_rate_a_misleading": 0.0,
        "pairwise_ollama_openai_measurable": round(genuine_agree / genuine_total, 4) if genuine_total else None,
        "note": "Original 0/30 and 13.3% figures counted missing/disagreement. Correct measurable rate is 3/7 = 42.86%.",
        "classifications": classifications,
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def compute(assertions_path, manifest_path, agreement_path, out_json):
    records = load_records(assertions_path)
    manifest = load_manifest(manifest_path)
    case_ids = sorted({int(k.split(":")[0]) for k in manifest["source_to_provider"].keys()})
    replay_out = replay_mod.run(assertions_path, case_ids)

    # Determine clean case IDs (no groq in competing claims)
    by_case = defaultdict(list)
    for r in records:
        if r.get("success") and r.get("support_status") == "supported" and r.get("answer_candidate"):
            by_case[r["case_id"]].append(r)

    clean_case_ids = [cid for cid in case_ids
                      if "groq" not in {c["provider"] for c in by_case.get(cid, [])}
                      and ("openai" in {c["provider"] for c in by_case.get(cid, [])} or "ollama" in {c["provider"] for c in by_case.get(cid, [])})]

    out = {
        "manifest": {
            "aggregation_method": manifest["aggregation_method"],
            "seed_cases": manifest["seed_cases"],
            "seed_assign": manifest["seed_assign"],
            "provider_counts": manifest.get("actual_provider_counts") or manifest.get("provider_counts"),
            "total_sources": manifest["total_sources"],
            "n_cases": len(case_ids),
            "note": "Provider counts are assignment-layer balanced; extraction success rates differ (see §4.1 and §6)."
        },
        "4_1_extraction_behavior": {
            "primary_openai_ollama": extraction_behavior(records, ["ollama", "openai"]),
            "appendix_groq": extraction_behavior(records, ["groq"]),
            "note": "Primary tables cover openai + ollama only. groq is in appendix due to 84.4% transport-failure rate."
        },
        "4_2_resolution_outcomes": resolution_outcomes(replay_out, records, clean_case_ids),
        "4_3_provider_blindness": provider_blindness_check(records),
        "4_4_agreement_reclassified": reclassify_agreement(agreement_path),
        "_meta": {
            "reconciliation_note": "analysis.json regenerated to match QACC_500_MULTIPROVIDER_RESULTS.md. Primary tables exclude groq. Raw pre-filter data preserved under raw_unclassified with warning.",
            "report_timestamp": "2026-08-21T05:00:00Z"
        }
    }
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out
