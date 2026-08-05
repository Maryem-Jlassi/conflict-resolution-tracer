"""
Pin the canonical (validated) benchmark artifacts.

Phase 14/15: the documentation generator and the release gate read validated
artifacts through ``benchmark_results/manifest.json``. This script (re)pins the
manifest by selecting the newest file matching each benchmark's canonical glob,
recording its relative path and SHA-256. Pinning is explicit and auditable:
after any artifact regeneration the manifest must be re-pinned here, and
``verify_release.py`` then cross-checks the recorded hashes against the files on
disk (tamper / staleness detection).

Usage::

    python tools/pin_artifacts.py [--out benchmark_results/manifest.json]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# benchmark key -> canonical artifact glob (relative to repo root)
CANONICAL_PATTERNS: Dict[str, List[str]] = {
    "benchmark_a": ["benchmark_results/benchmark_a_race_condition_*.csv"],
    "benchmark_b": ["benchmark_results/benchmark_b_mandela_*.csv"],
    "benchmark_c": ["benchmark_results/benchmark_c_evaluation_*.csv"],
    "benchmark_d": ["benchmark_results/benchmark_d_ablation_*.csv"],
    "benchmark_e": ["benchmark_results/benchmark_e_synthetic_*.json"],
}

REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_newest(patterns: List[str]) -> Optional[Path]:
    """Return the newest file matching any pattern (sorted by mtime)."""
    candidates: List[Path] = []
    for pat in patterns:
        for raw in glob.glob(str(REPO_ROOT / pat)):
            candidates.append(Path(raw))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_manifest() -> Dict:
    artifacts: Dict[str, Dict] = {}
    for key, patterns in CANONICAL_PATTERNS.items():
        path = pick_newest(patterns)
        if path is None:
            raise FileNotFoundError(f"No validated artifact matched {patterns}")
        rel = path.relative_to(REPO_ROOT).as_posix()
        artifacts[key] = {"path": rel, "sha256": sha256_of(path)}
    return {
        "schema_version": 1,
        "kind": "lcm_validated_artifacts",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "artifacts": artifacts,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pin validated benchmark artifacts")
    parser.add_argument("--out", default="benchmark_results/manifest.json")
    args = parser.parse_args(argv)

    manifest = build_manifest()
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned {len(manifest['artifacts'])} artifacts -> {out}")
    for key, entry in manifest["artifacts"].items():
        print(f"  {key:<12} {entry['path']}  sha256={entry['sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
