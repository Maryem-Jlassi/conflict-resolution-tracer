"""Inspect QACC dataset structure and case distribution."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from research_evaluation.qacc_mp import common


def main():
    dataset = common.load_dataset()
    cases = common.select_cases(dataset)
    splits = Counter(c.get("split") for c in dataset)
    print(f"Total dataset size : {len(dataset)}")
    print(f"Split distribution : {dict(splits)}")
    print(f"Selected cases     : {len(cases)}")

    ctx_counts = [len(c.get("contexts", [])) for c in cases]
    src_counts = [len(c.get("sources", [])) for c in cases]
    print(f"Avg contexts/case  : {sum(ctx_counts)/len(ctx_counts):.2f}")
    print(f"Avg sources/case   : {sum(src_counts)/len(src_counts):.2f}")

    print("\nFirst 3 cases:")
    for c in cases[:3]:
        cid = c["annotation_task_id"]
        print(f"  case_id={cid}  question={c.get('question', '')[:60]}...")
        print(f"    contexts={len(c.get('contexts', []))}  sources={len(c.get('sources', []))}")
        for i, s in enumerate(c.get("sources", [])[:3]):
            print(f"      src[{i}]={s.get('source', '')[:50]}")


if __name__ == "__main__":
    main()
