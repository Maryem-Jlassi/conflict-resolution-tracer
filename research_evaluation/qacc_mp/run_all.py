"""End-to-end orchestrator for the QACC 500-case multi-provider run.

Usage:
  python -m research_evaluation.qacc_mp.run_all        # full run
  QACC_MP_LIMIT=4 python -m ...run_all                 # bounded smoke validation

Pipeline: extract (Step 2) -> replay (Step 3) -> analysis (Step 4.1-4.3)
          -> agreement (Step 4.4) -> report (Step 5).
"""
from __future__ import annotations

import json
import sys

from . import common
from . import extract as extract_mod
from . import agreement as agreement_mod
from . import analysis as analysis_mod
from . import report as report_mod


def orchestrate(limit=None, workers=8, agree_n=None):
    if limit is None:
        limit = common.SMOKE_LIMIT
    if agree_n is None:
        agree_n = common.N_AGREEMENT

    tag = "RUN" if not limit else "SMOKE_%d" % limit
    out_dir = common.OUTPUT_DIR / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 2 ---------------------------------------------------------
    ext = extract_mod.run_extract(limit=limit, workers=workers)
    assertions_path = ext["assertions_path"]
    manifest_path = ext["manifest_path"]

    # --- Step 4.4 agreement (bounded) -----------------------------------
    agree_path = out_dir / "agreement_30.json"
    if not agree_path.exists():
        print("[run] extracting agreement subsample ...")
        agreement_mod.run(n=agree_n, out_path=str(agree_path), limit=limit)
    else:
        print("[run] agreement already present")

    # --- Step 4 analysis (incl. 4.3 verification) -----------------------
    analysis_path = out_dir / "analysis.json"
    analysis = analysis_mod.compute(assertions_path, manifest_path, str(analysis_path))

    # --- Step 4.4 metrics into the report -------------------------------
    agreement = {}
    if agree_path.exists():
        agreement = json.loads(agree_path.read_text(encoding="utf-8"))

    # --- Step 5 report ---------------------------------------------------
    md_path = out_dir / "QACC_500_MULTIPROVIDER_RESULTS.md" if limit else \
        common.OUTPUT_DIR / "QACC_500_MULTIPROVIDER_RESULTS.md"
    md_path = common.OUTPUT_DIR / ("QACC_500_MULTIPROVIDER_RESULTS.md" if not limit
                                   else str(tag) + "/QACC_500_MULTIPROVIDER_RESULTS.md")
    report_mod.render(analysis, agreement, str(md_path), tag=tag)

    print(f"[run] done -> {md_path}")
    return {"tag": tag, "extract": ext, "analysis": str(analysis_path), "md": str(md_path)}


if __name__ == "__main__":
    n = None
    if len(sys.argv) > 1:
        n = int(sys.argv[1])
    print(orchestrate(limit=n))