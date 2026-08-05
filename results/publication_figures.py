"""
Publication-grade figure suite for the LCM (Living Context Memory) paper.

Generates figures ONLY from real benchmark/experiment data cached in
``results/figures_data.json`` (schema v2) — no hardcoded numbers anywhere.

Figures
-------
  1.  fig_architecture.png        Illustrative write-path schematic (no data).
  2.  fig_race_condition.png      Benchmark A — lost-update rate & latency
                                  (LCM vs No-LCM, error bars, log-x).
  3.  fig_mandela_trapping.png    Benchmark B — contradiction-trapping
                                  efficiency vs attack repetitions (log-x).
  4.  fig_trust_gap_sweep.png     Benchmark B — trapping efficiency vs trust gap.
  5.  fig_benchmark_c_accuracy.png Benchmark C — accuracy by strategy.
  6.  fig_ablation.png            Benchmark D — per-component ablation.
  7.  fig_conflict_attribution.png Benchmark E — conflict rate distribution,
                                  correct-winner rate, deciding factor, Ψ margin.
  8.  fig_deciding_factor.png     Benchmark E — conflict share by deciding factor
                                  (bar + pie), synthetic by default; a
                                  real-Ollama split appears only when
                                  ``--include-real-agent`` supplies VALIDATED data.
  9.  fig_psi_explainer.png       One real conflict annotated: winner vs loser
                                  Ψ decomposition + deciding factor.
  10. fig_multi_agent_experiment.png Real-Ollama experiment (trust, write status,
                                  latency, memory ownership) — requires
                                  ``--include-real-agent`` with validated data.
  11. fig_sensitivity_sweeps.png  Parameter sensitivity (synthetic/model behaviour).
  12. fig_results_comparison.png  Benchmark C accuracy vs baselines + Benchmark E
                                  conflict behaviour — the real-Ollama panel is
                                  only plotted from VALIDATED real artifacts.

Data provenance
---------------
The cache records, per source artifact, ``{path, sha256, created_at, backend,
agent_mode, model, trials, validated}`` plus ``schema_version / generated_at /
git_commit / python_version / platform / benchmark_versions / config``.  A
``results/figures/manifest.json`` records every generated/skipped figure, its
source-cache hash, artifact hashes, git commit and data classification
(``synthetic`` / ``real-agent`` / ``illustrative``).

Real-agent honesty rule
-----------------------
A figure may only be labeled ``real-agent`` when every experiment it uses
passes :func:`is_verified_real_ollama_result` (genuine ``ollama`` backend,
``real_llm`` mode, ``llm_available``, non-empty write log, and no self-assigned
elevated evidence).  Unvalidated or excluded artifacts are never plotted — the
figure is SKIPPED and recorded in the manifest with the reason.

Usage
-----
    python results/publication_figures.py                       # cache, else run + cache
    python results/publication_figures.py --force               # re-run benchmarks + rebuild cache
    python results/publication_figures.py --cache-only          # cache only; error if missing
    python results/publication_figures.py --include-real-agent  # include VALIDATED real-Ollama data
    python results/publication_figures.py --figures fig_ablation fig_deciding_factor

Legacy aliases: ``--no-cache`` == ``--force``, ``--plot-only`` == ``--cache-only``,
``--only`` == ``--figures``.

Requires matplotlib (numpy optional).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "results"
FIG_DIR = RESULTS / "figures"
CACHE = RESULTS / "figures_data.json"
EXP_DIR = ROOT / "experiments" / "results"

FIG_DIR.mkdir(parents=True, exist_ok=True)

CACHE_SCHEMA_VERSION = 2

# Source file (per benchmark section) whose sha256 is recorded as the benchmark
# "version" in the cache — content-addressed provenance, honest by construction.
BENCHMARK_SOURCES = {
    "benchmark_a": ROOT / "benchmarks" / "benchmark_a_race_condition.py",
    "benchmark_b": ROOT / "benchmarks" / "benchmark_b_mandela.py",
    "benchmark_c": ROOT / "benchmarks" / "benchmark_c_evaluation.py",
    "benchmark_d": ROOT / "benchmarks" / "benchmark_d_ablation.py",
    "benchmark_e": ROOT / "benchmarks" / "benchmark_e_overlapping_writes.py",
}

# Which cache sections each figure consumes (drives per-figure artifact hashes
# in the manifest).
FIGURE_SOURCES: Dict[str, List[str]] = {
    "fig_architecture": [],
    "fig_race_condition": ["benchmark_a"],
    "fig_mandela_trapping": ["benchmark_b"],
    "fig_trust_gap_sweep": ["benchmark_b"],
    "fig_benchmark_c_accuracy": ["benchmark_c"],
    "fig_ablation": ["benchmark_d"],
    "fig_conflict_attribution": ["benchmark_e"],
    "fig_deciding_factor": ["benchmark_e", "experiments"],
    "fig_psi_explainer": ["benchmark_e"],
    "fig_multi_agent_experiment": ["experiments"],
    "fig_sensitivity_sweeps": [],
    "fig_results_comparison": ["benchmark_c", "benchmark_e", "experiments"],
}

# Data classification for the manifest (synthetic / real-agent / illustrative /
# mixed).  A figure is "real-agent" only when validated experiment data was
# actually used; otherwise it stays synthetic.
FIGURE_CLASSIFICATION: Dict[str, str] = {
    "fig_architecture": "illustrative",
    "fig_race_condition": "synthetic",
    "fig_mandela_trapping": "synthetic",
    "fig_trust_gap_sweep": "synthetic",
    "fig_benchmark_c_accuracy": "synthetic",
    "fig_ablation": "synthetic",
    "fig_conflict_attribution": "synthetic",
    "fig_deciding_factor": "synthetic",  # upgraded to real-agent only if real data used
    "fig_psi_explainer": "synthetic",
    "fig_multi_agent_experiment": "real-agent",
    "fig_sensitivity_sweeps": "synthetic",
    "fig_results_comparison": "synthetic",  # upgraded to mixed/real-agent if real panel drawn
}

# ─────────────────────────────────────────────────────────────────────────
# Style system — consistent, colorblind-safe, publication-oriented.
# ─────────────────────────────────────────────────────────────────────────

# Okabe-Ito colorblind-safe palette
C = {
    "blue":   "#0072B2",
    "orange": "#E69F00",
    "green":  "#009E73",
    "sky":    "#56B4E9",
    "verm":   "#D55E00",
    "purple": "#CC79A7",
    "yellow": "#F0E442",
    "grey":   "#8C8C8C",
}

STRATEGY_COLORS = {
    "LCM":             C["green"],
    "LCM_cold_start":  C["sky"],
    "last_write_wins": C["verm"],
    "recency_only":    C["orange"],
    "majority_voting": C["purple"],
    "fixed_trust":     C["grey"],
    "FixedTrust":      C["grey"],
}

SCENARIO_ORDER = ["high_trust_vs_low_trust", "recency_dominated", "cold_start"]
SCENARIO_LABEL = {
    "high_trust_vs_low_trust": "High trust vs low trust",
    "recency_dominated":       "Recency dominated",
    "cold_start":              "Cold start",
}


def apply_style() -> None:
    plt.rcParams.update({
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#DDDDDD",
        "grid.alpha": 0.8,
        "grid.linestyle": "--",
        "axes.axisbelow": True,
    })


def _ci95(values: List[float]) -> float:
    """95% CI half-width (mean ± x)."""
    values = [v for v in values if v is not None]
    if not values:
        return 0.0
    n = len(values)
    if n < 2:
        return 0.0
    m = statistics.mean(values)
    sd = statistics.stdev(values)
    z = 1.96 if n >= 30 else 2.045 if n >= 10 else 2.26 if n >= 5 else 2.78
    return z * sd / (n ** 0.5)


def _mean_ci(values: List[float]) -> Dict[str, float]:
    return {"mean": statistics.mean(values), "ci": _ci95(values)}


def _save(fig, name: str) -> Path:
    path = FIG_DIR / name
    fig.savefig(path)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)
    print(f"  [OK] {path.name} (+ .pdf)")
    return path


def _finalize(fig, top: float = 0.97) -> None:
    """Space subplots so inner titles never collide with panels above/below."""
    fig.tight_layout(rect=[0.0, 0.0, 1.0, top])


class FigureSkip(Exception):
    """Raised by a figure function when it honestly has no data to plot.

    The reason is recorded verbatim in the manifest so skipped figures stay
    auditable rather than silently absent.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ─────────────────────────────────────────────────────────────────────────
# Provenance / cache helpers
# ─────────────────────────────────────────────────────────────────────────

def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=3, check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_text(path.read_text(encoding="utf-8"))
    except OSError:
        return "unavailable"


def _file_created(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_ctime).isoformat()
    except OSError:
        return "unknown"


def _real_artifact_source(path: Path, d: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ``sources.experiments`` provenance entry for one artifact."""
    stats = d.get("stats") or {}
    return {
        "path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        "sha256": _sha256_file(path),
        "created_at": _file_created(path),
        "backend": d.get("backend"),
        "agent_mode": d.get("agent_mode"),
        "model": d.get("model"),
        "trials": d.get("trials"),
        "validated": True,
    }


def is_verified_real_ollama_result(d: Any) -> Tuple[bool, str]:
    """Strictly validate that an artifact is a genuine real-Ollama run.

    Returns ``(True, "")`` only when ALL of the following hold:

    * ``backend`` is ``"ollama"`` (or a real-LLM backend value);
    * ``agent_mode == "real_llm"``;
    * ``llm_available`` is truthy;
    * ``stats.total_writes > 0``;
    * ``write_log`` is a non-empty list;
    * every write carries no self-assigned elevated evidence type —
      agents must NOT be able to claim ``database`` / ``tool_output`` /
      ``document`` / ``user_input`` provenance.  Allowed values are the
      middleware-assigned fallbacks (``agent_claim_default`` /
      ``middleware_assigned``) or absent evidence fields.

    Anything else (synthetic fallback, fabricated fields, or evidence forgery)
    fails closed so no figure can mislabel it as a real-agent result.
    """
    if not isinstance(d, dict):
        return False, "artifact is not a dict"
    from experiments.result_schema import validate_real_agent_artifact
    strict = validate_real_agent_artifact(d)
    if not strict["valid"]:
        return False, "; ".join(strict["issues"])
    if d.get("backend") not in ("ollama", "langchain"):
        return False, f"backend={d.get('backend')!r} is not a real-LLM backend"
    if d.get("agent_mode") != "real_llm":
        return False, f"agent_mode={d.get('agent_mode')!r} != 'real_llm'"
    if not d.get("llm_available"):
        return False, "llm_available is not truthy"
    stats = d.get("stats")
    if not isinstance(stats, dict) or not stats.get("total_writes", 0) > 0:
        return False, "stats.total_writes is missing or zero"
    write_log = d.get("write_log")
    if not isinstance(write_log, list) or len(write_log) == 0:
        return False, "write_log is empty or missing"

    forbidden = {"database", "tool_output", "document", "user_input"}
    for i, w in enumerate(write_log):
        if not isinstance(w, dict):
            continue
        et = (w.get("evidence_type") or "").lower()
        es = (w.get("evidence_source") or "").lower()
        if et in forbidden or es in forbidden:
            return False, (
                f"write_log[{i}] self-assigns elevated evidence type "
                f"{et!r} (agents cannot claim provider authority)"
            )
    return True, ""


def collect_experiments() -> List[Dict[str, Any]]:
    """Load and VALIDATE real-Ollama experiment JSONs under experiments/results.

    Only artifacts that pass :func:`is_verified_real_ollama_result` are kept.
    Invalid artifacts are reported (never plotted).  Each returned entry carries
    the ``_source`` provenance field used by the cache ``sources`` section.
    """
    experiments: List[Dict[str, Any]] = []
    if not EXP_DIR.exists():
        return experiments
    for f in sorted(EXP_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {f.name}: unreadable ({exc})")
            continue
        ok, reason = is_verified_real_ollama_result(d)
        if not ok:
            print(f"  [skip] {f.name}: not a validated real-Ollama artifact ({reason})")
            continue
        d["_source"] = _real_artifact_source(f, d)
        experiments.append(d)
    return experiments


def _benchmark_version(key: str) -> str:
    """Content-addressed benchmark version (sha256 of the benchmark source)."""
    src = BENCHMARK_SOURCES.get(key)
    if src is None or not src.exists():
        return "unknown"
    return f"sha256:{_sha256_file(src)[:16]}"


def collect_benchmarks(live: Optional[Dict[str, Any]] = None,
                       live_renderer: Optional["_LiveRenderer"] = None) -> Dict[str, Any]:
    import asyncio

    print("Running Benchmarks A–E in-process (deterministic)...")
    data: Dict[str, Any] = {}

    def trial_cb(key: str, transform=None):
        def _cb(rows: List[Any]) -> None:
            if live_renderer is None:
                return
            out = [transform(r) for r in rows] if transform else rows
            live_renderer.render(key, out)
        return _cb

    from benchmarks.benchmark_a_race_condition import run_benchmark_a
    from benchmarks.benchmark_b_mandela import (
        run_benchmark_b, TRUST_GAP_SWEEP_FINE, PRODUCTION_UNCERTAINTY_THRESHOLD,
        SWEEP_TRUST_HALF_LIFE_DAYS,
    )
    from benchmarks.benchmark_c_evaluation import run_benchmark_c
    from benchmarks.benchmark_d_ablation import run_benchmark_d
    from benchmarks.benchmark_e_overlapping_writes import run_benchmark_e

    a = asyncio.run(run_benchmark_a(n_writers_list=[5, 20, 50, 100], trials=25,
                                    on_trial=trial_cb("benchmark_a")))
    data["benchmark_a"] = a
    if live is not None:
        live["benchmark_a"] = a

    b = asyncio.run(run_benchmark_b(repetition_counts=[1, 10, 50, 200], trials=25,
                                    trust_pairs=TRUST_GAP_SWEEP_FINE,
                                    uncertainty_threshold=PRODUCTION_UNCERTAINTY_THRESHOLD,
                                    trust_half_life_days=SWEEP_TRUST_HALF_LIFE_DAYS,
                                    on_trial=trial_cb("benchmark_b")))
    data["benchmark_b"] = b
    if live is not None:
        live["benchmark_b"] = b

    c = asyncio.run(run_benchmark_c(trials_per_scenario=40,
                                    on_trial=trial_cb("benchmark_c")))
    data["benchmark_c"] = c
    if live is not None:
        live["benchmark_c"] = c

    d = asyncio.run(run_benchmark_d(trials_per_scenario=40,
                                    on_trial=trial_cb("benchmark_d")))
    data["benchmark_d"] = d
    if live is not None:
        live["benchmark_d"] = d

    e = asyncio.run(run_benchmark_e(trials=20,
                                    on_trial=trial_cb("benchmark_e", transform=_trial_result_to_dict)))
    data["benchmark_e"] = [_trial_result_to_dict(r) for r in e]
    if live is not None:
        live["benchmark_e"] = data["benchmark_e"]

    return data


def _trial_result_to_dict(r: Any) -> Dict[str, Any]:
    return {
        "trial": r.trial,
        "topic": r.topic,
        "total_writes": r.total_writes,
        "conflicts_detected": r.conflicts_detected,
        "conflict_rate": r.conflict_rate,
        "verification_wins": r.verification_wins,
        "verification_win_rate": r.verification_win_rate,
        "lost_updates": r.lost_updates,
        "final_consistent": r.final_consistent,
        "overlap_ratio_requested": getattr(r, "overlap_ratio_requested", None),
        "overlap_ratio_realized": getattr(r, "overlap_ratio_realized", None),
        "resolved_paths": r.resolved_paths,
        "conflict_log": r.conflict_log,
    }


def build_cache_payload(data: Dict[str, Any], include_real_agent: bool,
                        experiments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Wrap raw benchmark/experiment rows in the schema-v2 provenance envelope."""
    experiments = experiments if experiments is not None else data.get("experiments", [])
    sources: Dict[str, Any] = {
        key: {
            "path": str(src.relative_to(ROOT) if src.is_relative_to(ROOT) else src),
            "sha256": _sha256_file(src),
            "created_at": _file_created(src),
            "backend": "in-process",
            "agent_mode": "synthetic",
            "model": None,
            "trials": None,
            "validated": True,
        }
        for key, src in BENCHMARK_SOURCES.items()
    }
    sources["experiments"] = [e.get("_source") for e in experiments]

    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "git_commit": _git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "benchmark_versions": {k: _benchmark_version(k) for k in BENCHMARK_SOURCES},
        "config": {
            "include_real_agent": bool(include_real_agent),
            "collect": {
                "benchmark_a": {"n_writers_list": [5, 20, 50, 100], "trials": 25},
                "benchmark_b": {"repetition_counts": [1, 10, 50, 200], "trials": 25,
                                "uncertainty_threshold": 0.05},
                "benchmark_c": {"trials_per_scenario": 40},
                "benchmark_d": {"trials_per_scenario": 40},
                "benchmark_e": {"trials": 20},
            },
        },
        "sources": sources,
    }
    for key in BENCHMARK_SOURCES:
        payload[key] = data.get(key, [])
    payload["experiments"] = experiments
    return payload


def _read_cache(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        print(f"  [warn] cache unreadable ({exc}); will rebuild")
        return None
    if data.get("schema_version", 1) < CACHE_SCHEMA_VERSION:
        print(f"  [warn] cache schema v{data.get('schema_version', 1)} is stale "
              f"(need v{CACHE_SCHEMA_VERSION}); will rebuild")
        return None
    return data


def load_data(use_cache: bool = True, force: bool = False,
              include_real_agent: bool = False,
              strict: bool = False) -> Dict[str, Any]:
    if use_cache and not force:
        cached = _read_cache(CACHE)
        if cached is not None:
            print(f"Loading cached data from {CACHE}")
            if include_real_agent and not cached.get("config", {}).get("include_real_agent"):
                print("  [warn] cache was built WITHOUT --include-real-agent; "
                      "real-agent figures will be skipped")
            return cached
        if strict:
            raise SystemExit(
                f"Cache {CACHE} is missing or schema v{CACHE_SCHEMA_VERSION} is stale; "
                "run WITHOUT --cache-only to rebuild it first.")

    live: Dict[str, Any] = {}
    renderer = _LiveRenderer(live)
    experiments = collect_experiments() if include_real_agent else []
    print(f"Real-time figure rendering ON — watch {FIG_DIR}/ update during collection")

    renderer.data["experiments"] = experiments
    data = collect_benchmarks(live=live, live_renderer=renderer)
    data["experiments"] = experiments

    payload = build_cache_payload(data, include_real_agent, experiments)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print(f"Saved data cache to {CACHE}")
    return payload


# Figures that can be rendered incrementally per benchmark (in real time)
BENCHMARK_FIGURES = {
    "benchmark_a": ["fig_race_condition"],
    "benchmark_b": ["fig_mandela_trapping", "fig_trust_gap_sweep"],
    "benchmark_c": ["fig_benchmark_c_accuracy"],
    "benchmark_d": ["fig_ablation"],
    "benchmark_e": ["fig_conflict_attribution", "fig_psi_explainer",
                    "fig_deciding_factor", "fig_results_comparison"],
}

# Benchmarks that must finish before their figure is meaningful
FINAL_ONLY_FIGURES = ["fig_multi_agent_experiment", "fig_sensitivity_sweeps",
                      "fig_architecture"]


class _LiveRenderer:
    """Re-renders figures in real time as benchmark trials accumulate."""

    def __init__(self, data: Dict[str, Any], throttle_s: float = 3.0):
        self.data = data
        self.throttle_s = throttle_s
        self._fig_lookup = {name: fn for name, fn in ALL_FIGURES}
        self._last = 0.0

    def render_many(self, names: List[str]) -> None:
        for name in names:
            try:
                self._fig_lookup[name](self.data)
            except Exception as exc:  # noqa: BLE001
                print(f"  [live] {name}: {exc}")

    def render(self, key: str, rows: List[Any]) -> None:
        self.data[key] = rows
        now = time.time()
        if now - self._last < self.throttle_s:
            return
        self._last = now
        for name in BENCHMARK_FIGURES.get(key, []):
            self.render_many([name])
            print(f"  [live] {name} updated ({len(rows)} rows so far)")


# ─────────────────────────────────────────────────────────────────────────
# Real-agent aggregation helpers (validator-gated)
# ─────────────────────────────────────────────────────────────────────────

def _validated_experiments(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    exps = data.get("experiments", [])
    return [e for e in exps if e.get("_source", {}).get("validated")]


def _real_e_conflicts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Conflict-log entries from validated real-Ollama artifacts (if any)."""
    out: List[Dict[str, Any]] = []
    for e in _validated_experiments(data):
        for c in e.get("conflict_log", []) or []:
            if isinstance(c, dict) and c.get("driver"):
                out.append(c)
    return out


def _real_e_aggregate(data: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Aggregate validated real-Ollama conflict behaviour, or None.

    Returns None unless at least one validated artifact actually produced
    conflicts (a zero-conflict smoke test is weak, honest data, but it does not
    support a conflict-behaviour panel).
    """
    total_writes = 0
    total_conflicts = 0
    total_resolved = 0
    tool_success = 0
    tool_calls = 0
    any_conflict_artifact = False
    for e in _validated_experiments(data):
        stats = e.get("stats") or {}
        total_writes += stats.get("total_writes", 0)
        n_conf = stats.get("conflict_resolved", 0) + stats.get("conflict_unresolved", 0)
        total_conflicts += n_conf
        total_resolved += stats.get("conflict_resolved", 0)
        tool_calls += stats.get("tool_call_failed", 0)
        tool_success += stats.get("committed", 0)
        if n_conf > 0:
            any_conflict_artifact = True
    if not any_conflict_artifact or total_writes <= 0:
        return None
    return {
        "conflict_rate": total_conflicts / total_writes,
        "resolved_rate": total_resolved / total_conflicts if total_conflicts else 0.0,
        "tool_success_rate": (tool_success / total_writes) if total_writes else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────
# Figure 1 — Architecture (illustrative, non-crossing)
# ─────────────────────────────────────────────────────────────────────────

def figure_architecture(data: Dict[str, Any]) -> Path:
    import matplotlib.patches as mpatches

    apply_style()
    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    agents = [
        (0.03, 0.60, "Agent A", C["sky"]),
        (0.03, 0.30, "Agent B", C["sky"]),
    ]
    umf_box = (0.26, 0.40, 0.20, 0.20, "UMF packet\n(assertion + confidence)", "#fff3cd")
    stages = [
        (0.54, 0.72, 0.20, 0.20, "validate & stamp\n(Ed25519 evidence)", "#e2f0d9"),
        (0.54, 0.40, 0.20, 0.20, "loop-detect / lock", "#e2f0d9"),
        (0.54, 0.08, 0.20, 0.20, "conflict\nresolution (Ψ)", "#f8d7da"),
    ]
    storage = (0.82, 0.40, 0.16, 0.20, "storage\n(active + archive)", "#dbe9f6")

    def box(rect):
        x, y, w, h, label, color = rect
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.01", fc=color, ec="#333333", lw=1.2))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=9, wrap=True)

    for x, y, label, color in agents:
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), 0.16, 0.14, boxstyle="round,pad=0.01", fc=color, ec="#333333", lw=1.2))
        ax.text(x + 0.08, y + 0.07, label, ha="center", va="center", fontsize=9)
    box(umf_box)
    for s in stages:
        box(s)
    box(storage)

    def arrow(x1, y1, x2, y2):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color="#333333", lw=1.4))

    # Agents -> UMF (diagonal in, never crossing: A is above, B below, UMF centred)
    arrow(0.19, 0.67, 0.26, 0.53)
    arrow(0.19, 0.37, 0.26, 0.47)
    # UMF -> pipeline stages (fan out, horizontal)
    arrow(0.46, 0.52, 0.54, 0.82)
    arrow(0.46, 0.50, 0.54, 0.50)
    arrow(0.46, 0.48, 0.54, 0.18)
    # Pipeline stages -> storage (fan in, horizontal, no crossings)
    arrow(0.74, 0.82, 0.82, 0.54)
    arrow(0.74, 0.50, 0.82, 0.50)
    arrow(0.74, 0.18, 0.82, 0.46)

    ax.text(0.50, 0.97, "Living Context Memory — write path (deterministic, zero LLM calls)",
            ha="center", fontsize=12, weight="bold")
    ax.text(0.50, 0.97 - 0.035, "Illustrative schematic — no benchmark data",
            ha="center", fontsize=9, style="italic", color=C["grey"])
    ax.text(0.50, 0.02, "Ψ = w_r·Recency + w_c·Confidence + w_t·Trust + w_p·Provenance",
            ha="center", fontsize=10, style="italic")
    _finalize(fig)
    return _save(fig, "fig_architecture.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 2 — Benchmark A: race condition
# ─────────────────────────────────────────────────────────────────────────

def figure_race_condition(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_a", [])
    if not rows:
        raise FigureSkip("no data")

    apply_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    groups: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[r["system"]][r["n_writers"]].append(r["lost_update_rate"])
    lat_groups: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        lat_groups[r["system"]][r["n_writers"]].append(r.get("mean_latency", 0) * 1000)

    n_vals = sorted({r["n_writers"] for r in rows})
    for system, color, marker in [
        ("LCM", C["green"], "o"),
        ("No-LCM", C["verm"], "s"),
    ]:
        means, cis = [], []
        for n in n_vals:
            m = _mean_ci(groups[system][n])
            means.append(m["mean"])
            cis.append(m["ci"])
        ax1.errorbar(n_vals, means, yerr=cis, marker=marker, lw=2, capsize=4,
                     color=color, label=system)
        lmeans, lcis = [], []
        for n in n_vals:
            m = _mean_ci(lat_groups[system][n])
            lmeans.append(m["mean"])
            lcis.append(m["ci"])
        ax2.errorbar(n_vals, lmeans, yerr=lcis, marker=marker, lw=2, capsize=4,
                     color=color, label=system)

    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_xticks(n_vals)
        ax.set_xticklabels([str(v) for v in n_vals])
        ax.set_xlabel("Concurrent writers N")
        ax.legend(frameon=False)

    ax1.set_ylabel("Lost update rate")
    ax1.set_ylim(-0.02, 1.05)
    ax1.set_title("(a) Lost updates — LCM vs no-LCM")
    ax2.set_ylabel("Mean latency (ms)")
    ax2.set_yscale("log")
    ax2.set_title("(b) Write latency scaling")

    fig.suptitle("Benchmark A — Race condition under concurrent writers (mean ± 95% CI)",
                 y=1.02, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_race_condition.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 3 — Benchmark B: Mandela / contradiction trapping
# ─────────────────────────────────────────────────────────────────────────

def figure_mandela_trapping(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_b", [])
    if not rows:
        raise FigureSkip("no data")

    apply_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    def _trap(r):
        return r.get("trapping_efficiency", r.get("contradiction_trapping_efficiency", 0))

    groups: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    corr: Dict[str, Dict[int, List[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        # Keep only the extreme (1.0, 0.0) trust pairing here; the sweep has its
        # own figure (fig_trust_gap_sweep). No-LCM rows carry trust_gap=None.
        if r["system"] == "LCM" and r.get("trust_gap") not in (None, 1.0):
            continue
        groups[r["system"]][r["repetitions"]].append(_trap(r))
        corr[r["system"]][r["repetitions"]].append(r["final_is_correct"])

    reps = sorted({r["repetitions"] for r in rows})
    for system, color, marker in [
        ("LCM", C["green"], "o"),
        ("No-LCM", C["verm"], "s"),
    ]:
        means, cis = [], []
        for rep in reps:
            m = _mean_ci(groups[system][rep])
            means.append(m["mean"]); cis.append(m["ci"])
        ax1.errorbar(reps, means, yerr=cis, marker=marker, lw=2, capsize=4,
                     color=color, label=system)
        cmeans = [statistics.mean(corr[system][rep]) for rep in reps]
        ax2.plot(reps, cmeans, marker=marker, lw=2, color=color, label=system)

    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_xticks(reps)
        ax.set_xticklabels([str(v) for v in reps])
        ax.set_xlabel("Attack repetitions R")
        ax.legend(frameon=False, loc="center right")
    ax1.set_ylabel("Contradiction trapping efficiency")
    ax1.set_ylim(-0.02, 1.1)
    ax1.set_title("(a) Trapping efficiency")
    ax2.set_ylabel("Final value correct (rate)")
    ax2.set_ylim(-0.02, 1.1)
    ax2.set_title("(b) Memory integrity")

    fig.suptitle("Benchmark B — False-memory (Mandela) repetition attack (mean ± 95% CI)",
                 y=1.02, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_mandela_trapping.png")


# Figure 3b — Benchmark B: trapping efficiency vs trust gap
# ─────────────────────────────────────────────────────────────────────────

def figure_trust_gap_sweep(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_b", [])
    lcm_rows = [r for r in rows if r["system"] == "LCM" and r.get("trust_gap") is not None]
    if not lcm_rows:
        raise FigureSkip("no sweep data")

    apply_style()
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15.5, 4.2))

    gaps = sorted({r["trust_gap"] for r in lcm_rows})
    reps = sorted({r["repetitions"] for r in lcm_rows})
    palette = {"1": "#0b3d91", "10": "#2e7d32", "50": "#e07a2f", "200": "#7a3fa0"}
    palette = {r: palette[str(r)] if str(r) in palette else C["blue"] for r in reps}

    for rep in reps:
        traps = {g: [] for g in gaps}
        unrs = {g: [] for g in gaps}
        corrs = {g: [] for g in gaps}
        for r in lcm_rows:
            if r["repetitions"] != rep:
                continue
            traps[r["trust_gap"]].append(r["trapping_efficiency"])
            unrs[r["trust_gap"]].append(r.get("unresolved_rate", 0.0))
            corrs[r["trust_gap"]].append(r["final_is_correct"])
        m1, c1, m2, m3 = [], [], [], []
        for g in gaps:
            mm = _mean_ci(traps[g])
            m1.append(mm["mean"]); c1.append(mm["ci"])
            m2.append(statistics.mean(unrs[g]))
            m3.append(statistics.mean(corrs[g]))
        ax1.errorbar(gaps, m1, yerr=c1, marker="o", lw=2, capsize=4,
                     color=palette[rep], label=f"R={rep}")
        ax2.plot(gaps, m2, marker="o", lw=2, color=palette[rep], label=f"R={rep}")
        ax3.plot(gaps, m3, marker="o", lw=2, color=palette[rep], label=f"R={rep}")

    for ax in (ax1, ax2, ax3):
        ax.set_xlabel("Trust gap (trusted − attacker)")
        ax.set_xlim(0.0, 1.05)
        ax.axvline(0.0, color="#888", lw=1, ls=":")
        ax.legend(frameon=False, loc="center right")
    ax1.set_ylabel("Contradiction trapping efficiency")
    ax1.set_ylim(-0.02, 1.1)
    ax1.set_title("(a) Trapping efficiency vs trust gap")
    ax2.set_ylabel("Unresolved rate (declined to resolve)")
    ax2.set_ylim(-0.02, 1.1)
    ax2.set_title("(b) Ambiguity handling")
    ax3.set_ylabel("Final value correct (rate)")
    ax3.set_ylim(-0.02, 1.1)
    ax3.set_title("(c) Memory integrity")

    fig.suptitle(
        "Benchmark B — Trust-gap sweep (25 trials/condition, production uncertainty "
        "threshold 0.05, mean ± 95% CI)",
        y=1.02, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_trust_gap_sweep.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 4 — Benchmark C: accuracy vs baselines
# ─────────────────────────────────────────────────────────────────────────

def figure_benchmark_c(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_c", [])
    if not rows:
        raise FigureSkip("no data")

    apply_style()
    strategies = ["LCM", "LCM_cold_start", "last_write_wins", "recency_only",
                  "majority_voting", "fixed_trust"]
    strategies = [s for s in strategies if any(r["strategy"] == s for r in rows)]
    display = {
        "LCM": "LCM",
        "LCM_cold_start": "LCM cold-start",
        "last_write_wins": "Last write\nwins",
        "recency_only": "Recency\nonly",
        "majority_voting": "Majority\nvoting",
        "fixed_trust": "Fixed\ntrust",
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.6))

    accs, cis = [], []
    for s in strategies:
        correct = [r["correct"] for r in rows if r["strategy"] == s]
        accs.append(statistics.mean(correct))
        cis.append(_ci95(correct))
    x = np.arange(len(strategies))
    bars = ax1.bar(x, accs, yerr=cis, width=0.62,
                   color=[STRATEGY_COLORS.get(s, C["grey"]) for s in strategies],
                   capsize=4)
    ax1.axhline(0.5, color="grey", ls=":", lw=1)
    for b, v in zip(bars, accs):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.1%}",
                 ha="center", fontsize=9)
    ax1.set_xticks(x)
    ax1.set_xticklabels([display.get(s, s) for s in strategies], fontsize=9)
    ax1.set_ylabel("Accuracy (correct winner)")
    ax1.set_ylim(0, 1.15)
    ax1.set_title("(a) Overall accuracy across all scenarios")

    scenario_types = [s for s in SCENARIO_ORDER if any(r["scenario_type"] == s for r in rows)]
    width = 0.13
    x = np.arange(len(scenario_types))
    for i, s in enumerate(strategies):
        means, cis2 = [], []
        for st in scenario_types:
            correct = [r["correct"] for r in rows if r["strategy"] == s and r["scenario_type"] == st]
            means.append(statistics.mean(correct) if correct else 0.0)
            cis2.append(_ci95(correct))
        ax2.bar(x + (i - len(strategies) / 2 + 0.5) * width, means, width,
                yerr=cis2, capsize=2, color=STRATEGY_COLORS.get(s, C["grey"]),
                label="LCM" if s == "LCM" else display.get(s, s).replace("\n", " "))
    ax2.set_xticks(x)
    ax2.set_xticklabels([SCENARIO_LABEL.get(s, s) for s in scenario_types], fontsize=9)
    ax2.set_ylabel("Accuracy")
    ax2.set_ylim(0, 1.15)
    ax2.legend(frameon=False, fontsize=8, ncol=2, loc="upper right")
    ax2.set_title("(b) Per-scenario accuracy")

    fig.suptitle("Benchmark C — Correct-winner accuracy: LCM vs baselines",
                 y=1.02, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_benchmark_c_accuracy.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 5 — Benchmark D: ablation
# ─────────────────────────────────────────────────────────────────────────

def figure_ablation(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_d", [])
    if not rows:
        raise FigureSkip("no data")

    apply_style()
    conditions = ["Full", "-Recency", "-Confidence", "-Trust", "-Provenance"]
    conditions = [c for c in conditions if any(r["condition"] == c for r in rows)]
    fixture_sets = [s for s in ["benchmark_c", "corrected_diagnostic_v2", "frozen_held_out"]
                    if any(r.get("fixture_set") == s for r in rows)]
    set_title = {
        "benchmark_c":             "Benchmark C scenarios (continuity)",
        "corrected_diagnostic_v2": "Corrected diagnostic (calibrated, not held-out)",
        "frozen_held_out":         "Frozen held-out (independent)",
    }

    fig, axes = plt.subplots(1, len(fixture_sets), figsize=(5.2 * len(fixture_sets), 4.6))
    if len(fixture_sets) == 1:
        axes = [axes]
    width = 0.18
    palette = [C["green"], C["orange"], C["sky"], C["purple"], C["verm"]]

    for ax, fs in zip(axes, fixture_sets):
        sub = [r for r in rows if r.get("fixture_set") == fs]
        types = []
        for r in sub:
            t = r.get("scenario_type")
            if t not in types:
                types.append(t)
        x = np.arange(len(types))
        for i, cond in enumerate(conditions):
            means, cis = [], []
            for st in types:
                correct = [r["resolved_correct"] for r in sub
                           if r["condition"] == cond and r.get("scenario_type") == st]
                if correct:
                    means.append(statistics.mean(correct))
                    cis.append(_ci95(correct))
                else:
                    means.append(0.0)
                    cis.append(0.0)
            ax.bar(x + (i - len(conditions) / 2 + 0.5) * width, means, width,
                   yerr=cis, capsize=2, color=palette[i], label=cond)
        ax.axhline(1.0, color="grey", ls=":", lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels(types, rotation=30, ha="right")
        ax.set_ylabel("Strict accuracy (resolved correct / n)")
        ax.set_ylim(0, 1.15)
        ax.set_title(set_title.get(fs, fs))
        if fs == fixture_sets[0]:
            ax.legend(frameon=False, title="Ablated Ψ component")
    fig.suptitle("Benchmark D — Ψ component ablation (mean ± 95% CI)", y=1.02, fontweight="bold")
    fig.tight_layout()
    _finalize(fig)
    return _save(fig, "fig_ablation.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 6 — Benchmark E: conflict rate + deciding-factor attribution
# ─────────────────────────────────────────────────────────────────────────

def _synthetic_driver_counter(data: Dict[str, Any]) -> Counter:
    drivers: Counter = Counter()
    for r in data.get("benchmark_e", []):
        for e in r.get("conflict_log", []):
            drivers[e.get("driver", "unknown")] += 1
    return drivers


def figure_conflict_attribution(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_e", [])
    if not rows:
        raise FigureSkip("no data")

    apply_style()
    conflict_rates = [r["conflict_rate"] for r in rows]
    win_rates = [r["verification_win_rate"] for r in rows if r["conflicts_detected"] > 0]
    consistent = sum(r["final_consistent"] for r in rows)

    drivers: Counter = Counter()
    psi_deltas: List[float] = []
    for r in rows:
        for e in r.get("conflict_log", []):
            drivers[e["driver"]] += 1
            if e.get("psi_winner") is not None and e.get("psi_loser") is not None:
                psi_deltas.append(e["psi_winner"] - e["psi_loser"])

    fig, axes = plt.subplots(2, 2, figsize=(11, 7))

    ax = axes[0, 0]
    ax.hist(conflict_rates, bins=8, color=C["blue"], edgecolor="white", alpha=0.9)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Conflict rate per trial")
    ax.set_ylabel("Trials")
    ax.set_title("(a) Conflict rate distribution")
    if conflict_rates:
        ax.axvline(statistics.mean(conflict_rates), color=C["verm"], ls="--", lw=1.5,
                   label=f"mean {statistics.mean(conflict_rates):.2f}")
        ax.legend(frameon=False)

    ax = axes[0, 1]
    if win_rates:
        ax.hist(win_rates, bins=8, color=C["green"], edgecolor="white", alpha=0.9)
        ax.set_xlim(0, 1)
        ax.set_xlabel("Verification-agent win rate per trial")
        ax.set_ylabel("Trials")
        ax.set_title("(b) Higher-authority win rate")
        ax.axvline(statistics.mean(win_rates), color=C["blue"], ls="--", lw=1.5,
                   label=f"mean {statistics.mean(win_rates):.2f}")
        ax.legend(frameon=False)

    ax = axes[1, 0]
    order = ["trust", "confidence", "authority", "recency"]
    order = [d for d in order if drivers.get(d)]
    if order:
        vals = [drivers[d] for d in order]
        bars = ax.bar([d.title() for d in order], vals, color=[
            C["purple"], C["blue"], C["green"], C["orange"]][:len(order)])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1, str(v), ha="center", fontsize=9)
        ax.set_ylabel("Conflicts")
        ax.set_title("(c) Ψ deciding factor")
    else:
        ax.axis("off")

    ax = axes[1, 1]
    if psi_deltas:
        ax.hist(psi_deltas, bins=10, color=C["purple"], edgecolor="white", alpha=0.9)
        ax.set_xlabel("Ψ winner − Ψ loser")
        ax.set_ylabel("Conflicts")
        ax.set_title("(d) Ψ margin")
    else:
        ax.axis("off")

    fig.suptitle("Benchmark E — Forced overlapping writes: LCM conflict behaviour",
                 y=1.0, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_conflict_attribution.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 7 — Deciding-factor attribution (bar + pie)
# ─────────────────────────────────────────────────────────────────────────

def figure_deciding_factor(data: Dict[str, Any]) -> Path:
    """Conflict share by Ψ deciding factor.

    Synthetic panel is always computed from the Benchmark E conflict_log.
    A real-Ollama split is added only when ``--include-real-agent`` supplied
    validated artifacts with conflict entries — never fabricated.
    """
    drivers = _synthetic_driver_counter(data)
    if not drivers:
        raise FigureSkip("no conflict_log data")

    real_drivers: Counter = Counter()
    real_conflicts = _real_e_conflicts(data)
    for c in real_conflicts:
        real_drivers[c.get("driver", "unknown")] += 1

    apply_style()
    order = ["trust", "confidence", "authority", "recency"]
    order = [d for d in order if drivers.get(d) or real_drivers.get(d)]
    colors = {"trust": C["purple"], "confidence": C["blue"],
              "authority": C["green"], "recency": C["orange"]}

    if real_drivers:
        fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))
        panel_titles = ["(a) Synthetic (Benchmark E)",
                        "(b) Real-Ollama (validated)", "(c) Share"]
    else:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
        panel_titles = ["(a) Synthetic (Benchmark E)", "(b) Share"]

    def bar_panel(ax, counts: Counter, title: str) -> None:
        vals = [counts.get(d, 0) for d in order]
        bars = ax.bar([d.title() for d in order], vals,
                      color=[colors[d] for d in order])
        for b, v in zip(bars, vals):
            if v:
                ax.text(b.get_x() + b.get_width() / 2, v + 1, str(v),
                        ha="center", fontsize=9)
        ax.set_ylabel("Conflicts")
        ax.set_title(title)

    def pie_panel(ax, counts: Counter, title: str) -> None:
        vals = [counts.get(d, 0) for d in order]
        if sum(vals):
            ax.pie(vals, labels=[d.title() for d in order],
                   colors=[colors[d] for d in order], autopct="%1.1f%%",
                   startangle=90, textprops={"fontsize": 9})
            ax.set_title(title)

    bar_panel(axes[0], drivers, panel_titles[0])
    if real_drivers:
        bar_panel(axes[1], real_drivers, panel_titles[1])
        pie_panel(axes[2], real_drivers, "Conflict share (real)")
        title = ("Ψ deciding factor — synthetic vs real-Ollama "
                 "(real data validated; requires --include-real-agent)")
    else:
        pie_panel(axes[1], drivers, "Conflict share")
        title = "Ψ deciding factor — synthetic conflicts (Benchmark E)"
    fig.suptitle(title, y=1.0, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_deciding_factor.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 8 — Ψ conflict explainer (single real conflict)
# ─────────────────────────────────────────────────────────────────────────

def figure_psi_explainer(data: Dict[str, Any]) -> Path:
    rows = data.get("benchmark_e", [])
    conflict = None
    for r in rows:
        for e in r.get("conflict_log", []):
            if e.get("winner_breakdown") and e.get("loser_breakdown") and not e.get("unresolved"):
                conflict = e
                break
        if conflict:
            break
    if not conflict:
        raise FigureSkip("no real resolved conflict in data")

    apply_style()
    wb = conflict["winner_breakdown"]
    lb = conflict["loser_breakdown"]
    comps = ["R", "C", "T", "A"]
    comp_names = {"R": "Recency", "C": "Confidence", "T": "Trust", "A": "Authority"}
    weights = {"R": wb.get("w_r", 0.25), "C": wb.get("w_c", 0.25),
               "T": wb.get("w_t", 0.25), "A": wb.get("w_p", 0.25)}
    w_contrib = {c: weights[c] * wb.get(c, 0.0) for c in comps}
    l_contrib = {c: weights[c] * lb.get(c, 0.0) for c in comps}
    driver = conflict.get("driver", "?")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.6, 1]})

    y = np.arange(len(comps))
    h = 0.36
    ax1.barh(y + h / 2, [w_contrib[c] for c in comps], height=h,
             color=C["green"], label=f"Winner ({conflict['winner']})")
    ax1.barh(y - h / 2, [l_contrib[c] for c in comps], height=h,
             color=C["verm"], label=f"Loser ({conflict['loser']})")
    ax1.set_yticks(y)
    ax1.set_yticklabels([comp_names[c] for c in comps])
    ax1.set_xlabel(f"Weighted contribution  (Ψ = Σ wᵢ·component)")
    ax1.legend(frameon=False, loc="lower right", fontsize=9)
    ax1.set_title("(a) Ψ component decomposition")

    psi_w = conflict.get("psi_winner") or sum(w_contrib.values())
    psi_l = conflict.get("psi_loser") or sum(l_contrib.values())
    ax2.axis("off")
    ax2.text(0.5, 0.86, "Ψ conflict", ha="center", fontsize=14, weight="bold")
    ax2.text(0.5, 0.72, f"{conflict['winner']}  wins", ha="center", fontsize=13,
             color=C["green"], weight="bold")
    ax2.text(0.5, 0.60, f"Ψ winner = {psi_w:.4f}\nΨ loser  = {psi_l:.4f}",
             ha="center", fontsize=12, va="center",
             bbox=dict(boxstyle="round", fc="#F3F3F3", ec="#BBBBBB"))
    ax2.text(0.5, 0.38, f"margin ΔΨ = {abs(psi_w - psi_l):.4f}", ha="center",
             fontsize=12)
    ax2.text(0.5, 0.26, f"deciding factor:  {driver}", ha="center", fontsize=12,
             color=C["blue"], weight="bold")
    ax2.text(0.5, 0.12, f"weights:  w_r={weights['R']:.2f}  w_c={weights['C']:.2f}  "
                        f"w_t={weights['T']:.2f}  w_a={weights['A']:.2f}",
             ha="center", fontsize=9, color=C["grey"])
    ax2.text(0.5, 0.04, f"path: {conflict['path']}", ha="center", fontsize=9,
             color=C["grey"])

    fig.suptitle("Ψ explainer — how the winner was chosen", y=1.0,
                 fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_psi_explainer.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 9 — Multi-agent experiment (validated real Ollama only)
# ─────────────────────────────────────────────────────────────────────────

def _flatten_experiments(experiments: List[Dict[str, Any]]) -> Dict[str, Any]:
    writes: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    trust: Dict[str, float] = {}
    latency: Dict[str, List[float]] = defaultdict(list)
    final_state: Dict[str, Any] = {}

    for d in experiments:
        for w in d.get("write_log", []) or d.get("writes", []):
            if isinstance(w, dict):
                writes.append(w)
                if w.get("latency_ms") is not None:
                    latency[w.get("agent_id", "?")].append(float(w["latency_ms"]))
        for c in d.get("conflict_log", []) or d.get("conflicts", []):
            if isinstance(c, dict):
                conflicts.append(c)
        ts = d.get("trust_scores", {})
        for agent, v in ts.items():
            if isinstance(v, dict):
                trust[agent] = v.get("trust_score", v.get("trust", 0.5))
            else:
                trust[agent] = float(v or 0.5)
        fs = d.get("final_state", d.get("final_memory", {}))
        if isinstance(fs, dict):
            for path, meta in fs.items():
                if isinstance(meta, dict):
                    final_state[path] = meta.get("agent_id", "?")
                else:
                    final_state[path] = "?"

    return {"writes": writes, "conflicts": conflicts, "trust": trust,
            "latency": dict(latency), "final_state": final_state}


def figure_multi_agent_experiment(data: Dict[str, Any]) -> Path:
    exps = _validated_experiments(data)
    if not exps:
        raise FigureSkip("no validated real-Ollama artifacts "
                         "(pass --include-real-agent with Ollama + LCM running)")
    exp = _flatten_experiments(exps)
    if not exp["writes"]:
        raise FigureSkip("no experiment data")

    apply_style()
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))

    ax = axes[0, 0]
    agents = list(exp["trust"].keys())
    if agents:
        scores = [exp["trust"][a] for a in agents]
        colors = [C["green"] if s >= 0.7 else C["orange"] if s >= 0.3 else C["verm"] for s in scores]
        bars = ax.bar(agents, scores, color=colors)
        ax.axhline(0.5, color="grey", ls=":", lw=1)
        for b, s in zip(bars, scores):
            ax.text(b.get_x() + b.get_width() / 2, s + 0.02, f"{s:.2f}",
                    ha="center", fontsize=8)
        ax.set_ylim(0, 1.15)
        ax.set_ylabel("Trust score")
        ax.set_title("(a) Trust per agent")
        ax.tick_params(axis="x", rotation=20)
    else:
        ax.axis("off")

    ax = axes[0, 1]
    statuses = Counter(w.get("status", "?") for w in exp["writes"])
    s_order = sorted(statuses, key=lambda s: -statuses[s])
    colors = [C["green"] if "committed" in s else C["verm"] if "reject" in s
              else C["blue"] for s in s_order]
    bars = ax.bar(s_order, [statuses[s] for s in s_order], color=colors)
    for b, s in zip(bars, s_order):
        ax.text(b.get_x() + b.get_width() / 2, statuses[s] + 0.3, str(statuses[s]),
                ha="center", fontsize=9)
    ax.set_ylabel("Writes")
    ax.set_title("(b) Write status")
    ax.tick_params(axis="x", rotation=20)

    ax = axes[1, 0]
    lagents = list(exp["latency"].keys())
    if lagents:
        means = [statistics.mean(exp["latency"][a]) for a in lagents]
        cis = [_ci95(exp["latency"][a]) for a in lagents]
        ax.bar(lagents, means, yerr=cis, capsize=4, color=C["sky"])
        ax.set_ylabel("Mean latency (ms)")
        ax.set_title("(c) Write latency per agent")
        ax.tick_params(axis="x", rotation=20)
    else:
        ax.axis("off")

    ax = axes[1, 1]
    if exp["final_state"]:
        owners = Counter(exp["final_state"].values())
        o_order = sorted(owners, key=lambda a: -owners[a])
        bars = ax.bar(o_order, [owners[a] for a in o_order], color=C["purple"])
        for b, a in zip(bars, o_order):
            ax.text(b.get_x() + b.get_width() / 2, owners[a] + 0.2, str(owners[a]),
                    ha="center", fontsize=9)
        ax.set_ylabel("Memory paths owned")
        ax.set_title("(d) Final memory ownership")
        ax.tick_params(axis="x", rotation=20)
    else:
        ax.axis("off")

    fig.suptitle("Multi-agent experiment — real Ollama agents on LCM "
                 "(validated real-LLM artifacts)", y=1.0, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_multi_agent_experiment.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 10 — Sensitivity sweeps (model behaviour / synthetic)
# ─────────────────────────────────────────────────────────────────────────

def _make_umf(agent: str, conf: float, auth: float, source: str,
              t: Any) -> Any:
    from lcm_core.schema import ProvenanceInfo, StampedUMF
    import uuid
    return StampedUMF(
        agent_id=agent, session_id="sweep",
        timestamp=t, confidence_score=conf,
        assertion_payload={f"k.{agent}": 1},
        provenance_id=str(uuid.uuid4()),
        ingested_at=t,
        provenance_info=ProvenanceInfo(
            source_type=source, authority_score=auth,
            verified_confidence=conf, domain="_global",
        ),
    )


def figure_sensitivity_sweeps(data: Dict[str, Any]) -> Path:
    from datetime import datetime, timedelta
    from lcm_core.conflict import ConflictResolutionEngine

    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    now = datetime.utcnow()

    ax = axes[0]
    rng = random.Random(42)
    now = datetime.utcnow()
    scenarios = []
    for _ in range(300):
        conf0 = rng.uniform(0.4, 0.95)
        auth0 = rng.choice([0.3, 0.75, 0.85, 0.9])
        trust0 = rng.uniform(0.35, 0.95)
        base_t = now - timedelta(hours=rng.uniform(0.0, 12.0))
        t0 = base_t - timedelta(minutes=rng.uniform(0.0, 240.0))
        t1 = base_t - timedelta(minutes=rng.uniform(0.0, 240.0))
        conf1 = min(0.99, max(0.1, conf0 + rng.gauss(0.0, 0.08)))
        trust1 = min(0.99, max(0.1, trust0 + rng.gauss(0.0, 0.08)))
        auth1 = min(0.99, max(0.1, auth0 + rng.gauss(0.0, 0.08)))
        src0 = "agent_claim" if auth0 < 0.5 else "database"
        src1 = "agent_claim" if auth1 < 0.5 else "database"
        a = _make_umf("A", conf0, auth0, src0, t0)
        b = _make_umf("B", conf1, auth1, src1, t1)
        scenarios.append((a, b, {"A": trust0, "B": trust1}))

    thresholds = np.linspace(0.0, 0.25, 26)
    unresolved = []
    for th in thresholds:
        eng = ConflictResolutionEngine(uncertainty_threshold=float(th))
        n_unres = sum(
            1 for a, b, tt in scenarios
            if eng.resolve_conflict(a, b, tt, reference_time=now).unresolved
        )
        unresolved.append(n_unres / len(scenarios))
    ax.plot(thresholds, unresolved, "o-", color=C["verm"], lw=2)
    ax.axvline(0.05, color="grey", ls=":", lw=1)
    ax.text(0.052, 0.85, "default 0.05", fontsize=8, color=C["grey"])
    ax.set_xlabel("Uncertainty threshold  |ΨA−ΨB|")
    ax.set_ylabel("Unresolved (near-tie) rate")
    ax.set_ylim(-0.05, 1.15)
    ax.set_title("(a) Uncertainty threshold — over 300 varied near-tie scenarios")

    ax = axes[1]
    for half_h, color in [(6, C["orange"]), (24, C["green"]), (72, C["blue"]), (168, C["purple"])]:
        lam = 0.6931471805599453 / (half_h * 3600.0)
        hours = np.linspace(0, 72, 100)
        recency = np.exp(-lam * hours * 3600.0)
        ax.plot(hours, recency, lw=2, color=color, label=f"{half_h} h")
    ax.axhline(0.5, color="grey", ls=":", lw=1)
    ax.set_xlabel("Memory age (hours)")
    ax.set_ylabel("Recency factor  e^(−λ·Δt)")
    ax.set_ylim(0, 1.05)
    ax.legend(frameon=False, title="Half-life")
    ax.set_title("(b) Recency decay by half-life")

    ax = axes[2]
    grid = np.linspace(0.05, 0.95, 40)
    X, Y = np.meshgrid(grid, grid)
    W = np.zeros_like(X)
    for i in range(len(grid)):
        for j in range(len(grid)):
            w_p, w_c = grid[i], grid[j]
            rem = 1.0 - w_p - w_c
            if rem < 0:
                W[j, i] = np.nan
                continue
            w_r = w_t = rem / 2.0
            eng = ConflictResolutionEngine(psi_weights={
                "recency": w_r, "confidence": w_c,
                "trust": w_t, "provenance": w_p,
            }, uncertainty_threshold=0.0)
            a_umf = _make_umf("A", 0.95, 0.3, "agent_claim", now - timedelta(seconds=5))
            b_umf = _make_umf("B", 0.6, 0.9, "database", now)
            res = eng.resolve_conflict(a_umf, b_umf, {"A": 0.5, "B": 0.5},
                                       reference_time=now)
            W[j, i] = 1.0 if (res.winner and res.winner.agent_id == "B") else 0.0
    im = ax.imshow(W, origin="lower", aspect="auto", cmap="RdYlGn",
                   extent=[0.05, 0.95, 0.05, 0.95], vmin=0, vmax=1)
    ax.set_xlabel("Ψ weight — authority  w_p")
    ax.set_ylabel("Ψ weight — confidence  w_c")
    ax.set_title("(c) Winner boundary (B = authority-backed)")
    cb = fig.colorbar(im, ax=ax, fraction=0.046)
    cb.set_ticks([0, 1])
    cb.set_ticklabels(["A wins\n(conf. 0.95)", "B wins\n(auth. 0.9)"])

    fig.suptitle("LCM sensitivity to tunable parameters (model behaviour)", y=1.02,
                 fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_sensitivity_sweeps.png")


# ─────────────────────────────────────────────────────────────────────────
# Figure 11 — Results comparison (Benchmark C + Benchmark E)
# ─────────────────────────────────────────────────────────────────────────

def figure_results_comparison(data: Dict[str, Any]) -> Path:
    """LCM vs baselines on C, and synthetic-vs-real conflict behaviour on E.

    The real-Ollama panel is plotted ONLY from validated artifacts that
    actually produced conflicts (see :func:`_real_e_aggregate`).  If the
    validated data has zero conflicts, the panel is omitted and annotated —
    never fabricated.
    """
    c_rows = data.get("benchmark_c", [])
    e_rows = data.get("benchmark_e", [])
    if not c_rows and not e_rows:
        raise FigureSkip("no data")

    real_e = _real_e_aggregate(data)

    apply_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.6))

    # (a) Benchmark C accuracy by strategy
    strategies = ["LCM", "LCM_cold_start", "last_write_wins", "recency_only",
                  "majority_voting", "fixed_trust"]
    strategies = [s for s in strategies if any(r["strategy"] == s for r in c_rows)]
    display = {
        "LCM": "LCM",
        "LCM_cold_start": "LCM cold-start",
        "last_write_wins": "Last write wins",
        "recency_only": "Recency only",
        "majority_voting": "Majority voting",
        "fixed_trust": "Fixed trust",
    }
    if strategies:
        accs, cis = [], []
        for s in strategies:
            correct = [r["correct"] for r in c_rows if r["strategy"] == s]
            accs.append(statistics.mean(correct))
            cis.append(_ci95(correct))
        x = np.arange(len(strategies))
        bars = ax1.bar(x, accs, yerr=cis, width=0.6, capsize=4,
                       color=[STRATEGY_COLORS.get(s, C["grey"]) for s in strategies])
        for b, v in zip(bars, accs):
            ax1.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.0%}",
                     ha="center", fontsize=8)
        ax1.set_xticks(x)
        ax1.set_xticklabels([display.get(s, s) for s in strategies], fontsize=8)
        ax1.set_ylabel("Accuracy (correct winner)")
        ax1.set_ylim(0, 1.15)
    ax1.axhline(0.5, color="grey", ls=":", lw=1)
    ax1.set_title("(a) Benchmark C — accuracy by strategy (mean ± 95% CI)")

    # (b) Benchmark E conflict behaviour (synthetic, and real when validated)
    def synth_e_metrics(rows):
        total_writes = sum(r["total_writes"] for r in rows)
        total_conf = sum(r["conflicts_detected"] for r in rows)
        total_wins = sum(r["verification_wins"] for r in rows)
        consistent = sum(r["final_consistent"] for r in rows)
        return {
            "conflict_rate": total_conf / total_writes if total_writes else 0.0,
            "correct_winner": total_wins / total_conf if total_conf else 0.0,
            "consistency": consistent / len(rows) if rows else 0.0,
        }

    synth = synth_e_metrics(e_rows) if e_rows else None
    groups = []
    if synth is not None:
        groups.append(("synthetic", synth))
    if real_e is not None:
        groups.append(("real-Ollama (validated)", real_e))

    metric_labels = ["conflict rate", "correct winner", "memory consistent"]
    metric_keys = ["conflict_rate", "correct_winner", "consistency"]
    if groups:
        n_groups = len(groups)
        x = np.arange(3)
        width = 0.34
        for i, (label, metrics) in enumerate(groups):
            offset = (i - (n_groups - 1) / 2) * width
            color = C["green"] if "real" in label else C["blue"]
            vals = [metrics[k] for k in metric_keys]
            bars = ax2.bar(x + offset, vals, width, color=color, label=label)
            for b, v in zip(bars, vals):
                ax2.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.0%}",
                         ha="center", fontsize=8)
        ax2.set_xticks(x)
        ax2.set_xticklabels(metric_labels)
        ax2.set_ylim(0, 1.15)
        ax2.legend(frameon=False, fontsize=9)
    else:
        ax2.text(0.5, 0.5, "no Benchmark E data", ha="center", va="center",
                 fontsize=11, color=C["grey"])
        ax2.axis("off")

    if synth is not None and real_e is None:
        ax2.set_title("(b) Benchmark E — synthetic conflict behaviour\n"
                      "(real-Ollama panel omitted: no validated conflict artifacts)")
    elif real_e is not None:
        ax2.set_title("(b) Benchmark E — synthetic vs real-Ollama (validated)")

    fig.suptitle("Results comparison — all data from figures_data.json cache",
                 y=1.02, fontsize=13, weight="bold")
    _finalize(fig)
    return _save(fig, "fig_results_comparison.png")


# ─────────────────────────────────────────────────────────────────────────
# Manifest
# ─────────────────────────────────────────────────────────────────────────

def _classify(data: Dict[str, Any], name: str) -> str:
    """Resolve the data classification for the manifest (honest labeling)."""
    base = FIGURE_CLASSIFICATION.get(name, "synthetic")
    if base == "real-agent":
        return "real-agent" if _validated_experiments(data) else "skipped"
    if name == "fig_deciding_factor":
        return "real-agent" if _real_e_conflicts(data) else "synthetic"
    if name == "fig_results_comparison":
        return "real-agent" if _real_e_aggregate(data) else "synthetic"
    return base


def write_manifest(data: Dict[str, Any], figure_outcomes: List[Tuple[str, Path, str]]) -> Path:
    """Write results/figures/manifest.json describing every figure decision."""
    cache_hash = _sha256_text(json.dumps(data, sort_keys=True, default=str))
    sources = data.get("sources", {})
    entries = []
    for name, path, reason in figure_outcomes:
        status = "generated" if path.name else "skipped"
        used_sections = FIGURE_SOURCES.get(name, [])
        artifact_hashes = {}
        for sec in used_sections:
            src = sources.get(sec)
            if isinstance(src, list):
                artifact_hashes[sec] = [
                    s.get("sha256") for s in src if isinstance(s, dict)
                ]
            elif isinstance(src, dict):
                artifact_hashes[sec] = src.get("sha256")
        entries.append({
            "figure": name,
            "filename": path.name if path.name else None,
            "status": status,
            "skip_reason": reason if not path.name else None,
            "timestamp": datetime.now().isoformat(),
            "source_cache_sha256": cache_hash,
            "git_commit": data.get("git_commit"),
            "cache_generated_at": data.get("generated_at"),
            "artifact_hashes": artifact_hashes,
            "data_classification": _classify(data, name),
        })

    manifest = {
        "manifest_version": 1,
        "generated_at": datetime.now().isoformat(),
        "cache": {
            "path": str(CACHE.relative_to(ROOT) if CACHE.is_relative_to(ROOT) else CACHE),
            "sha256": cache_hash,
            "schema_version": data.get("schema_version"),
            "git_commit": data.get("git_commit"),
        },
        "figures": entries,
    }
    out = FIG_DIR / "manifest.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"  [OK] manifest.json ({len(entries)} figures)")
    return out


# ─────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────

ALL_FIGURES = [
    ("fig_architecture", figure_architecture),
    ("fig_race_condition", figure_race_condition),
    ("fig_mandela_trapping", figure_mandela_trapping),
    ("fig_trust_gap_sweep", figure_trust_gap_sweep),
    ("fig_benchmark_c_accuracy", figure_benchmark_c),
    ("fig_ablation", figure_ablation),
    ("fig_conflict_attribution", figure_conflict_attribution),
    ("fig_deciding_factor", figure_deciding_factor),
    ("fig_psi_explainer", figure_psi_explainer),
    ("fig_multi_agent_experiment", figure_multi_agent_experiment),
    ("fig_sensitivity_sweeps", figure_sensitivity_sweeps),
    ("fig_results_comparison", figure_results_comparison),
]

ALL_FIGURE_NAMES = [name for name, _ in ALL_FIGURES]


def _parse_cli(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate publication figures from the canonical data cache")
    parser.add_argument("--force", action="store_true", dest="force",
                        help="Re-run benchmarks and rebuild the cache even if it exists")
    parser.add_argument("--no-cache", action="store_true", dest="force",
                        help="Alias for --force")
    parser.add_argument("--cache-only", action="store_true", dest="cache_only",
                        help="Only plot from the existing cache; fail if it is missing")
    parser.add_argument("--plot-only", action="store_true", dest="cache_only",
                        help="Alias for --cache-only")
    parser.add_argument("--include-real-agent", action="store_true",
                        dest="include_real_agent",
                        help="Include VALIDATED real-Ollama experiment artifacts")
    parser.add_argument("--figures", nargs="+", dest="only", default=None,
                        metavar="NAME",
                        help=f"Only these figures: {', '.join(ALL_FIGURE_NAMES)}")
    parser.add_argument("--only", nargs="+", dest="only", default=None,
                        help="Alias for --figures")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = _parse_cli(argv)

    if args.only:
        unknown = [n for n in args.only if n not in ALL_FIGURE_NAMES]
        if unknown:
            raise SystemExit(f"Unknown figure(s): {', '.join(unknown)}. "
                             f"Valid: {', '.join(ALL_FIGURE_NAMES)}")

    if args.cache_only:
        if not CACHE.exists():
            raise SystemExit(
                f"Cache {CACHE} not found. Run without --cache-only to build it first.")
        data = load_data(use_cache=True, force=False,
                         include_real_agent=args.include_real_agent,
                         strict=True)
    elif args.force:
        data = load_data(use_cache=False, force=True,
                         include_real_agent=args.include_real_agent)
    else:
        data = load_data(use_cache=True, force=False,
                         include_real_agent=args.include_real_agent)

    print(f"\nGenerating figures into {FIG_DIR}/")
    outcomes: List[Tuple[str, Path, str]] = []
    n_ok = 0
    for name, fn in ALL_FIGURES:
        if args.only and name not in args.only:
            continue
        try:
            path = fn(data)
            if path and path.name:
                outcomes.append((name, path, ""))
                n_ok += 1
            else:
                print(f"  [skip] {name}: no output")
                outcomes.append((name, Path(), "no output"))
        except FigureSkip as exc:
            print(f"  [skip] {name}: {exc}")
            outcomes.append((name, Path(), exc.reason))
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERROR] {name}: {exc}")
            outcomes.append((name, Path(), f"error: {exc}"))

    write_manifest(data, outcomes)
    skipped = [name for name, p, _ in outcomes if not p.name]
    print(f"\nDone: {n_ok} figure(s) written to {FIG_DIR}/ "
          f"({len(skipped)} skipped). Manifest: {FIG_DIR / 'manifest.json'}")
    if skipped:
        print("  Skipped:", ", ".join(skipped))


if __name__ == "__main__":
    main()
