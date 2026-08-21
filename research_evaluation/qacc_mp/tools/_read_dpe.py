"""Read DPE (Dynamic Policy Engine) artifacts or config."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def find_dpe_files():
    candidates = [
        REPO_ROOT / "crt_core" / "config.py",
        REPO_ROOT / "crt_core" / "confidence_engine.py",
        REPO_ROOT / "crt_core" / "conflict.py",
        REPO_ROOT / "crt_core" / "trust_manager.py",
    ]
    found = [p for p in candidates if p.exists()]
    return found


def main():
    files = find_dpe_files()
    if not files:
        print("No DPE-related files found.")
        sys.exit(1)

    print("=== DPE / Policy Engine Files ===\n")
    for p in files:
        print(f"{p}")
        print("-" * 60)
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if any(k in line.lower() for k in ["policy", "dpe", "dynamic", "engine", "trust", "confidence"]):
                    print(f"  {i:4d}: {line.rstrip()}")
        print()


if __name__ == "__main__":
    main()
