"""Read sample cases from the QACC dataset."""
from __future__ import annotations

import json
from pathlib import Path

from research_evaluation.qacc_mp import common


def main():
    dataset = common.load_dataset()
    cases = common.select_cases(dataset)
    for i, case in enumerate(cases[:5]):
        cid = case["annotation_task_id"]
        print(f"\n=== case {i+1} id={cid} ===")
        print(f"question : {case.get('question', '')[:120]}")
        print(f"gold     : {common.qacc_gold(case)}")
        print(f"contexts : {len(case.get('contexts', []))}")
        for j, ctx in enumerate(case.get("contexts", [])[:2]):
            print(f"  ctx[{j}]={ctx.get('context_id', '')}: {ctx.get('text', '')[:100]}")
        print(f"sources  : {len(case.get('sources', []))}")
        for j, src in enumerate(case.get("sources", [])[:3]):
            print(f"  src[{j}] type={common.classify_source_type(src.get('source', ''))} auth={common.source_authority(src.get('source', ''))}")
            print(f"    url={src.get('source', '')[:80]}")


if __name__ == "__main__":
    main()
