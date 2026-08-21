"""Read backend/provider configurations from the QACC framework."""
from __future__ import annotations

import json
from pathlib import Path

from research_evaluation.qacc_mp import common


def main():
    print("=== Backends / Providers ===\n")
    for name, cfg in common.PROVIDERS.items():
        print(f"{name}:")
        print(f"  model       : {cfg.get('model')}")
        print(f"  temperature : {cfg.get('temperature')}")
        print(f"  endpoint    : {cfg.get('endpoint') or 'default'}")
        print(f"  api_key_env : {cfg.get('api_key_env')}")
        print()

    print("=== Source-type authority map ===\n")
    for k, v in common.SOURCE_TYPE_AUTHORITY.items():
        print(f"  {k}: {v}")

    print(f"\nAssignment order: {common.ASSIGNMENT_ORDER}")
    print(f"Aggregation     : {common.AGGREGATION_METHOD}")


if __name__ == "__main__":
    main()
