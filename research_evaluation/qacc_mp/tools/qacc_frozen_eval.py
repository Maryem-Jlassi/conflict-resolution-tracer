"""QACC frozen evaluation runner (root-level convenience wrapper).

Usage:
    python qacc_frozen_eval.py              # full 500-case run
    python qacc_frozen_eval.py --smoke 40   # bounded smoke validation
    python qacc_frozen_eval.py --dry-run    # preview only
"""
from __future__ import annotations

import argparse
import sys

from research_evaluation.qacc_mp import run_all


def main():
    ap = argparse.ArgumentParser(description="QACC frozen evaluation runner")
    ap.add_argument("--smoke", type=int, default=0, help="bounded smoke limit (0 = full run)")
    ap.add_argument("--dry-run", action="store_true", help="print config and exit")
    ap.add_argument("--workers", type=int, default=8, help="extraction workers")
    args = ap.parse_args()

    if args.dry_run:
        from research_evaluation.qacc_mp import common
        print("QACC frozen eval config:")
        print(f"  n_cases      : {common.N_CASES}")
        print(f"  seed_cases   : {common.SEED_CASES}")
        print(f"  seed_assign  : {common.SEED_ASSIGN}")
        print(f"  providers    : {list(common.PROVIDERS.keys())}")
        print(f"  output_dir   : {common.OUTPUT_DIR}")
        print(f"  smoke_limit  : {args.smoke or 'none (full run)'}")
        sys.exit(0)

    limit = args.smoke if args.smoke else None
    res = run_all.orchestrate(limit=limit, workers=args.workers)
    print(res)


if __name__ == "__main__":
    main()
