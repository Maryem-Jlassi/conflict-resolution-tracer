"""
Benchmark C — Conflict Resolution Accuracy & Cold-Start Evaluation

Metrics (per your mentor's specification):
  - conflict_resolution_accuracy   : correct winner chosen / total conflicts
  - incorrect_overwrite_rate       : correct memory wrongly replaced / total conflicts
  - unresolved_conflict_rate       : conflicts left unresolved / total conflicts
  - cold_start_accuracy            : accuracy when agents have NO prior history
  - post_history_accuracy          : accuracy after ≥N verified outcomes are recorded

Scenario tiers:
  high_trust_vs_low_trust  — clear winner by trust + evidence (control)
  recency_dominated        — clear winner by recency (control)
  cold_start               — clear winner by evidence type (control)
  graded_ambiguous         — small margins; factors point in different directions.
                             No single dominant signal. Tests that LCM correctly
                             defers (marks unresolved) rather than forcing a wrong
                             decision when the correct answer is genuinely unclear.

Compared against all four baselines:
  last_write_wins | recency_only | majority_voting | fixed_trust
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine, ResolutionConfig
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.schema import StampedUMF, ProvenanceInfo
from lcm_core.crypto import sign_evidence_message, benchmark_dev_evidence_key
from benchmarks.baselines_extended import (
    LastWriteWins, RecencyOnly, MajorityVoting, FixedTrust, ALL_BASELINES
)


# ---------------------------------------------------------------------------
# Minimal in-memory storage for benchmarks (no SQLite dependency)
# ---------------------------------------------------------------------------

class _DictStorage:
    def __init__(self):
        self._live: Dict[str, StampedUMF] = {}

    def get_existing(self, path: str) -> Optional[StampedUMF]:
        return self._live.get(path)

    def commit(self, umf: StampedUMF, path: str) -> None:
        self._live[path] = umf

    def commit_pending(self, umf: StampedUMF, path: str) -> None:
        pass  # not needed for accuracy benchmarks

    def archive(self, provenance_id: str) -> None:
        pass  # not needed for accuracy benchmarks

    def update_provenance_fields(self, provenance_id: str, **kwargs) -> None:
        pass  # no-op for benchmark storage


# ---------------------------------------------------------------------------
# Scenario builder helpers
# ---------------------------------------------------------------------------

def _make_umf(agent_id: str, path: str, value: Any,
              confidence: float, delta_seconds: float = 0,
              verified_confidence: Optional[float] = None,
              domain: Optional[str] = None) -> StampedUMF:
    ts = datetime(2026, 7, 14, 10, 0, 0) - timedelta(seconds=delta_seconds)
    vc = verified_confidence if verified_confidence is not None else confidence
    # Derive a reasonable authority score from verified_confidence
    # (high evidence quality → high authority; agent_claim floor = 0.3)
    authority = max(0.3, min(1.0, vc))
    prov = ProvenanceInfo(verified_confidence=vc, authority_score=authority, domain=domain)
    return StampedUMF(
        agent_id=agent_id,
        session_id="bench",
        timestamp=ts,
        confidence_score=confidence,
        assertion_payload={path: value},
        provenance_id=f"prov_{agent_id}_{delta_seconds}",
        ingested_at=ts,
        provenance_info=prov,
    )


def _raw(agent_id: str, path: str, value: Any,
         confidence: float = 0.7, delta_seconds: float = 0) -> Dict[str, Any]:
    ts = datetime(2026, 7, 14, 10, 0, 0) - timedelta(seconds=delta_seconds)
    return {
        "agent_id": agent_id,
        "session_id": "bench",
        "timestamp": ts,
        "confidence_score": confidence,
        "assertion_payload": {path: value},
    }


def _raw_from_umf(umf: StampedUMF) -> Dict[str, Any]:
    """Reconstruct the raw agent packet from a StampedUMF (agent-owned fields only)."""
    raw = {
        "agent_id": umf.agent_id,
        "session_id": umf.session_id,
        "timestamp": umf.timestamp,
        "confidence_score": umf.confidence_score,
        "assertion_payload": dict(umf.assertion_payload),
    }
    if umf.media_uri is not None:
        raw["media_uri"] = umf.media_uri
    if umf.media_hash is not None:
        raw["media_hash"] = umf.media_hash
    return raw


# ---------------------------------------------------------------------------
# Single trial runner
# ---------------------------------------------------------------------------

@dataclass
class TrialScenario:
    """Describes one conflict scenario with a known ground truth."""
    path: str
    existing: StampedUMF            # currently committed memory
    incoming: StampedUMF            # new claim
    ground_truth_agent: str         # which agent holds the correct value
    evidence_existing: List[EvidenceRecord] = field(default_factory=list)
    evidence_incoming: List[EvidenceRecord] = field(default_factory=list)
    evidence_signature_existing: Optional[str] = None
    evidence_signature_incoming: Optional[str] = None
    domain: Optional[str] = None


@dataclass
class TrialResult:
    """Outcome of one trial for one strategy."""
    strategy: str
    correct: bool               # did the right agent win?
    incorrect_overwrite: bool   # was a correct memory replaced by wrong one?
    unresolved: bool            # was the conflict left unresolved?
    design_tradeoff_recency: bool = False  # was this a recency design tradeoff?


async def _run_lcm_trial(scenario: TrialScenario,
                          trust: TrustManager,
                          uncertainty_threshold: float = 0.0) -> TrialResult:
    """Run one scenario through the full LCM pipeline."""
    storage = _DictStorage()

    pipeline = WritePipeline(
        storage=storage,
        trust_manager=trust,
        conflict_engine=ConflictResolutionEngine(
            uncertainty_threshold=uncertainty_threshold
        ),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(),
    )

    # Time-invariant Ψ reference: newest claim of THIS scenario plus a fixed
    # offset, so the benchmark does not drift with the wall clock. Mirrors
    # benchmark_d_ablation.BENCHMARK_REFERENCE_DELTA.
    reference_time = (
        max(scenario.existing.timestamp, scenario.incoming.timestamp)
        + timedelta(hours=12)
    )

    # Write the existing (incumbent) claim through the pipeline with its own
    # evidence so the middleware derives authority from evidence records —
    # NOT a hand-stamped authority_score on a pre-built StampedUMF.  A DB-backed
    # agent therefore keeps database-level authority instead of collapsing to
    # the agent_claim default (which happens when no evidence/signature is given).
    # When the incumbent carries no external evidence we commit the pre-stamped
    # UMF directly (its authority was already derived at build time).
    if scenario.evidence_existing:
        await pipeline.process(
            _raw_from_umf(scenario.existing),
            evidence_records=scenario.evidence_existing,
            evidence_signature=scenario.evidence_signature_existing,
            domain=scenario.domain,
        )
    else:
        storage.commit(scenario.existing, scenario.path)

    raw = {
        "agent_id": scenario.incoming.agent_id,
        "session_id": "bench",
        "timestamp": scenario.incoming.timestamp,
        "confidence_score": scenario.incoming.confidence_score,
        "assertion_payload": scenario.incoming.assertion_payload,
    }

    result = await pipeline.process(
        raw,
        evidence_records=scenario.evidence_incoming or None,
        evidence_signature=scenario.evidence_signature_incoming,
        domain=scenario.domain,
        reference_time=reference_time,
    )

    winner_agent = result.committed.agent_id if result.committed else scenario.existing.agent_id
    correct = winner_agent == scenario.ground_truth_agent

    # Incorrect overwrite = the correct memory was replaced with a wrong one
    incorrect_overwrite = (
        scenario.existing.agent_id == scenario.ground_truth_agent
        and winner_agent != scenario.ground_truth_agent
        and not result.status == "unresolved"
    )

    # Detect recency tradeoff: when oracle prefers pure recency and LCM loses
    design_tradeoff_recency = False
    if not correct and scenario.incoming.timestamp > scenario.existing.timestamp:
        # Check if this is a recency-dominated scenario where all other factors are equal
        # If oracle chose the newer agent but LCM didn't, it's a design tradeoff
        if (scenario.incoming.confidence_score == scenario.existing.confidence_score and
            scenario.domain is None):  # No domain advantage
            design_tradeoff_recency = True

    return TrialResult(
        strategy="LCM",
        correct=correct,
        incorrect_overwrite=incorrect_overwrite,
        unresolved=(result.status == "unresolved"),
        design_tradeoff_recency=design_tradeoff_recency,
    )


def _run_baseline_trial(scenario: TrialScenario, baseline) -> TrialResult:
    """Run one scenario through a baseline resolver."""
    winner = baseline.resolve(scenario.existing, scenario.incoming)
    correct = winner.agent_id == scenario.ground_truth_agent
    incorrect_overwrite = (
        scenario.existing.agent_id == scenario.ground_truth_agent
        and winner.agent_id != scenario.ground_truth_agent
    )
    return TrialResult(
        strategy=baseline.name,
        correct=correct,
        incorrect_overwrite=incorrect_overwrite,
        unresolved=False,
    )


# ---------------------------------------------------------------------------
# Scenario library
# ---------------------------------------------------------------------------

def build_high_trust_vs_low_trust_scenarios(n: int = 50) -> List[TrialScenario]:
    """
    High-trust agent holds the correct value; low-trust agent contradicts it.
    Ground truth = high-trust agent.
    """
    scenarios = []
    for i in range(n):
        path = f"benchmark.c.ht_lt.{i}"
        existing = _make_umf("trusted_agent", path, "correct_value",
                              confidence=0.85, delta_seconds=3600,
                              verified_confidence=0.85)
        incoming = _make_umf("untrusted_agent", path, "wrong_value",
                              confidence=0.8, delta_seconds=0,
                              verified_confidence=0.4)
        scenarios.append(TrialScenario(
            path=path,
            existing=existing,
            incoming=incoming,
            ground_truth_agent="trusted_agent",
            evidence_incoming=[EvidenceRecord(
                evidence_type=EvidenceType.AGENT_CLAIM, relevance_score=0.4
            )],
        ))
    return scenarios


def build_recency_dominated_scenarios(n: int = 50) -> List[TrialScenario]:
    """
    Both agents equally trusted and confident; newer information is the correct one.
    Ground truth = incoming (newer) agent.
    
    This scenario tests that LCM defers to recency when trust, confidence, and
    authority are truly equal. All factors except recency are identical to isolate
    the recency signal. LCM should prioritize the newer information when other
    decision factors are equal.
    """
    scenarios = []
    for i in range(n):
        path = f"benchmark.c.recency.{i}"
        # Both agents have identical trust, confidence, and authority
        # Only difference is timestamp (recency)
        existing = _make_umf("agent_a", path, "old_value",
                              confidence=0.85, delta_seconds=86400 * 30,
                              verified_confidence=0.85)
        incoming = _make_umf("agent_b", path, "new_value",
                              confidence=0.85, delta_seconds=0,
                              verified_confidence=0.85)
        scenarios.append(TrialScenario(
            path=path,
            existing=existing,
            incoming=incoming,
            ground_truth_agent="agent_b",  # Newer agent should win
            evidence_incoming=[EvidenceRecord(
                evidence_type=EvidenceType.DATABASE, relevance_score=0.85,
                source_id="db://recency",
            )],
            evidence_existing=[EvidenceRecord(
                evidence_type=EvidenceType.DATABASE, relevance_score=0.85,
                source_id="db://recency",
            )],
        evidence_signature_existing=sign_evidence_message(EvidenceType.DATABASE, "db://recency"),
        evidence_signature_incoming=sign_evidence_message(EvidenceType.DATABASE, "db://recency"),
        ))
    return scenarios


def build_cold_start_scenarios(n: int = 50) -> List[TrialScenario]:
    """
    Both agents new — no trust history. Evidence quality determines winner.
    Ground truth = the agent with stronger evidence (database-backed).

    The existing agent's UMF is pre-stamped with database authority (0.9)
    so the pipeline uses that stored value directly when comparing.
    The incoming agent's evidence is agent_claim (0.3) passed through the pipeline.
    """
    scenarios = []
    for i in range(n):
        path = f"benchmark.c.coldstart.{i}"
        # Pre-stamp the existing UMF with database-level authority so it survives
        # storage.commit() with the right provenance intact.
        from lcm_core.confidence_engine import ConfidenceEngine as _CE
        _ce = _CE()
        existing = StampedUMF(
            agent_id="new_agent_a",
            session_id="bench",
            timestamp=datetime(2026, 7, 14, 10, 0, 0) - timedelta(seconds=60),
            confidence_score=0.7,
            assertion_payload={path: "db_backed_value"},
            provenance_id=f"prov_cold_a_{i}",
            ingested_at=datetime(2026, 7, 14, 10, 0, 0) - timedelta(seconds=60),
            provenance_info=ProvenanceInfo(
                source_type="database",
                authority_score=0.9,          # database authority — explicit
                verified_confidence=_ce.score_from_source_type("database"),  # 0.9
            ),
        )
        incoming = _make_umf("new_agent_b", path, "llm_only_value",
                              confidence=0.9, delta_seconds=0,
                              verified_confidence=None)
        scenarios.append(TrialScenario(
            path=path,
            existing=existing,
            incoming=incoming,
            ground_truth_agent="new_agent_a",  # DB-backed should win
            evidence_existing=[EvidenceRecord(
                evidence_type=EvidenceType.DATABASE, relevance_score=1.0,
                source_id="db://patient",
            )],
            evidence_incoming=[EvidenceRecord(
                evidence_type=EvidenceType.AGENT_CLAIM, relevance_score=0.3
            )],
        evidence_signature_existing=sign_evidence_message(EvidenceType.DATABASE, "db://patient"),
        ))
    return scenarios


def build_graded_ambiguous_scenarios(n: int = 50) -> List[TrialScenario]:
    """
    Genuinely ambiguous conflicts — factors point in different directions
    with small margins. No single signal dominates.

    These are NOT constructed to guarantee a specific winner. Instead, they
    test that LCM either:
      (a) makes a marginally correct decision when Ψ is unambiguous enough, or
      (b) correctly marks the conflict as unresolved when |ΨΔ| < threshold.

    Both outcomes are valid per the supervisor's spec; forcing a wrong decision
    is the only failure mode.

    Sub-tiers (varied across n scenarios):
      trust_vs_recency  : older agent has higher trust; newer agent has lower trust
                          but marginally better evidence. Trust gap ≤ 0.2.
      confidence_vs_authority : one agent has higher self-reported confidence but
                                lower evidence authority; the other has the reverse.
      near_tie          : all factors within 0.05 of each other → expected unresolved.
    """
    import math
    scenarios = []
    for i in range(n):
        subtier = i % 3
        path = f"benchmark.c.ambiguous.{i}"

        if subtier == 0:
            # trust vs recency: older but slightly more trusted agent
            # trust gap = 0.15 (small), recency gap = 2h (meaningful)
            existing = _make_umf(
                "senior_agent", path, "established_value",
                confidence=0.72, delta_seconds=7200,
                verified_confidence=0.72,
            )
            incoming = _make_umf(
                "junior_agent", path, "updated_value",
                confidence=0.70, delta_seconds=0,
                verified_confidence=0.68,
            )
            # senior_agent has trust 0.65, junior 0.50 (set up by trust_ambiguous)
            # recency favours junior; trust favours senior — genuine conflict
            ground_truth = "senior_agent"   # trust + evidence edges out recency
            ev_existing = [EvidenceRecord(
                evidence_type=EvidenceType.TOOL_OUTPUT, relevance_score=0.8,
            )]
            ev_incoming = [EvidenceRecord(
                evidence_type=EvidenceType.DOCUMENT, relevance_score=0.75,
            )]

        elif subtier == 1:
            # confidence vs authority: high-confidence agent_claim vs lower-confidence DB
            existing = _make_umf(
                "db_agent", path, "database_value",
                confidence=0.65, delta_seconds=1800,
                verified_confidence=0.82,   # DB authority lifts this
            )
            incoming = _make_umf(
                "llm_agent", path, "llm_generated_value",
                confidence=0.92, delta_seconds=0,
                verified_confidence=0.35,   # agent_claim: confidence ignored
            )
            ground_truth = "db_agent"   # authority should beat raw confidence
            ev_existing = [EvidenceRecord(
                evidence_type=EvidenceType.DATABASE, relevance_score=0.9,
            )]
            ev_incoming = [EvidenceRecord(
                evidence_type=EvidenceType.AGENT_CLAIM, relevance_score=0.3,
            )]

        else:
            # near-tie: all factors within a tiny margin — expect unresolved
            existing = _make_umf(
                "agent_x", path, "value_x",
                confidence=0.75, delta_seconds=120,
                verified_confidence=0.74,
            )
            incoming = _make_umf(
                "agent_y", path, "value_y",
                confidence=0.76, delta_seconds=0,
                verified_confidence=0.75,
            )
            # Near-tie: ground truth is None — either winner or unresolved is acceptable.
            # We record ground_truth as incoming (the marginally newer one) but count
            # unresolved as a valid outcome (not an incorrect_overwrite).
            ground_truth = "agent_y"
            ev_existing = [EvidenceRecord(
                evidence_type=EvidenceType.TOOL_OUTPUT, relevance_score=0.74,
            )]
            ev_incoming = [EvidenceRecord(
                evidence_type=EvidenceType.TOOL_OUTPUT, relevance_score=0.75,
            )]

        scenarios.append(TrialScenario(
            path=path,
            existing=existing,
            incoming=incoming,
            ground_truth_agent=ground_truth,
            evidence_existing=ev_existing,
            evidence_incoming=ev_incoming,
        ))
    return scenarios


# ---------------------------------------------------------------------------
# Main evaluation runner
# ---------------------------------------------------------------------------

async def run_benchmark_c(trials_per_scenario: int = 50, on_trial=None) -> List[Dict[str, Any]]:
    """
    Run the full evaluation across all scenario types and strategies.

    Signed database evidence is verified inside ``_run_lcm_trial`` against the
    dev Ed25519 provider key, so the dev-key opt-in is scoped to this run only
    (never mutated at import time, never leaked to the caller's environment).
    """
    with benchmark_dev_evidence_key():
        return await _run_benchmark_c(trials_per_scenario, on_trial)


async def _run_benchmark_c(trials_per_scenario: int = 50, on_trial=None) -> List[Dict[str, Any]]:
    results = []

    scenario_builders = {
        "high_trust_vs_low_trust": build_high_trust_vs_low_trust_scenarios,
        "recency_dominated":       build_recency_dominated_scenarios,
        "cold_start":              build_cold_start_scenarios,
        "graded_ambiguous":        build_graded_ambiguous_scenarios,
    }

    for scenario_type, builder in scenario_builders.items():
        print(f"\n=== Benchmark C: {scenario_type} ({trials_per_scenario} trials) ===")

        # --- LCM with mature trust history (post-history) ---
        trust_mature = TrustManager()
        if scenario_type == "high_trust_vs_low_trust":
            for _ in range(20):
                trust_mature.record_outcome("trusted_agent",   correct=True)
                trust_mature.record_outcome("untrusted_agent", correct=False)
        elif scenario_type == "recency_dominated":
            for _ in range(5):
                trust_mature.record_outcome("agent_a", correct=True)
                trust_mature.record_outcome("agent_b", correct=True)
        elif scenario_type == "graded_ambiguous":
            # senior_agent: 13/20 correct → trust=0.65; junior_agent: 10/20 → 0.50
            for _ in range(13):
                trust_mature.record_outcome("senior_agent", correct=True)
            for _ in range(7):
                trust_mature.record_outcome("senior_agent", correct=False)
            for _ in range(10):
                trust_mature.record_outcome("junior_agent", correct=True)
            for _ in range(10):
                trust_mature.record_outcome("junior_agent", correct=False)
            # db_agent/llm_agent have no history → cold-start prior 0.5 (fair)

        # --- LCM cold-start: no history ---
        trust_cold = TrustManager()

        scenarios = builder(trials_per_scenario)

        for trial_idx, scenario in enumerate(scenarios):
            phase = "cold_start" if scenario_type == "cold_start" else "post_history"

            # LCM post-history
            lcm_result = await _run_lcm_trial(scenario, trust_mature)
            results.append({
                "strategy": "LCM",
                "scenario_type": scenario_type,
                "phase": "post_history",
                "trial": trial_idx,
                "correct": lcm_result.correct,
                "incorrect_overwrite": lcm_result.incorrect_overwrite,
                "unresolved": lcm_result.unresolved,
                "design_tradeoff_recency": lcm_result.design_tradeoff_recency,
            })

            # LCM cold-start
            lcm_cold_result = await _run_lcm_trial(scenario, trust_cold)
            results.append({
                "strategy": "LCM_cold_start",
                "scenario_type": scenario_type,
                "phase": "cold_start",
                "trial": trial_idx,
                "correct": lcm_cold_result.correct,
                "incorrect_overwrite": lcm_cold_result.incorrect_overwrite,
                "unresolved": lcm_cold_result.unresolved,
                "design_tradeoff_recency": lcm_cold_result.design_tradeoff_recency,
            })

            # Baselines
            for baseline in ALL_BASELINES:
                bl_result = _run_baseline_trial(scenario, baseline)
                results.append({
                    "strategy": baseline.name,
                    "scenario_type": scenario_type,
                    "phase": "n/a",
                    "trial": trial_idx,
                    "correct": bl_result.correct,
                    "incorrect_overwrite": bl_result.incorrect_overwrite,
                    "unresolved": bl_result.unresolved,
                })

            if on_trial is not None:
                on_trial(results)

        print(f"  Completed {len(scenarios)} trials for {scenario_type}")

    return results


def summarise_benchmark_c(results: List[Dict[str, Any]]) -> None:
    """Print per-strategy accuracy metrics to stdout."""
    from collections import defaultdict
    import statistics

    groups: Dict[str, Dict[str, List]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        key = f"{r['strategy']}|{r['scenario_type']}"
        groups[key]["correct"].append(r["correct"])
        groups[key]["incorrect_overwrite"].append(r["incorrect_overwrite"])
        groups[key]["unresolved"].append(r["unresolved"])
        groups[key]["design_tradeoff_recency"].append(r.get("design_tradeoff_recency", False))

    print("\n" + "=" * 80)
    print("BENCHMARK C SUMMARY")
    print(f"{'Strategy':<22} {'Scenario':<28} {'Acc':>6} {'Wrong%':>7} {'Unres%':>7} {'Note'}")
    print("-" * 80)

    for key in sorted(groups):
        strategy, scenario = key.split("|", 1)
        g = groups[key]
        n = len(g["correct"])
        acc   = sum(g["correct"]) / n
        wrong = sum(g["incorrect_overwrite"]) / n
        unres = sum(g["unresolved"]) / n
        note = ""
        if scenario == "graded_ambiguous":
            # For ambiguous tier: unresolved is a valid outcome, not a failure.
            # Show what fraction the system correctly declined vs forced a wrong answer.
            note = f"(unresolved=valid; wrong_decision={wrong:.2f})"
        print(f"{strategy:<22} {scenario:<28} {acc:>6.3f} {wrong:>7.3f} {unres:>7.3f}  {note}")


if __name__ == "__main__":
    import csv as _csv
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    results = asyncio.run(run_benchmark_c(trials_per_scenario=50))
    summarise_benchmark_c(results)

    _Path("benchmark_results").mkdir(exist_ok=True)
    _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    _fname = f"benchmark_results/benchmark_c_evaluation_{_ts}.csv"
    with open(_fname, "w", newline="") as _f:
        _w = _csv.DictWriter(_f, fieldnames=list(results[0].keys()))
        _w.writeheader()
        _w.writerows(results)
    print(f"Saved {len(results)} rows to {_fname}")
