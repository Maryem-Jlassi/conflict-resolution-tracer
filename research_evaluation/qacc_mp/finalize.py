"""Regenerate the Step 4 analysis + Step 5 report from an already-extracted run,
without re-calling any provider.  Use this only AFTER extraction has completed.

  python -m research_evaluation.qacc_mp.finalize RUN      # full 500-case tag
  python -m research_evaluation.qacc_mp.finalize SMOKE_3  # a finished smoke

Guard: it refuses to compute the cross-model analysis unless the extraction
cache is COMPLETE for that tag (== manifest.total_sources), so a partial run
can never be mislabeled as the final 500-case report.
"""
from __future__ import annotations

import json
import sys

from . import common
from . import agreement as agreement_mod
from . import analysis as analysis_mod
from . import report as report_mod


def _cache_count(tag: str) -> int:
    cache_path = common.OUTPUT_DIR / tag / "_extract_cache.jsonl"
    if not cache_path.exists():
        return 0
    with open(cache_path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def finalize(tag: str = "RUN"):
    out_dir = common.OUTPUT_DIR / tag
    assertions = out_dir / "_assertions_500_multiprovider.jsonl"
    manifest = out_dir / "manifest_provider_distribution.json"
    agree = out_dir / "agreement_30.json"
    analysis_path = out_dir / "analysis.json"
    md_path = (common.OUTPUT_DIR / "QACC_500_MULTIPROVIDER_RESULTS.md"
               if tag == "RUN"
               else out_dir / "QACC_500_MULTIPROVIDER_RESULTS.md")

    # --- completeness guard (Step 2 done before Step 4/5) ------------------
    man = json.loads(manifest.read_text(encoding="utf-8"))
    total = man.get("total_sources", 0)
    done = _cache_count(tag)
    if done < total - 1:
        print(f"[finalize] ABORT: extraction incomplete for tag={tag} "
              f"(cache={done}/{total}). Track with `python research_evaluation\\_mon.py`; "
              f"re-run finalize once the cache reaches {total}.")
        return None

        # --- Step 4.4 agreement (only if absent) -----------------------------
    if not agree.exists():
        print("[finalize] extracting agreement subsample ...")
        agree_limit = int(tag.split("_")[1]) if tag.startswith("SMOKE_") else None
        agreement_mod.run(n=common.N_AGREEMENT, out_path=str(agree), limit=agree_limit)
    agreement = json.loads(agree.read_text(encoding="utf-8"))

    # --- Step 4 analysis (4.1/4.2/4.3) -----------------------------------
    print("[finalize] computing analysis (Step 4) ...")
    analysis = analysis_mod.compute(str(assertions), str(manifest), str(agree), str(analysis_path))

    # --- Step 5 report ------------------------------------------------------
    print("[finalize] rendering report (Step 5) ...")
    report_mod.render(analysis, agreement, str(md_path), tag=tag)
    print(f"[finalize] report -> {md_path}")
    return str(md_path)


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "RUN"
    print(finalize(t))