"""Validate QACC dataset integrity and required fields."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from research_evaluation.qacc_mp import common


REQUIRED_CASE_FIELDS = {"annotation_task_id", "question", "contexts", "sources"}
REQUIRED_SOURCE_FIELDS = {"source"}
REQUIRED_CONTEXT_FIELDS = {"context_id", "text"}


def check_case(case):
    missing = REQUIRED_CASE_FIELDS - case.keys()
    if missing:
        return False, f"case {case.get('annotation_task_id')} missing {missing}"
    for i, ctx in enumerate(case.get("contexts", [])):
        if not REQUIRED_CONTEXT_FIELDS.issubset(ctx.keys()):
            return False, f"case {case['annotation_task_id']} context[{i}] missing fields"
    for i, src in enumerate(case.get("sources", [])):
        if not REQUIRED_SOURCE_FIELDS.issubset(src.keys()):
            return False, f"case {case['annotation_task_id']} source[{i}] missing fields"
    return True, "ok"


def main():
    dataset = common.load_dataset()
    cases = common.select_cases(dataset)
    ok = 0
    fail = 0
    for case in cases:
        good, msg = check_case(case)
        if good:
            ok += 1
        else:
            fail += 1
            print(f"FAIL: {msg}")
    print(f"Checked {len(cases)} cases  pass={ok}  fail={fail}")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
