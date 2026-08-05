"""
Unified Benchmark Suite Runner (Phase 11)

Runs the deterministic benchmarks (A–F) through one entry point and aggregates
headline metrics into a single summary JSON under ``benchmark_results/``.

Benchmarks and tags
-------------------
  A  race-condition / lost-updates      ``verification``   (locking isolation)
  B  Mandela injection (false memory)   ``experiment``
  C  conflict-resolution accuracy       ``verification``
  D  Ψ weight ablation (held-out)       ``verification``
  E  overlapping writes (in-process)    ``experiment``
  F  uncertainty-aware trust            ``diagnostic``     (pure model property)

Sizes
-----
``--quick`` (default) uses small trial counts for fast, CI-friendly runs;
``--full`` uses the production sizes each benchmark ships with. A fixed random
seed is applied up front so runs are reproducible; the seed is recorded in the
summary so any run can be replayed exactly.

Usage::

    python benchmarks/run_all.py                 # quick suite
    python benchmarks/run_all.py --full          # production-size suite
    python benchmarks/run_all.py --out out.json  # custom output path
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from benchmarks.benchmark_a_race_condition import run_benchmark_a
from benchmarks.benchmark_b_mandela import run_benchmark_b
from benchmarks.benchmark_c_evaluation import run_benchmark_c
from benchmarks.benchmark_d_ablation import run_benchmark_d
from benchmarks.benchmark_e_overlapping_writes import run_benchmark_e
from benchmarks.benchmark_f_trust_uncertainty import (
    trust_model_comparison_table,
    trust_profile_sweep,
)

DEFAULT_OUTPUT_DIR = "benchmark_results"


# ---------------------------------------------------------------------------
# Per-benchmark headline extractors (defensive: only present fields are used)
# ---------------------------------------------------------------------------

def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _headline_a(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"rows": len(rows), "tags": ["verification"]}
    for system in ("LCM", "No-LCM"):
        subset = [r["lost_update_rate"] for r in rows if r.get("system") == system]
        summary[f"{system}_mean_lost_update_rate"] = _mean(subset)
    return summary


def _headline_b(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"rows": len(rows), "tags": ["experiment"]}
    lcm = [r for r in rows if r.get("system") == "LCM"]
    no_lcm = [r for r in rows if r.get("system") == "No-LCM"]
    summary["LCM_mean_trapping_efficiency"] = _mean(
        [r["trapping_efficiency"] for r in lcm if "trapping_efficiency" in r]
    )
    summary["LCM_final_correct_rate"] = _mean(
        [1.0 if r.get("final_is_correct") else 0.0 for r in lcm]
    )
    summary["No-LCM_final_correct_rate"] = _mean(
        [1.0 if r.get("final_is_correct") else 0.0 for r in no_lcm]
    )
    return summary


def _headline_c(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"rows": len(rows), "tags": ["verification"]}
    for scenario_type in ("high_trust_vs_low_trust", "recency_dominated",
                          "cold_start", "graded_ambiguous"):
        subset = [
            r for r in rows
            if r.get("strategy") == "LCM" and r.get("scenario_type") == scenario_type
        ]
        summary[f"{scenario_type}_LCM_accuracy"] = _mean(
            [1.0 if r.get("correct") else 0.0 for r in subset]
        )
        summary[f"{scenario_type}_LCM_unresolved_rate"] = _mean(
            [1.0 if r.get("unresolved") else 0.0 for r in subset]
        )
    return summary


def _headline_d(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"rows": len(rows), "tags": ["verification"]}
    for fixture_set in ("frozen_held_out", "corrected_diagnostic_v2", "benchmark_c"):
        subset = [
            r for r in rows
            if r.get("fixture_set") == fixture_set and r.get("condition") == "Full"
        ]
        summary[f"{fixture_set}_full_strict_accuracy"] = _mean(
            [1.0 if r.get("correct") else 0.0 for r in subset]
        )
    return summary


def _headline_e(rows: List[Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"rows": len(rows), "tags": ["experiment"]}
    rates = []
    win_rates = []
    consistent = []
    for r in rows:
        d = dataclasses.asdict(r) if dataclasses.is_dataclass(r) else r
        if "conflict_rate" in d:
            rates.append(d["conflict_rate"])
        if "verification_win_rate" in d:
            win_rates.append(d["verification_win_rate"])
        if "final_consistent" in d:
            consistent.append(1.0 if d["final_consistent"] else 0.0)
    summary["mean_conflict_rate"] = _mean(rates)
    summary["mean_verification_win_rate"] = _mean(win_rates)
    summary["final_consistent_rate"] = _mean(consistent)
    return summary


def _headline_f() -> Dict[str, Any]:
    return {
        "tags": ["diagnostic"],
        "rows": len(trust_model_comparison_table()),
        "comparison": trust_model_comparison_table(),
        "sweep_rows": len(trust_profile_sweep(max_outcomes=25)),
    }


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_benchmark_suite(
    *,
    quick: bool = True,
    seed: int = 20260714,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> Dict[str, Any]:
    """Run the deterministic benchmark suite and return the aggregated summary."""
    random.seed(seed)
    started = time.perf_counter()
    results: Dict[str, Dict[str, Any]] = {}

    async def _run() -> None:
        if quick:
            results["benchmark_a"] = _headline_a(await run_benchmark_a(
                n_writers_list=[5], trials=3, failure_rates=[0.0, 0.02]))
            results["benchmark_b"] = _headline_b(await run_benchmark_b(
                repetition_counts=[1, 10], trials=3))
            results["benchmark_c"] = _headline_c(await run_benchmark_c(
                trials_per_scenario=5))
            results["benchmark_d"] = _headline_d(await run_benchmark_d(
                trials_per_scenario=5))
            results["benchmark_e"] = _headline_e(await run_benchmark_e(
                trials=3, seed=seed))
        else:
            results["benchmark_a"] = _headline_a(await run_benchmark_a())
            results["benchmark_b"] = _headline_b(await run_benchmark_b())
            results["benchmark_c"] = _headline_c(await run_benchmark_c())
            results["benchmark_d"] = _headline_d(await run_benchmark_d())
            results["benchmark_e"] = _headline_e(await run_benchmark_e(
                trials=20, seed=seed))

    asyncio.run(_run())
    results["benchmark_f"] = _headline_f()

    summary: Dict[str, Any] = {
        "suite": "benchmark_suite",
        "generated_at": datetime.utcnow().isoformat(),
        "mode": "quick" if quick else "full",
        "seed": seed,
        "reproducible": True,
        "benchmarks": results,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = out_dir / f"benchmark_suite_{ts}.json"
    fname.write_text(json.dumps(summary, indent=2, default=str))
    summary["saved_to"] = str(fname)
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    print(f"\nBenchmark suite ({summary['mode']}, seed={summary['seed']}) "
          f"— {summary['elapsed_seconds']}s")
    for name, payload in summary["benchmarks"].items():
        tags = ",".join(payload.get("tags", []))
        keys = [k for k in payload if k not in ("tags", "rows")]
        print(f"  {name:<12} [{tags}] {len(keys)} metric(s)")
        for k in keys:
            print(f"    {k}: {payload[k]}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LCM benchmark suite runner (Phase 11)")
    parser.add_argument("--full", action="store_true", help="Production-size runs")
    parser.add_argument("--seed", type=int, default=20260714, help="Random seed")
    parser.add_argument("--out", default=None, help="Custom output directory")
    args = parser.parse_args(argv)

    summary = run_benchmark_suite(
        quick=not args.full,
        seed=args.seed,
        output_dir=args.out or DEFAULT_OUTPUT_DIR,
    )
    _print_summary(summary)
    print(f"\nSaved summary to {summary['saved_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
