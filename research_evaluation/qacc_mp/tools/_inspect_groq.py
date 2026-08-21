"""Inspect Groq extraction results and failure modes."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from research_evaluation.qacc_mp import common


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

    groq = [r for r in records if r.get("provider") == "groq"]
    ok = [r for r in groq if r.get("success")]
    fail = [r for r in groq if not r.get("success")]

    print(f"Groq records  : {len(groq)}")
    print(f"  success     : {len(ok)}")
    print(f"  failed      : {len(fail)}")

    if fail:
        error_types = Counter()
        for r in fail:
            err = (r.get("error") or "").lower()
            if "429" in err or "rate" in err:
                error_types["rate_limit"] += 1
            elif "timeout" in err:
                error_types["timeout"] += 1
            elif "parse" in err:
                error_types["parse"] += 1
            else:
                error_types["other"] += 1
        print(f"  failure breakdown: {dict(error_types)}")

    if ok:
        supported = [r for r in ok if r.get("support_status") == "supported"]
        unsupported = [r for r in ok if r.get("support_status") == "unsupported"]
        print(f"  supported   : {len(supported)}")
        print(f"  unsupported : {len(unsupported)}")
        lens = [len(r.get("answer_candidate") or "") for r in supported]
        if lens:
            print(f"  mean claim len: {sum(lens)/len(lens):.2f}")

    print("\nSample failures:")
    for r in fail[:3]:
        print(f"  case={r.get('case_id')} src={r.get('source_id')} err={r.get('error', '')[:120]}")


if __name__ == "__main__":
    main()
