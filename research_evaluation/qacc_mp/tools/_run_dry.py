"""Dry-run / smoke validation for QACC evaluation."""
from __future__ import annotations

import sys
from pathlib import Path

from research_evaluation.qacc_mp import common, run_all


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"[dry-run] smoke limit={limit}")
    print(f"  dataset    : {common.DATASET_PATH}")
    print(f"  output_dir : {common.OUTPUT_DIR}")
    print(f"  providers  : {list(common.PROVIDERS.keys())}")

    dataset = common.load_dataset()
    cases = common.select_cases(dataset, common.N_CASES)
    cases = cases[:limit]
    print(f"  cases      : {len(cases)}")

    total_slots = sum(len(c.get("contexts", [])) for c in cases)
    print(f"  source slots: {total_slots}")

    print("\nProceeding with orchestrate()...")
    res = run_all.orchestrate(limit=limit, workers=2)
    print(res)


if __name__ == "__main__":
    main()
