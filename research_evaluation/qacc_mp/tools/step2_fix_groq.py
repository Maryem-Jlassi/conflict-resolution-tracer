"""Step 2 Groq fix: rerun Groq extractions with backoff/retry config."""
from __future__ import annotations

import json
import time
from pathlib import Path

from research_evaluation.qacc_mp import common, extract_mod, provider_client


REPO_ROOT = Path(__file__).resolve().parent
RUN_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc" / "_frozen_assertions_500_multiprovider" / "RUN"
CACHE = RUN_DIR / "_extract_cache.jsonl"


def main():
    if not CACHE.exists():
        print(f"extract cache not found: {CACHE}")
        return

    records = []
    with open(CACHE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    groq_fail = [r for r in records if r.get("provider") == "groq" and not r.get("success")]
    print(f"Groq failures to retry: {len(groq_fail)}")

    if not groq_fail:
        print("No Groq failures found. Nothing to fix.")
        return

    with open(CACHE, "a", encoding="utf-8") as fh:
        for i, rec in enumerate(groq_fail, 1):
            print(f"[fix] retrying case={rec['case_id']} source={rec['source_id']} ({i}/{len(groq_fail)})")
            try:
                dataset = common.load_dataset()
                cases = common.select_cases(dataset, common.N_CASES)
                case = next(c for c in cases if int(c["annotation_task_id"]) == int(rec["case_id"]))
                source = case["sources"][int(rec["source_id"])]
                new_rec = provider_client.extract_source("groq", case, int(rec["source_id"]), source)
                fh.write(json.dumps(new_rec, ensure_ascii=False) + "\n")
                time.sleep(1.0)
            except Exception as exc:
                print(f"[fix] ERROR: {exc}")

    print("[fix] done. Re-run extract.py to rebuild assertions.")


if __name__ == "__main__":
    main()
