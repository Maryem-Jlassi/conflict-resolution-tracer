"""Inspect generated output files in the QACC evaluation directory."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
QACC_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"


def list_dir(tag: str):
    base = QACC_DIR / "_frozen_assertions_500_multiprovider" / tag
    if not base.exists():
        print(f"MISSING: {base}")
        return
    print(f"\n--- {tag} ---")
    for p in sorted(base.iterdir()):
        size = p.stat().st_size if p.is_file() else "-"
        print(f"  {p.name:50s}  {size}")


def main():
    for tag in ["RUN", "SMOKE_3", "SMOKE_40", "SMOKE_100"]:
        list_dir(tag)
    init = QACC_DIR / "_frozen_assertions_500_initial"
    if init.exists():
        print(f"\n--- _frozen_assertions_500_initial ---")
        for p in sorted(init.iterdir()):
            size = p.stat().st_size if p.is_file() else "-"
            print(f"  {p.name:50s}  {size}")


if __name__ == "__main__":
    main()
