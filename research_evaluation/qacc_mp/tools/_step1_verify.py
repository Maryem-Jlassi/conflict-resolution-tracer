"""Step 1 verification: confirm provider configs and environment."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from research_evaluation.qacc_mp import common


def check_env(var: str):
    val = os.environ.get(var)
    if val:
        masked = val[:8] + "..." if len(val) > 8 else val
        print(f"  {var}: set ({masked})")
        return True
    else:
        print(f"  {var}: NOT SET")
        return False


def main():
    print("=== Step 1 Verification ===\n")
    print("Providers configured:")
    for name, cfg in common.PROVIDERS.items():
        print(f"  {name}: model={cfg['model']}  temp={cfg['temperature']}")
        if cfg.get("api_key_env"):
            check_env(cfg["api_key_env"])
        if cfg.get("endpoint"):
            print(f"    endpoint={cfg['endpoint']}")

    print("\nDataset path:")
    print(f"  {common.DATASET_PATH}")
    print(f"  exists={common.DATASET_PATH.exists()}")

    print("\nOutput path:")
    print(f"  {common.OUTPUT_DIR}")
    print(f"  exists={common.OUTPUT_DIR.exists()}")

    print("\nSource-type authority map:")
    for k, v in common.SOURCE_TYPE_AUTHORITY.items():
        print(f"  {k}: {v}")

    missing = []
    for name, cfg in common.PROVIDERS.items():
        if cfg.get("api_key_env") and not os.environ.get(cfg["api_key_env"]):
            missing.append(cfg["api_key_env"])
    if missing:
        print(f"\nWARNING: missing env vars: {missing}")
        sys.exit(1)
    print("\nOK: all required env vars present")


if __name__ == "__main__":
    main()
