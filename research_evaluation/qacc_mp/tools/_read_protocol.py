"""Read QACC protocol / evaluation protocol definitions."""
from __future__ import annotations

import json
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
QACC_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"
MULTI_DIR = QACC_DIR / "_frozen_assertions_500_multiprovider" / "RUN"


def main():
    print("=== QACC Protocol ===\n")
    print(f"N_CASES           : {common.N_CASES}")
    print(f"SEED_CASES        : {common.SEED_CASES}")
    print(f"SEED_ASSIGN       : {common.SEED_ASSIGN}")
    print(f"SEED_AGREE        : {common.SEED_AGREE}")
    print(f"N_AGREEMENT       : {common.N_AGREEMENT}")
    print(f"AGGREGATION_METHOD: {common.AGGREGATION_METHOD}")
    print(f"SMOKE_LIMIT       : {common.SMOKE_LIMIT}")
    print(f"PROVIDERS         : {list(common.PROVIDERS.keys())}")
    print(f"ASSIGNMENT_ORDER  : {common.ASSIGNMENT_ORDER}")
    print(f"DATASET_PATH      : {common.DATASET_PATH}")
    print(f"OUTPUT_DIR        : {common.OUTPUT_DIR}")

    manifest_path = MULTI_DIR / "manifest_provider_distribution.json"
    if manifest_path.exists():
        print(f"\n=== Actual manifest ({manifest_path.name}) ===")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(json.dumps(manifest, indent=2, default=str))
    else:
        print(f"\n(manifest not found at {manifest_path})")


if __name__ == "__main__":
    main()
