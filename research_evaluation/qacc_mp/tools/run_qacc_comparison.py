"""Run QACC comparison: single-provider vs multi-provider results."""
from __future__ import annotations

import json
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
QACC_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"
SINGLE_DIR = QACC_DIR / "_frozen_assertions_500"
MULTI_DIR = QACC_DIR / "_frozen_assertions_500_multiprovider" / "RUN"


def main():
    print("=== QACC Comparison: single-provider vs multi-provider ===\n")

    if not MULTI_DIR.exists():
        print(f"Multi-provider RUN not found: {MULTI_DIR}")
        return

    multi_analysis = MULTI_DIR / "analysis.json"
    if not multi_analysis.exists():
        print("analysis.json missing in multi-provider RUN")
        return

    data = json.loads(multi_analysis.read_text(encoding="utf-8"))
    ro = data.get("4_2_resolution_outcomes", {})
    clean = ro.get("clean_subset_openai_ollama", {})

    print("Multi-provider (openai + ollama clean subset):")
    print(f"  resolved cases : {clean.get('resolved_cases_full_lcm', 0)}")
    print(f"  openai wins    : {clean.get('winners_by_provider', {}).get('openai', 0)}")
    print(f"  ollama wins    : {clean.get('winners_by_provider', {}).get('ollama', 0)}")
    print(f"  openai ratio   : {clean.get('win_vs_share_ratio_full_lcm', {}).get('openai', 'N/A')}")
    print(f"  ollama ratio   : {clean.get('win_vs_share_ratio_full_lcm', {}).get('ollama', 'N/A')}")

    if SINGLE_DIR.exists():
        single_report = SINGLE_DIR / "QACC_500_MULTIPROVIDER_RESULTS.md"
        if single_report.exists():
            print(f"\nSingle-provider report found: {single_report}")
        else:
            print(f"\nSingle-provider dir exists but no report at {single_report}")
    else:
        print(f"\nSingle-provider dir not found: {SINGLE_DIR}")
        print("(This additive run does not modify prior single-provider artifacts.)")


if __name__ == "__main__":
    main()
