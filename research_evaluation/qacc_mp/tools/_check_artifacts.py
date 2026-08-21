"""Check frozen artifacts in the QACC evaluation output directory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
QACC_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"


def check_dir(tag: str):
    base = QACC_DIR / "_frozen_assertions_500_multiprovider" / tag
    if not base.exists():
        print(f"MISSING: {base}")
        return False

    required = [
        "_assertions_500_multiprovider.jsonl",
        "_extract_cache.jsonl",
        "manifest_provider_distribution.json",
        "analysis.json",
        "QACC_500_MULTIPROVIDER_RESULTS.md",
    ]
    ok = True
    for name in required:
        p = base / name
        if not p.exists():
            print(f"MISSING: {p}")
            ok = False
        else:
            print(f"OK    : {p}  ({p.stat().st_size} bytes)")

    if (base / "analysis.json").exists():
        data = json.loads((base / "analysis.json").read_text(encoding="utf-8"))
        prov = data.get("manifest", {}).get("provider_counts", {})
        print(f"Provider counts : {prov}")
    return ok


def main():
    print("=== RUN ===")
    run_ok = check_dir("RUN")
    print("\n=== SMOKE_3 ===")
    smoke_ok = check_dir("SMOKE_3")
    print("\n=== SMOKE_40 ===")
    smoke40_ok = check_dir("SMOKE_40")
    print("\n=== SMOKE_100 ===")
    smoke100_ok = check_dir("SMOKE_100")
    all_ok = run_ok and smoke_ok and smoke40_ok and smoke100_ok
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
