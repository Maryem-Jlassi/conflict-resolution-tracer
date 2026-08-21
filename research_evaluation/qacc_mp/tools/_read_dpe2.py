"""Read DPE2 (secondary policy engine / DPE v2) artifacts or config."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def find_dpe2_files():
    candidates = [
        REPO_ROOT / "crt_core" / "pipeline.py",
        REPO_ROOT / "crt_core" / "replay.py",
        REPO_ROOT / "crt_core" / "user_input_policy.py",
        REPO_ROOT / "crt_core" / "temporal_enforcement.py",
    ]
    found = [p for p in candidates if p.exists()]
    return found


def main():
    files = find_dpe2_files()
    if not files:
        print("No DPE2-related files found.")
        sys.exit(1)

    print("=== DPE2 / Policy Engine v2 Files ===\n")
    for p in files:
        print(f"{p}")
        print("-" * 60)
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if any(k in line.lower() for k in ["policy", "dpe", "pipeline", "replay", "temporal", "user_input"]):
                    print(f"  {i:4d}: {line.rstrip()}")
        print()


if __name__ == "__main__":
    main()
