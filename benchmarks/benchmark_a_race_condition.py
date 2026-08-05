"""
Benchmark A — Race Condition / Lost Updates

Question: Does LCM prevent silent overwrites under concurrent writes?

Concurrency model note (report in paper methodology):
  All writers are asyncio coroutines on a single OS thread. Contention is
  cooperative (yield-based), not preemptive OS-thread contention. LCM's
  AsyncLockManager serialises access correctly within this model. To be
  transparent about what the benchmark measures, we run two modes:

  failure_rate > 0 (default, failure_rate=0.02):
    Injects 2% simulated write failures (timeout/network). One failure mask
    per trial is generated ONCE and passed to BOTH LCM and No-LCM, so the
    injected failure component is identical for the two systems and cannot
    confound the comparison. This models real-world transient errors. The
    naive baseline ceiling for No-LCM is (N-1)/N (all non-winners are lost)
    plus the injected failure rate. The LCM improvement over No-LCM reflects
    both the locking benefit and the identical injected failure component —
    these are reported separately.

  failure_rate=0.0 (pure locking mode):
    No injected failures. Shows the pure concurrency-protection contribution
    of LCM's locking layer in isolation. IMPORTANT: within asyncio, No-LCM
    loses exactly (N-1)/N writes because it is a destructive LAST-WRITE-WINS
    store (each write overwrites the single live slot). That is version
    RETENTION behaviour, not a "natural race" the way an OS-thread data race
    would be — the No-LCM N-1/N loss is the computed ceiling for a non-versioned
    single-slot store under sequentialised asyncio writes, not evidence of a
    concurrent race defect. LCM's 0-loss result demonstrates it records every
    version (winner live + losers archived), i.e. retention under contention.

Metrics:
  - lost_update_rate        : writes silently discarded / total writes
  - naive_ceiling           : (N-1)/N — theoretical maximum for No-LCM (reported)
  - locking_contribution    : No-LCM rate minus LCM rate (pure locking benefit)
  - mean_latency / p95      : per-write wall time (LCM locking cost vs No-LCM)
"""

from __future__ import annotations

import asyncio
import time
import random
from datetime import datetime
from typing import Any, Dict, List, Optional
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.schema import StampedUMF, ProvenanceInfo
from benchmarks.baseline import NoLCMStorage


# ---------------------------------------------------------------------------
# Minimal in-memory storage for the benchmark (avoids SQLite overhead)
# ---------------------------------------------------------------------------

class _DictStorage:
    """Thread-safe enough for asyncio (single-threaded event loop)."""

    def __init__(self):
        self._live: Dict[str, StampedUMF] = {}
        self._archive: List[str] = []
        self._pending: Dict[str, StampedUMF] = {}

    def get_existing(self, path: str):
        return self._live.get(path)

    def commit(self, umf: StampedUMF, path: str) -> None:
        self._live[path] = umf

    def commit_pending(self, umf: StampedUMF, path: str) -> None:
        self._pending[path] = umf

    def archive(self, provenance_id: str) -> None:
        self._archive.append(provenance_id)

    def update_provenance_fields(self, provenance_id: str, **kwargs) -> None:
        pass  # no-op for benchmark storage

    def all_committed_values(self, path: str):
        umf = self._live.get(path)
        return [umf.assertion_payload.get(path)] if umf else []

    def archive_count(self) -> int:
        return len(self._archive)


# ---------------------------------------------------------------------------
# LCM via WritePipeline
# ---------------------------------------------------------------------------

async def run_lcm_concurrent_writes(
    n_writers: int, path: str = "test.counter", failure_rate: float = 0.02,
    failure_mask: Optional[set] = None,
) -> Dict[str, Any]:
    storage = _DictStorage()
    pipeline = WritePipeline(
        storage=storage,
        trust_manager=TrustManager(),
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.0),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(rate_threshold=1000),  # disable loop-freeze for this bench
    )

    latencies: List[float] = []
    failed_writes = [0]
    start_time = time.perf_counter()

    async def writer(writer_id: int):
        await asyncio.sleep(random.uniform(0.001, 0.005))
        if (failure_mask is not None and writer_id in failure_mask) or \
           (failure_mask is None and random.random() < failure_rate):
            failed_writes[0] += 1
            return "failed"
        t0 = time.perf_counter()
        raw = {
            "agent_id": f"writer_{writer_id}",
            "session_id": "race_bench",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {path: writer_id},
        }
        result = await pipeline.process(raw)
        latencies.append(time.perf_counter() - t0)
        return result.status

    await asyncio.gather(*[writer(i) for i in range(n_writers)])
    total_time = time.perf_counter() - start_time

    live_value = storage.all_committed_values(path)
    archived = storage.archive_count()
    total_recorded = len(live_value) + archived
    lost_updates = n_writers - total_recorded
    lost_update_rate = max(0, lost_updates) / n_writers if n_writers > 0 else 0

    # Decomposition: injected failure contribution vs locking contribution
    injected_failure_rate = failed_writes[0] / n_writers if n_writers > 0 else 0
    naive_ceiling = (n_writers - 1) / n_writers if n_writers > 1 else 0  # last-write-wins upper bound

    latencies_sorted = sorted(latencies)
    return {
        "system": "LCM",
        "failure_rate_param": failure_rate,
        "n_writers": n_writers,
        "recorded_count": total_recorded,
        "lost_updates": max(0, lost_updates),
        "lost_update_rate": lost_update_rate,
        "injected_failure_rate": injected_failure_rate,
        "naive_ceiling": round(naive_ceiling, 4),
        "total_time": total_time,
        "mean_latency": sum(latencies) / len(latencies) if latencies else 0,
        "p95_latency": latencies_sorted[int(len(latencies_sorted) * 0.95)] if latencies_sorted else 0,
        "failed_writes": failed_writes[0],
    }


# ---------------------------------------------------------------------------
# No-LCM baseline
# ---------------------------------------------------------------------------

async def run_no_lcm_concurrent_writes(
    n_writers: int, path: str = "test.counter", failure_rate: float = 0.02,
    failure_mask: Optional[set] = None,
) -> Dict[str, Any]:
    storage = NoLCMStorage(":memory:")
    latencies: List[float] = []
    failed_writes = [0]
    start_time = time.perf_counter()

    async def writer(writer_id: int):
        await asyncio.sleep(random.uniform(0.001, 0.005))
        if (failure_mask is not None and writer_id in failure_mask) or \
           (failure_mask is None and random.random() < failure_rate):
            failed_writes[0] += 1
            return
        t0 = time.perf_counter()
        await asyncio.sleep(0)
        storage.write(path, f"writer_{writer_id}", writer_id, datetime.utcnow())
        latencies.append(time.perf_counter() - t0)

    await asyncio.gather(*[writer(i) for i in range(n_writers)])
    total_time = time.perf_counter() - start_time

    final = storage.read(path)
    recorded_count = 1 if final else 0
    lost_updates = n_writers - recorded_count - failed_writes[0]
    lost_update_rate = lost_updates / n_writers if n_writers > 0 else 0

    naive_ceiling = (n_writers - 1) / n_writers if n_writers > 1 else 0

    latencies_sorted = sorted(latencies)
    return {
        "system": "No-LCM",
        "failure_rate_param": failure_rate,
        "n_writers": n_writers,
        "recorded_count": recorded_count,
        "lost_updates": max(0, lost_updates),
        "lost_update_rate": max(0, lost_update_rate),
        "injected_failure_rate": failed_writes[0] / n_writers if n_writers > 0 else 0,
        "naive_ceiling": round(naive_ceiling, 4),
        "total_time": total_time,
        "mean_latency": sum(latencies) / len(latencies) if latencies else 0,
        "p95_latency": latencies_sorted[int(len(latencies_sorted) * 0.95)] if latencies_sorted else 0,
        "failed_writes": failed_writes[0],
    }


# ---------------------------------------------------------------------------
# Runner — two failure_rate modes
# ---------------------------------------------------------------------------

async def run_benchmark_a(
    n_writers_list: List[int] = [5, 20, 50],
    trials: int = 30,
    failure_rates: List[float] = [0.02, 0.0],
    on_trial=None,
) -> List[Dict[str, Any]]:
    """
    Run both systems across writer counts and failure rate modes.

    failure_rate=0.02 : includes 2% injected failures (models transient errors).
                        Both LCM and No-LCM receive identical failures so the
                        improvement reflects locking + identical noise.
    failure_rate=0.0  : pure locking mode. No-LCM loses exactly (N-1)/N writes
                        (last-write-wins ceiling). LCM approaches 0% loss.
                        This is the clean concurrency-isolation result.

    Both modes are written to the CSV so the paper can report them separately.
    """
    results = []
    for fr in failure_rates:
        for n_writers in n_writers_list:
            label = f"failure_rate={fr} N={n_writers}"
            print(f"\n=== Benchmark A: {label} ===")
            for trial in range(trials):
                # One failure mask per trial, shared by both systems, so the
                # injected failure component is IDENTICAL for LCM and No-LCM.
                failure_mask = (
                    set(i for i in range(n_writers)
                        if random.random() < fr)
                    if fr > 0 else set()
                )
                lcm = await run_lcm_concurrent_writes(
                    n_writers, failure_rate=fr, failure_mask=failure_mask)
                lcm["trial"] = trial
                results.append(lcm)

                no_lcm = await run_no_lcm_concurrent_writes(
                    n_writers, failure_rate=fr, failure_mask=failure_mask)
                no_lcm["trial"] = trial
                results.append(no_lcm)

                if on_trial is not None:
                    on_trial(results)

            if trials:
                import statistics as _s
                lcm_rows = [r for r in results if r["system"] == "LCM"
                            and r["n_writers"] == n_writers
                            and r["failure_rate_param"] == fr]
                rates = [r["lost_update_rate"] for r in lcm_rows]
                print(f"  LCM lost_update_rate={_s.mean(rates):.3f}±{_s.stdev(rates) if len(rates)>1 else 0:.3f}"
                      f"  naive_ceiling={(n_writers-1)/n_writers:.3f}")
    return results


def save_results_to_csv(
    results: List[Dict[str, Any]],
    output_dir: str = "benchmark_results",
) -> str:
    from pathlib import Path as _Path
    import csv as _csv
    out = _Path(output_dir)
    out.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = out / f"benchmark_a_race_condition_{ts}.csv"
    if not results:
        return ""
    with open(fname, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"Saved {len(results)} rows to {fname}")
    return str(fname)


if __name__ == "__main__":
    import statistics

    print("Benchmark A — Race Condition / Lost Updates")
    print("Both failure_rate=0.02 and failure_rate=0.0 (pure locking) modes")
    print("=" * 60)
    results = asyncio.run(run_benchmark_a(
        n_writers_list=[5, 20, 50], trials=30,
        failure_rates=[0.02, 0.0],
    ))
    save_results_to_csv(results)

    for fr in [0.02, 0.0]:
        print(f"\n--- failure_rate={fr} ---")
        for system in ["LCM", "No-LCM"]:
            for n in [5, 20, 50]:
                rows = [r for r in results if r["system"] == system
                        and r["n_writers"] == n and r["failure_rate_param"] == fr]
                if rows:
                    rates = [r["lost_update_rate"] for r in rows]
                    ceiling = rows[0]["naive_ceiling"]
                    print(f"  {system} N={n}: lost_update={statistics.mean(rates):.3f}±"
                          f"{statistics.stdev(rates) if len(rates)>1 else 0:.3f}"
                          f"  naive_ceiling={ceiling:.3f}")
