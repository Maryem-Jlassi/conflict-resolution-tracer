"""Final pre/post-run validation for QACC evaluation artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
QACC_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"
RUN_DIR = QACC_DIR / "_frozen_assertions_500_multiprovider" / "RUN"


def check():
    failures = []

    if not RUN_DIR.exists():
        failures.append("RUN directory missing")
        return failures

    analysis_path = RUN_DIR / "analysis.json"
    manifest_path = RUN_DIR / "manifest_provider_distribution.json"
    assertions_path = RUN_DIR / "_assertions_500_multiprovider.jsonl"
    md_path = RUN_DIR / "QACC_500_MULTIPROVIDER_RESULTS.md"

    for p in [analysis_path, manifest_path, assertions_path, md_path]:
        if not p.exists():
            failures.append(f"missing {p.name}")
        elif p.stat().st_size == 0:
            failures.append(f"empty {p.name}")

    if analysis_path.exists():
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        if "4_1_extraction_behavior" not in data:
            failures.append("analysis missing 4_1")
        if "4_2_resolution_outcomes" not in data:
            failures.append("analysis missing 4_2")
        if "4_3_provider_blindness" not in data:
            failures.append("analysis missing 4_3")
        verdict = data.get("4_3_provider_blindness", {}).get("provider_blind_verification")
        if verdict != "PASS":
            failures.append(f"provider blindness check failed: {verdict}")

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("n_cases") != 500:
            failures.append(f"unexpected n_cases: {manifest.get('n_cases')}")

    return failures


def main():
    print("[final_check] validating QACC artifacts ...")
    failures = check()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print("OK: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
