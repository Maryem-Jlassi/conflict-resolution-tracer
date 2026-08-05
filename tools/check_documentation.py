"""Documentation-consistency gate (Phase 14).

Regenerates all documentation in memory from the central configuration and the
validated artifacts, then requires a byte-for-byte match with the committed
docs/ files and README generated sections. Exits non-zero on any drift or on
artifact tamper/staleness.

Usage::

    python tools/check_documentation.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.generate_documentation import REPO_ROOT, check_consistency


def main(argv: Optional[list] = None) -> int:
    report = check_consistency(REPO_ROOT / "benchmark_results" / "manifest.json")
    for c in report["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}"
              + (f" — {c['detail']}" if c["detail"] else ""))
    if not report["ok"]:
        print("documentation consistency: FAIL")
        return 1
    print("documentation consistency: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
