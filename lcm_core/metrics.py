"""Runtime metrics & telemetry for LCM (Phase 12).

A small, honest metrics registry — counters, gauges and latency histograms —
plus derived rates (rejection rate, conflict-resolution rate, replay/temporal
rejection rates, …). Instrumented points:

* ``writes.total`` / ``writes.status.<status>``        — pipeline outcomes
* ``pipeline.latency_ms`` (histogram)                  — end-to-end write latency
* ``evidence.verification.total``                      — crypto verifier calls
* ``evidence.replay_rejected``                         — Phase 5 nonce replays
* ``evidence.temporal_rejected``                       — Phase 4 expired/not-yet-valid
* ``evidence.signature_invalid``                       — bad/missing signatures
* ``lineage.nodes_recorded``                           — Phase 8 lineage writes
* ``trust.updates``                                    — verification feedback
* ``conflicts.resolved`` / ``conflicts.unresolved``    — pipeline conflict outcomes

No PII is collected: keys are agent-agnostic counters only.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Name -> kind, where kind ∈ {"counter", "gauge", "histogram"}.
METRIC_SCHEMA: Dict[str, str] = {
    "writes.total": "counter",
    "writes.status.committed": "counter",
    "writes.status.conflict_resolved": "counter",
    "writes.status.unresolved": "counter",
    "writes.status.rejected": "counter",
    "writes.status.rejected_untrusted": "counter",
    "writes.status.rejected_suspicious": "counter",
    "writes.status.loop_frozen": "counter",
    "writes.status.lock_failed": "counter",
    "writes.status.pipeline_error": "counter",
    "conflicts.resolved": "counter",
    "conflicts.unresolved": "counter",
    "evidence.verification.total": "counter",
    "evidence.replay_rejected": "counter",
    "evidence.temporal_rejected": "counter",
    "evidence.signature_invalid": "counter",
    "lineage.nodes_recorded": "counter",
    "trust.updates": "counter",
    "pipeline.latency_ms": "histogram",
}

# Derived rates: (numerator_counter, denominator_counter).
RATE_SPEC: Dict[str, tuple] = {
    "gate_rejection_rate": ("writes.status.rejected", "writes.total"),
    "untrusted_rejection_rate": ("writes.status.rejected_untrusted", "writes.total"),
    "conflict_resolution_rate": ("conflicts.resolved", "writes.total"),
    "conflict_unresolved_rate": ("conflicts.unresolved", "writes.total"),
    "replay_rejection_rate": ("evidence.replay_rejected", "evidence.verification.total"),
    "temporal_rejection_rate": ("evidence.temporal_rejected", "evidence.verification.total"),
    "signature_invalid_rate": ("evidence.signature_invalid", "evidence.verification.total"),
    "commit_rate": ("writes.status.committed", "writes.total"),
}

_STATUS_COUNTER = {
    "committed": "writes.status.committed",
    "conflict_resolved": "writes.status.conflict_resolved",
    "unresolved": "writes.status.unresolved",
    "rejected": "writes.status.rejected",
    "rejected_untrusted": "writes.status.rejected_untrusted",
    "rejected_suspicious": "writes.status.rejected_suspicious",
    "loop_frozen": "writes.status.loop_frozen",
    "lock_failed": "writes.status.lock_failed",
}


@dataclass
class MetricsRegistry:
    """Thread-safe-by-convention counters/gauges/histograms.

    Operations are plain dict updates; callers are single-threaded (asyncio
    event loop). ``snapshot()`` returns a copy so concurrent reads are safe.
    """

    counters: Dict[str, float] = field(default_factory=dict)
    gauges: Dict[str, float] = field(default_factory=dict)
    histograms: Dict[str, List[float]] = field(default_factory=dict)

    def incr(self, name: str, delta: float = 1.0) -> None:
        self.counters[name] = self.counters.get(name, 0.0) + delta

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = float(value)

    def observe(self, name: str, value: float) -> None:
        self.histograms.setdefault(name, []).append(float(value))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {k: list(v) for k, v in self.histograms.items()},
        }

    def reset(self) -> None:
        self.counters.clear()
        self.gauges.clear()
        self.histograms.clear()


_global_registry: Optional[MetricsRegistry] = None


def get_metrics_registry() -> MetricsRegistry:
    """Return the process-global metrics registry (lazily created)."""
    global _global_registry
    if _global_registry is None:
        _global_registry = MetricsRegistry()
    return _global_registry


def reset_metrics_registry() -> None:
    """Reset the global registry (tests / fresh process)."""
    global _global_registry
    if _global_registry is None:
        _global_registry = MetricsRegistry()
    _global_registry.reset()


def record_write_status(status: str) -> None:
    """Increment the total-write and per-status counters for one outcome."""
    reg = get_metrics_registry()
    reg.incr("writes.total")
    counter = _STATUS_COUNTER.get(status, "writes.status.pipeline_error")
    reg.incr(counter)
    if status == "conflict_resolved":
        reg.incr("conflicts.resolved")
    elif status == "unresolved":
        reg.incr("conflicts.unresolved")


def _mean(values: List[float]) -> Optional[float]:
    return round(statistics.mean(values), 3) if values else None


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round((q / 100.0) * (len(s) - 1))))
    return round(s[idx], 3)


def compute_metrics_snapshot(registry: Optional[MetricsRegistry] = None) -> Dict[str, Any]:
    """Build a structured snapshot with derived rates and latency percentiles."""
    reg = registry or get_metrics_registry()
    snap = reg.snapshot()
    counters = snap["counters"]

    rates: Dict[str, float] = {}
    for rate_name, (num, den) in RATE_SPEC.items():
        d = counters.get(den, 0.0)
        rates[rate_name] = round(counters.get(num, 0.0) / d, 6) if d > 0 else 0.0

    histograms = {}
    for name, values in snap["histograms"].items():
        histograms[name] = {
            "count": len(values),
            "mean_ms": _mean(values),
            "p50_ms": _percentile(values, 50),
            "p95_ms": _percentile(values, 95),
            "max_ms": round(max(values), 3) if values else None,
        }

    return {
        "counters": counters,
        "gauges": snap["gauges"],
        "rates": rates,
        "histograms": histograms,
        "generated_at": datetime.utcnow().isoformat(),
    }
