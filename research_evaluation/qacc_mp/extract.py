"""Step 2 - source-agent generation with provider provenance.

Builds the deterministic 500-case manifest, assigns each source to exactly one
provider (Option A), runs the real extraction calls (resumable via a disk
cache), and writes:
  * the frozen attestation/assertion line-delimited log,
  * the manifest showing the ACTUAL provider distribution achieved.
Provider identity is metadata only; authority_score is computed solely from
source_type in provider_client (provider-blind).
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import common
from . import provider_client


def build_manifest(cases: list) -> dict:
    assign = common.assign_providers(cases)
    manifest = {
        "aggregation_method": common.AGGREGATION_METHOD,
        "seed_cases": common.SEED_CASES,
        "seed_assign": common.SEED_ASSIGN,
        "n_cases": len(cases),
        "total_sources": len(assign),
        "source_to_provider": {},
        "provider_counts": {},
    }
    for (ci, si), prov in sorted(assign.items()):
        cid = int(cases[ci]["annotation_task_id"])
        manifest["source_to_provider"][f"{cid}:{si}"] = prov
    manifest["provider_counts"] = dict(Counter(assign.values()))
    return manifest


def _cache_keys(path: Path) -> set:
    if not path.exists():
        return set()
    keys = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            keys.add((int(rec["case_id"]), rec["source_id"]))
    return keys


def run_extract(limit=None, workers: int = 8) -> dict:
    if limit is None:
        limit = common.SMOKE_LIMIT
    dataset = common.load_dataset()
    cases = common.select_cases(dataset, common.N_CASES)
    if limit and limit > 0:
        cases = cases[:limit]

    tag = "RUN" if not limit else "SMOKE_%d" % limit
    out_dir = common.OUTPUT_DIR / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "_extract_cache.jsonl"
    assertions_path = out_dir / "_assertions_500_multiprovider.jsonl"
    manifest_path = out_dir / "manifest_provider_distribution.json"

    manifest = build_manifest(cases)
    prov_for = manifest["source_to_provider"]

    slots = []
    for ci, c in enumerate(cases):
        cid = int(c["annotation_task_id"])
        for si in range(len(c.get("contexts", []))):
            slots.append((c, si, prov_for[f"{cid}:{si}"]))

    done = _cache_keys(cache_path)
    pending = [s for s in slots if (int(s[0]["annotation_task_id"]), s[1]) not in done]

    if pending:
        lock = threading.Lock()
        pending_sorted = sorted(
            pending, key=lambda s: (int(s[0]["annotation_task_id"]), s[1])
        )
        with open(cache_path, "a", encoding="utf-8") as fh:
            def _work(item):
                c, si, prov = item
                source = c["sources"][si]
                rec = provider_client.extract_source(prov, c, si, source)
                with lock:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                return rec

            with ThreadPoolExecutor(max_workers=workers) as pool:
                pool.map(_work, pending_sorted)

    # deterministic reconstruction from the cache, in slot order
    records_by_key = {}
    with open(cache_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            records_by_key[(int(r["case_id"]), r["source_id"])] = r

    ordered = []
    for c, si, _ in slots:
        rec = records_by_key.get((int(c["annotation_task_id"]), si))
        if rec is not None:
            ordered.append(rec)

    with open(assertions_path, "w", encoding="utf-8") as fh:
        for r in ordered:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    prov_counts = Counter(r["provider"] for r in ordered)
    failures = [
        {"case_id": r["case_id"], "source_id": r["source_id"],
         "provider": r["provider"], "error": (r.get("error") or "")[:300]}
        for r in ordered if not r["success"]
    ]
    manifest["actual_provider_counts"] = dict(prov_counts)
    manifest["n_failed_extracess"] = len(failures)
    manifest["failures"] = failures

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    print(f"[extract] wrote {len(ordered)} records -> {assertions_path}")
    print(f"[extract] provider_counts {dict(prov_counts)}")
    print(f"[extract] failed {len(failures)}")
    return {
        "assertions_path": str(assertions_path),
        "manifest_path": str(manifest_path),
        "records": len(ordered),
        "provider_counts": dict(prov_counts),
        "failures": failures,
    }


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    res = run_extract(limit=(n if n else None))
    print(res)