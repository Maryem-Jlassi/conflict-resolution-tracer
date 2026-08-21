"""Step 4.4 - extraction agreement sub-check (Option B, small scale only).

On a RANDOM subsample of 30 sources, run ALL THREE providers on the SAME
context (90 extra calls) and report how often they extract the same
claim/value vs disagree.  This bounds whether Option A's one-provider-per-
source design measures genuine source disagreement vs extraction noise.
"""
from __future__ import annotations

import json
import random

from . import common
from . import provider_client


def run(n=common.N_AGREEMENT, out_path=None, limit=None):
    dataset = common.load_dataset()
    cases = common.select_cases(dataset, common.N_CASES)
    if limit and limit > 0:
        cases = cases[:limit]

    # all (global) source slots across the selected cases
    slots = []
    for c in cases:
        cid = int(c["annotation_task_id"])
        for si in range(len(c.get("contexts", []))):
            slots.append((cid, si))
    rng = random.Random(common.SEED_AGREE)
    sample = rng.sample(slots, min(n, len(slots)))
    _by_id = {int(c["annotation_task_id"]): c for c in cases}

    from concurrent.futures import ThreadPoolExecutor

    tasks = []
    for cid, si in sample:
        case = _by_id[cid]
        source = case["sources"][si]
        for prov in ["ollama", "openai", "groq"]:
            tasks.append((cid, si, case, source, prov))

    results_map = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {}
        for (cid, si, case, source, prov) in tasks:
            futs[pool.submit(_agree_one, case, si, source, prov)] = (cid, si, prov)
        for fut in futs:
            cid, si, prov = futs[fut]
            results_map[(cid, si, prov)] = fut.result()

    rows = []
    for cid, si in sample:
        source = _by_id[cid]["sources"][si]
        res = {prov: results_map[(cid, si, prov)] for prov in ["ollama", "openai", "groq"]}
        rows.append({"case_id": cid, "source_id": si, "source": source, "results": res})

    metrics = aggregate(rows)
    out = {"seed": common.SEED_AGREE, "n_sources": len(rows), "metrics": metrics,
           "rows": rows}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
    return out


def _norm(s):
    return common.normalize_text(s) if s else None


def _agree_one(case, si, source, prov):
    rec = provider_client.extract_source(prov, case, si, source)
    return {
        "success": rec["success"],
        "status": rec.get("support_status"),
        "answer": rec.get("answer_candidate"),
        "parse": rec.get("parse_status"),
        "error": rec.get("error"),
    }


def aggregate(rows):
    total = len(rows)
    all_three_agree = 0
    all_three_agree_value = 0
    agreed_value_pairs = 0
    pairs = [("ollama", "openai"), ("ollama", "groq"), ("openai", "groq")]
    pair_counts = {p: 0 for p in pairs}
    pair_agree = {p: 0 for p in pairs}
    abstain_all = 0

    for row in rows:
        corr = {}
        for p in ["ollama", "openai", "groq"]:
            r = row["results"][p]
            corr[p] = r["status"] == "supported" and r.get("answer")
        vals = {p: _norm(row["results"][p].get("answer"))
                for p in ["ollama", "openai", "groq"]}
        supported_vals = {p: v for p, v in vals.items() if v}
        if all(not v for v in supported_vals.values()):
            abstain_all += 1
        if len(set(map(str, [corr[k] for k in corr]))) == 1 and all(corr.values()):
            all_three_agree += 1
            # all three supported a claim
            sv = set(supported_vals.values())
            if len(sv) == 1:
                all_three_agree_value += 1
        for (a, b) in pairs:
            pair_counts[(a, b)] += 1  # both comparable
            if corr[a] and corr[b]:
                if (vals[a] or "") == (vals[b] or "") and vals[a]:
                    pair_agree[(a, b)] += 1
    return {
        "total_sources": total,
        "three_supported_agree": all_three_agree,
        "three_value_agree": all_three_agree_value,
        "three_value_agree_rate": round(all_three_agree_value / total, 4) if total else 0.0,
        "all_three_abstain": abstain_all,
        "pairwise_value_agree_rate": {
            "%s|%s" % (a, b): round(pair_agree[(a, b)] / pair_counts[(a, b)], 4)
            if pair_counts[(a, b)] else None
            for (a, b) in pairs
        },
    }


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else common.N_AGREEMENT
    run(n=n)