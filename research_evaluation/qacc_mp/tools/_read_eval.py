"""Read and pretty-print QACC evaluation results."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
RUN_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc" / "_frozen_assertions_500_multiprovider" / "RUN"


def read_analysis():
    p = RUN_DIR / "analysis.json"
    if not p.exists():
        print("analysis.json not found")
        sys.exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, default=str))


def read_manifest():
    p = RUN_DIR / "manifest_provider_distribution.json"
    if not p.exists():
        print("manifest_provider_distribution.json not found")
        sys.exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, default=str))


def read_assertions(limit: int = 5):
    p = RUN_DIR / "_assertions_500_multiprovider.jsonl"
    if not p.exists():
        print("_assertions_500_multiprovider.jsonl not found")
        sys.exit(1)
    with open(p, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            rec = json.loads(line)
            print(json.dumps(rec, indent=2, default=str))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", action="store_true", help="print analysis.json")
    ap.add_argument("--manifest", action="store_true", help="print manifest")
    ap.add_argument("--assertions", type=int, default=0, help="print N assertion records")
    args = ap.parse_args()

    if args.analysis:
        read_analysis()
    elif args.manifest:
        read_manifest()
    elif args.assertions:
        read_assertions(args.assertions)
    else:
        read_analysis()


if __name__ == "__main__":
    main()
