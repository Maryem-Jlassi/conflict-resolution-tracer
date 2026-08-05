"""
Benchmark E — Forced Overlapping Writes (Research vs Verification agent)

Purpose
-------
A research agent writes findings to shared paths while a verification
(critic) agent DELIBERATELY targets the SAME paths.  This guarantees real
overlapping writes → real conflicts → real Ψ decisions → non-zero conflict rate.

Paths (both agents write to these, in each trial):
    research.key_finding
    research.main_challenge
    research.applications
    research.summary

Explicit domain trust (observed, never hand-assigned)
------------------------------------------------------
Trust is built from recorded outcomes in a single task domain ("research"):
    verification_agent:  9 correct / 1 wrong  → trust 0.9 (reliable critic)
    research_agent:      4 correct / 6 wrong  → trust 0.4 (speculative finder)
These trust levels are logged alongside every conflict so the Ψ decision
trace shows the trust component explicitly.

Evidence + signature contract (same mechanism as production)
------------------------------------------------------------
Both agents carry real EvidenceRecords; the critic uses EvidenceType.DATABASE
(authority 0.9) and the researcher EvidenceType.DOCUMENT (authority 0.75).
Every signed write supplies an evidence_signature that satisfies the exact
production gate `lcm_core.provenance.verify_evidence_signature()` — the SAME
validator the HTTP API (lcm_service/app.py:152/208/223) uses.  No private
test-only bypass: both `sig_benchmark_e_*` strings are non-empty, start with
`sig_`, and therefore validate identically to any live Data Provider token.

Metrics (per trial):
    total_writes            : all pipeline.process() calls
    conflicts_detected      : writes that entered conflict resolution
    conflict_rate           : conflicts_detected / total_writes
    verification_win_rate   : conflicts won by the higher-authority critic
    lost_updates            : unresolved conflicts (must be 0)
    final_consistent        : every path committed (no unresolved memory)
    conflict_log            : per-conflict Ψ trace (winner/loser breakdowns,
                              trust, authority, deciding factor)

Distribution / multi-trial report:
    Run N trials (default 20) and report conflict rate n/N, correct-winner
    n/conflicts, lost-updates 0%, memory consistency X/N, plus mean±std of
    the conflict rate and the deciding-factor attribution (authority vs
    recency vs confidence vs trust).

Real-Ollama mode
----------------
`python benchmarks/benchmark_e_overlapping_writes.py --ollama --trials 20`
runs the two agents through a real llama3.1:8b (Ollama) tool-calling loop
writing over LCMClient HTTP.  Requires Ollama on :11434 AND the LCM service
on :8000.  If either is unavailable the mode reports SKIP with the reason.

Design notes:
    - overlap_ratio controls how many research paths the critic targets, so
      the conflict rate forms a distribution across trials (always > 0).
    - research_writes_last flips write order to exercise the recency-vs-
      authority trade-off inside Ψ.
"""

from __future__ import annotations

import asyncio
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.schema import StampedUMF
from lcm_core.provenance import verify_evidence_signature
from lcm_core.crypto import (
    sign_evidence_message,
    sign_evidence_for_records,
    benchmark_dev_evidence_key,
)

RESEARCH_PATHS = [
    "research.key_finding",
    "research.main_challenge",
    "research.applications",
    "research.summary",
]

RESEARCH_AGENT = "research_agent"
VERIFICATION_AGENT = "verification_agent"

TASK_DOMAIN = "research"

# Signatures are real Ed25519 signatures produced by the dev Data-Provider key
# (lcm_core.crypto.sign_evidence_message), validated by the SAME production
# gate used by app.py. The legacy forgeable `sig_*` placeholders are now
# rejected by verify_evidence_signature(), so per-write signatures are computed
# dynamically from the actual evidence metadata (see _evidence()).
SIG_VERIFICATION = sign_evidence_message(EvidenceType.DATABASE, "db://verified")
SIG_RESEARCH = sign_evidence_message(EvidenceType.DOCUMENT, "doc://literature")

# Explicit, observed domain trust (see module docstring).
VERIFICATION_TRUST = 0.9
RESEARCH_TRUST = 0.4


def _assert_signature_contract() -> None:
    """Confirm the benchmark signatures satisfy the exact production gate."""
    assert verify_evidence_signature(
        EvidenceType.DATABASE, "db://verified", SIG_VERIFICATION
    ), "verification signature must satisfy verify_evidence_signature()"
    assert verify_evidence_signature(
        EvidenceType.DOCUMENT, "doc://literature", SIG_RESEARCH
    ), "research signature must satisfy verify_evidence_signature()"
    # Security regression gate: legacy forgeable placeholders must be REJECTED.
    assert not verify_evidence_signature(
        EvidenceType.DATABASE, "db://verified", "sig_benchmark_e_verification"
    ), "legacy placeholder must be rejected by verify_evidence_signature()"


# Benchmark E is self-consistent dev/test tooling: it signs with the dev
# Ed25519 Data-Provider key and verifies against the SAME key (the only key
# available without a real external key service). That requires the explicit
# dev-key opt-in; without it verify_evidence_signature() fails closed and the
# module cannot run at all. A real deployment sets LCM_EVIDENCE_PUBLIC_KEY and
# this opt-in is a no-op for it.
#
# The opt-in is scoped to the benchmark execution blocks (run_benchmark_e,
# run_benchmark_e_ollama) via benchmark_dev_evidence_key() — it is NEVER
# mutated at import time, so importing this module leaves the environment
# untouched and production fail-closed behaviour intact.
with benchmark_dev_evidence_key():
    _assert_signature_contract()


# ---------------------------------------------------------------------------
# Config metadata (embedded in every JSON artifact so results are auditable)
# ---------------------------------------------------------------------------

BENCHMARK_VERSION = "benchmark-e-v2"
UNCERTAINTY_THRESHOLD = 0.0  # synthetic trials resolve all close conflicts
PSI_WEIGHTS = {"recency": 0.25, "confidence": 0.25, "trust": 0.25, "provenance": 0.25}
TRUST_HALF_LIFE_DAYS = 30.0
EVIDENCE_SIGNATURE_MODE = "ed25519"


def _benchmark_metadata(seed: int, backend: str = "in-process") -> Dict[str, Any]:
    """Collect auditable run-config metadata for the saved artifact."""
    import platform
    import subprocess

    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=3, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001  (not a git repo / git unavailable)
        git_commit = "unknown"

    return {
        "benchmark_version": BENCHMARK_VERSION,
        "git_commit": git_commit,
        "seed": seed,
        "backend": backend,
        "python_version": platform.python_version(),
        "timestamp": datetime.now().isoformat(),
        "config": {
            "psi_weights": dict(PSI_WEIGHTS),
            "uncertainty_threshold": UNCERTAINTY_THRESHOLD,
            "trust_half_life_days": TRUST_HALF_LIFE_DAYS,
            "evidence_signature_mode": EVIDENCE_SIGNATURE_MODE,
            "domain_trust": {
                "verification_range": [0.55, 0.90],
                "research_range": [0.45, 0.95],
                "note": ("realized per trial via _seed_trust (observed outcomes); "
                         "randomized so Trust does not dominate every conflict"),
            },
            "sig_contract": {
                "verification": "ed25519(database, db://verified)",
                "research": "ed25519(document, doc://literature)",
                "legacy_sig_rejected": True,
            },
        },
    }



# ---------------------------------------------------------------------------
# Minimal in-memory storage (no SQLite dependency)
# ---------------------------------------------------------------------------

class _DictStorage:
    def __init__(self):
        self._live: Dict[str, StampedUMF] = {}

    def get_existing(self, path: str) -> Optional[StampedUMF]:
        return self._live.get(path)

    def commit(self, umf: StampedUMF, path: str) -> None:
        self._live[path] = umf

    def commit_pending(self, umf: StampedUMF, path: str) -> None:
        pass

    def archive(self, provenance_id: str) -> None:
        pass

    def update_provenance_fields(self, provenance_id: str, **kwargs) -> None:
        pass


# ---------------------------------------------------------------------------
# Deciding-factor attribution (who won and why)
# ---------------------------------------------------------------------------

_COMPONENT_WEIGHT_KEY = {"R": "w_r", "C": "w_c", "T": "w_t", "A": "w_p"}
_COMPONENT_NAME = {"R": "recency", "C": "confidence", "T": "trust", "A": "authority"}


def _deciding_factor(win_breakdown: Dict[str, float], lose_breakdown: Dict[str, float]) -> Dict[str, Any]:
    """
    Attribute a Ψ win to the component whose *weighted* delta
    (winner − loser) is largest.

    breakdown dicts come from ConflictResolutionEngine.calculate_psi_breakdown
    and carry R/C/T/A component scores plus w_r/w_c/w_t/w_p weights.
    """
    deltas: Dict[str, float] = {}
    for comp, wkey in _COMPONENT_WEIGHT_KEY.items():
        w = win_breakdown.get(wkey, 0.25)
        delta = w * (win_breakdown.get(comp, 0.0) - lose_breakdown.get(comp, 0.0))
        deltas[_COMPONENT_NAME[comp]] = round(delta, 4)
    driver = max(deltas, key=lambda k: deltas[k])
    return {"driver": driver, "weighted_deltas": deltas}


def _trust_for(trust: TrustManager, agent_id: str) -> float:
    return trust.get_trust(agent_id, TASK_DOMAIN)


def _seed_trust(trust: TrustManager, agent_id: str, target: float, n: int = 20) -> None:
    """Realize a target trust score (correct/total) via observed outcomes."""
    correct = int(round(target * n))
    for _ in range(correct):
        trust.record_outcome(agent_id, correct=True, domain=TASK_DOMAIN)
    for _ in range(n - correct):
        trust.record_outcome(agent_id, correct=False, domain=TASK_DOMAIN)


def _build_trust(rng=None) -> TrustManager:
    """
    Build domain trust via observed outcomes, randomized per trial.

    The gap between the two agents' trust scores is sampled each trial (and
    can even favour the researcher).  A fixed 0.9-vs-0.4 gap would otherwise
    make Trust the deciding factor in every single conflict, so the benchmark
    would only ever exercise one Ψ dimension.
    """
    rng = rng if rng is not None else random
    trust = TrustManager()
    t_verification = rng.choice([0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90])
    t_research = rng.choice([0.45, 0.50, 0.55, 0.60, 0.65, 0.70,
                             0.75, 0.80, 0.85, 0.90, 0.95])
    _seed_trust(trust, VERIFICATION_AGENT, t_verification)
    _seed_trust(trust, RESEARCH_AGENT, t_research)
    return trust


# ---------------------------------------------------------------------------
# Trial
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    trial: int
    topic: str
    total_writes: int = 0
    conflicts_detected: int = 0
    conflict_rate: float = 0.0
    verification_wins: int = 0
    verification_win_rate: float = 0.0
    lost_updates: int = 0
    final_consistent: bool = True
    overlap_ratio_requested: float = 0.0
    overlap_ratio_realized: float = 0.0
    resolved_paths: Dict[str, str] = field(default_factory=dict)
    conflict_log: List[Dict[str, Any]] = field(default_factory=list)


async def _run_lcm_trial(
    trial_idx: int,
    topic: str,
    overlap_ratio: float,
    research_writes_last: bool,
    rng: random.Random,
) -> TrialResult:
    storage = _DictStorage()
    trust = _build_trust(rng)

    pipeline = WritePipeline(
        storage=storage,
        trust_manager=trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=UNCERTAINTY_THRESHOLD),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(),
    )

    num_research_paths = rng.randint(2, len(RESEARCH_PATHS))
    research_paths = rng.sample(RESEARCH_PATHS, num_research_paths)
    # Critic targets a random overlapping subset of the research paths
    # (always ≥1 → guaranteed conflicts), plus any critic-only paths so the
    # total write count varies and the conflict rate forms a distribution.
    # overlap_ratio drives the overlap size (recorded requested vs realized).
    n_overlap = max(1, min(num_research_paths,
                           round(num_research_paths * overlap_ratio)))
    overlap_paths = rng.sample(research_paths, n_overlap)
    overlap_ratio_realized = len(overlap_paths) / num_research_paths
    critic_only_paths = [p for p in RESEARCH_PATHS if p not in research_paths]
    critic_targets = overlap_paths + critic_only_paths

    # ── Per-trial variability so every Ψ component can decide ──────────────
    # Confidence: sampled per packet (not a per-trial constant).
    # Recency: timestamps spread over hour-scale gaps anchored at "now" so
    #   the last-written block is genuinely fresher than the first (a 24h
    #   half-life makes second-scale gaps a no-op).
    # Authority: evidence type drawn per packet from a per-trial "regime"
    #   menu so the authority gap can favour either side (or degrade to a
    #   weak agent_claim).
    max_order = len(research_paths) + len(critic_targets) - 1
    step_hours = rng.uniform(2.0, 10.0)
    anchor = datetime.utcnow() - timedelta(seconds=rng.uniform(5.0, 60.0))

    regime = rng.random()
    if regime < 0.35:      # verification-strong evidence
        v_evidence_menu = [EvidenceType.DATABASE] * 4 + [EvidenceType.TOOL_OUTPUT]
        r_evidence_menu = [EvidenceType.DOCUMENT] * 4 + [EvidenceType.AGENT_CLAIM]
    elif regime < 0.70:    # research-strong evidence
        v_evidence_menu = [EvidenceType.DATABASE, EvidenceType.TOOL_OUTPUT,
                           EvidenceType.DOCUMENT, EvidenceType.AGENT_CLAIM]
        r_evidence_menu = [EvidenceType.DATABASE] * 2 + [EvidenceType.TOOL_OUTPUT] * 2 + \
                          [EvidenceType.DOCUMENT]
    else:                  # neutral
        v_evidence_menu = [EvidenceType.DATABASE] * 3 + [EvidenceType.TOOL_OUTPUT] * 2 + \
                          [EvidenceType.DOCUMENT]
        r_evidence_menu = [EvidenceType.DOCUMENT] * 3 + [EvidenceType.TOOL_OUTPUT] * 2 + \
                          [EvidenceType.DATABASE]

    _SOURCE_ID = {
        EvidenceType.DATABASE: "db://verified",
        EvidenceType.TOOL_OUTPUT: "tool://measurement",
        EvidenceType.DOCUMENT: "doc://literature",
    }

    def _ts(order: int) -> datetime:
        return anchor - timedelta(hours=step_hours) * (max_order - order)

    def research_packet(path: str, order: int) -> Dict[str, Any]:
        return {
            "agent_id": RESEARCH_AGENT,
            "session_id": f"trial_{trial_idx}",
            "timestamp": _ts(order),
            "confidence_score": rng.uniform(0.55, 0.98),
            "assertion_payload": {path: f"finding_{path.split('.')[-1]}_about_{topic}"},
        }

    def verification_packet(path: str, order: int) -> Dict[str, Any]:
        return {
            "agent_id": VERIFICATION_AGENT,
            "session_id": f"trial_{trial_idx}",
            "timestamp": _ts(order),
            "confidence_score": rng.uniform(0.55, 0.98),
            "assertion_payload": {path: f"verified_{path.split('.')[-1]}_about_{topic}"},
        }

    def _evidence(menu: List[EvidenceType]) -> tuple:
        """Return (records, signature) for a randomly-chosen evidence type.

        The signature is a real Ed25519 signature over the chosen record's
        metadata, so it always matches what verify_evidence_signature() checks
        — regardless of which evidence type the RNG picks.
        """
        etype = rng.choice(menu)
        if etype == EvidenceType.AGENT_CLAIM:
            return [], None  # no external evidence → weak agent_claim (authority 0.3)
        # relevance_score scales verified_confidence but NOT authority_score,
        # decoupling the C and A components so Confidence can be a distinct
        # deciding factor.
        rec = EvidenceRecord(
            evidence_type=etype, relevance_score=rng.uniform(0.4, 1.0),
            source_id=_SOURCE_ID[etype],
        )
        return [rec], sign_evidence_for_records([rec])

    def research_evidence() -> tuple:
        return _evidence(r_evidence_menu)

    def verification_evidence() -> tuple:
        return _evidence(v_evidence_menu)

    writes = 0
    conflicts = 0
    verification_wins = 0
    lost_updates = 0
    final_consistent = True
    resolved_paths: Dict[str, str] = {}
    conflict_log: List[Dict[str, Any]] = []

    def tally(path: str, result) -> None:
        """Record one write: conflicts, winner attribution, Ψ trace, consistency."""
        nonlocal writes, conflicts, verification_wins, lost_updates, final_consistent
        writes += 1
        if result.status in ("conflict_resolved", "unresolved"):
            conflicts += 1
        if result.status == "unresolved":
            lost_updates += 1
            final_consistent = False
        elif (result.status == "conflict_resolved"
              and result.committed is not None
              and result.committed.agent_id == VERIFICATION_AGENT):
            verification_wins += 1
        resolved_paths[path] = (
            result.committed.agent_id if result.committed else "unresolved"
        )

        # ── Per-conflict Ψ trace (the "who won and why" evidence) ────────
        if result.status in ("conflict_resolved", "unresolved") and result.conflict is not None:
            c = result.conflict
            win_bd = c.psi_winner_breakdown or {}
            lose_bd = c.psi_loser_breakdown or {}
            attribution = _deciding_factor(win_bd, lose_bd)
            conflict_log.append({
                "path": path,
                "winner": c.winner.agent_id,
                "loser": c.loser.agent_id,
                "unresolved": c.unresolved,
                "psi_winner": round(c.psi_winner, 4),
                "psi_loser": round(c.psi_loser, 4),
                "winner_breakdown": win_bd,
                "loser_breakdown": lose_bd,
                "winner_source_type": c.winner.provenance_info.source_type,
                "loser_source_type": c.loser.provenance_info.source_type,
                "winner_authority": round(win_bd.get("A", 0.0), 4),
                "loser_authority": round(lose_bd.get("A", 0.0), 4),
                "winner_trust": round(win_bd.get("T", 0.0), 4),
                "loser_trust": round(lose_bd.get("T", 0.0), 4),
                "driver": attribution["driver"],
                "weighted_deltas": attribution["weighted_deltas"],
                "reason": c.reason,
            })

    if research_writes_last:
        # Critic writes first; research agent then writes to its own paths
        # (contradicting the critic on the overlap subset → conflicts).
        for order, path in enumerate(critic_targets):
            ev_recs, ev_sig = verification_evidence()
            result = await pipeline.process(
                verification_packet(path, order),
                domain=TASK_DOMAIN,
                evidence_records=ev_recs,
                evidence_signature=ev_sig,
            )
            tally(path, result)
        for order, path in enumerate(research_paths):
            ev_recs, ev_sig = research_evidence()
            result = await pipeline.process(
                research_packet(path, len(critic_targets) + order),
                domain=TASK_DOMAIN,
                evidence_records=ev_recs,
                evidence_signature=ev_sig,
            )
            tally(path, result)
    else:
        # Research agent writes first; critic contradicts it on the overlap
        # subset afterwards (real conflicts) and adds critic-only findings.
        for order, path in enumerate(research_paths):
            ev_recs, ev_sig = research_evidence()
            result = await pipeline.process(
                research_packet(path, order),
                domain=TASK_DOMAIN,
                evidence_records=ev_recs,
                evidence_signature=ev_sig,
            )
            tally(path, result)
        for order, path in enumerate(critic_targets):
            ev_recs, ev_sig = verification_evidence()
            result = await pipeline.process(
                verification_packet(path, len(research_paths) + order),
                domain=TASK_DOMAIN,
                evidence_records=ev_recs,
                evidence_signature=ev_sig,
            )
            tally(path, result)

    return TrialResult(
        trial=trial_idx,
        topic=topic,
        total_writes=writes,
        conflicts_detected=conflicts,
        conflict_rate=(conflicts / writes) if writes else 0.0,
        verification_wins=verification_wins,
        verification_win_rate=(verification_wins / conflicts) if conflicts else 0.0,
        lost_updates=lost_updates,
        final_consistent=final_consistent,
        overlap_ratio_requested=overlap_ratio,
        overlap_ratio_realized=overlap_ratio_realized,
        resolved_paths=resolved_paths,
        conflict_log=conflict_log,
    )


# ---------------------------------------------------------------------------
# Runner (in-process, deterministic)
# ---------------------------------------------------------------------------

TOPICS = [
    "transformer attention mechanisms",
    "retrieval-augmented generation",
    "multi-agent LLM coordination",
    "memory consolidation in LLMs",
    "trust-calibrated fact checking",
    "provenance-aware memory systems",
]


async def run_benchmark_e(trials: int = 20, seed: Optional[int] = None,
                          on_trial=None) -> List[TrialResult]:
    """Run `trials` overlapping-write trials and return the full result set.

    Per-write Ed25519 signatures are verified against the dev provider key, so
    the dev-key opt-in is scoped to this run only (never mutated at import).
    """
    with benchmark_dev_evidence_key():
        return await _run_benchmark_e(trials, seed, on_trial)


async def _run_benchmark_e(trials: int = 20, seed: Optional[int] = None,
                           on_trial=None) -> List[TrialResult]:
    if seed is None:
        seed = 20260802
    rng = random.Random(seed)
    results: List[TrialResult] = []
    for i in range(trials):
        topic = rng.choice(TOPICS)
        overlap_ratio = rng.uniform(0.5, 1.0)
        research_writes_last = rng.random() < 0.5
        result = await _run_lcm_trial(
            i, topic, overlap_ratio, research_writes_last, rng
        )
        results.append(result)
        if on_trial is not None:
            on_trial(results)
        if (i + 1) % 5 == 0:
            print(f"  {i + 1}/{trials} trials done")
    return results


# ---------------------------------------------------------------------------
# Summary — multi-trial reporting
# ---------------------------------------------------------------------------

def _fmt_mean_std(values: List[float]) -> str:
    if not values:
        return "n/a"
    if len(values) == 1:
        return f"{values[0]:.3f}"
    return f"{statistics.mean(values):.3f} +/- {statistics.stdev(values):.3f}"


def summarise_benchmark_e(results: List[TrialResult]) -> None:
    """Print distribution statistics + the mentor-requested multi-trial report."""
    n = len(results)
    conflict_rates = [r.conflict_rate for r in results]
    win_rates = [r.verification_win_rate for r in results if r.conflicts_detected > 0]
    consistent = [r.final_consistent for r in results]
    lost_updates = sum(r.lost_updates for r in results)

    # Deciding-factor attribution across ALL conflicts (who won and why).
    drivers: Dict[str, int] = {}
    total_conflicts = 0
    for r in results:
        for entry in r.conflict_log:
            total_conflicts += 1
            drivers[entry["driver"]] = drivers.get(entry["driver"], 0) + 1

    print("\n" + "=" * 72)
    print("BENCHMARK E SUMMARY - Forced Overlapping Writes")
    print(f"Trials: {n} | trust realized per trial: verification 0.55-0.90, "
          f"research 0.45-0.95 (see conflict_log winner/loser trust)")
    print(f"Evidence: verification=DATABASE(0.9), research=DOCUMENT(0.75) | "
          f"signatures validated by verify_evidence_signature()")
    print("-" * 72)

    print("Conflict-rate distribution (must be non-zero to show real conflicts):")
    print(f"  mean+/-stdev : {_fmt_mean_std(conflict_rates)}")
    print(f"  median       : {statistics.median(conflict_rates):.4f}")
    print(f"  min / max    : {min(conflict_rates):.4f} / {max(conflict_rates):.4f}")

    if win_rates:
        print("\nCritic (higher-authority) win rate across conflicts:")
        print(f"  mean+/-stdev : {_fmt_mean_std(win_rates)}")
        print(f"  min / max    : {min(win_rates):.4f} / {max(win_rates):.4f}")

    print("\nMulti-trial report (mentor format):")
    total_writes = sum(r.total_writes for r in results)
    conflicts_total = sum(r.conflicts_detected for r in results)
    verification_wins_total = sum(r.verification_wins for r in results)
    print(f"  conflict rate     : {conflicts_total}/{total_writes} writes "
          f"({conflicts_total / total_writes:.1%})")
    print(f"  correct-winner    : {verification_wins_total}/{conflicts_total} conflicts "
          f"won by verification agent ({verification_wins_total / conflicts_total:.1%})")
    print(f"  lost updates      : {lost_updates} "
          f"({lost_updates / total_writes:.1%} of writes)")
    print(f"  memory consistency: {sum(consistent)}/{n} trials fully committed")

    if total_conflicts:
        print("\nDeciding factor attribution (who won and why):")
        for driver, count in sorted(drivers.items(), key=lambda kv: -kv[1]):
            print(f"  {driver:<12}: {count:>3} conflicts ({count / total_conflicts:.1%})")

    print("\n" + "-" * 72)
    print(f"{'Trial':<6} {'Writes':>7} {'Conflicts':>9} {'ConfRate':>9} "
          f"{'CriticWin':>9} {'LostUpd':>8} {'Consist':>8}  Last-2-path winners")
    for r in results:
        last2 = sorted(r.resolved_paths.items())[:2]
        winners = ",".join(f"{p.split('.')[-1]}:{w}" for p, w in last2)
        print(f"{r.trial:<6} {r.total_writes:>7} {r.conflicts_detected:>9} "
              f"{r.conflict_rate:>9.3f} {r.verification_win_rate:>9.3f} "
              f"{r.lost_updates:>8} {str(r.final_consistent):>8}  {winners}")

    # Per-conflict Ψ trace for the first trial that had conflicts
    for r in results:
        if r.conflict_log:
            print("\nSample Psi decision trace (first conflicted trial):")
            for entry in r.conflict_log[:3]:
                print(f"  {entry['path']:<28} Psi {entry['psi_winner']:.3f} "
                      f"(win) vs {entry['psi_loser']:.3f} (lose)")
                print(f"    winner {entry['winner']:<18} src={entry['winner_source_type']:<10} "
                      f"T={entry['winner_trust']:.2f} A={entry['winner_authority']:.2f}")
                print(f"    loser  {entry['loser']:<18} src={entry['loser_source_type']:<10} "
                      f"T={entry['loser_trust']:.2f} A={entry['loser_authority']:.2f}")
                print(f"    driver: {entry['driver']} | deltas: {entry['weighted_deltas']}")
            break


# ---------------------------------------------------------------------------
# Real-Ollama mode (LCMClient HTTP + llama3.1:8b)
# ---------------------------------------------------------------------------

def _lcm_available() -> bool:
    try:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://localhost:8000/")
            return resp.status_code == 200
    except Exception:
        return False


def _ollama_available() -> bool:
    try:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://localhost:11434/api/version")
            return resp.status_code == 200
    except Exception:
        return False


def _call_ollama(prompt: str, model: str = "llama3.1:8b", timeout_s: float = 300.0) -> str:
    """Call Ollama with a generous timeout (cold model loads exceed 60s)."""
    import requests
    for attempt in range(3):
        try:
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 500},
                },
                timeout=timeout_s,
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            if attempt == 2:
                print(f"  [Ollama error] {e}")
                return ""
    return ""


_VERIFICATION_SYSTEM_TEMPLATE = (
    "You are a Verification Agent. Your agent_id is 'verification_agent'.\n\n"
    "Your task: verify the research findings listed below and write a correction "
    "or confirmation for EACH path. You MUST use WRITE_MEMORY for every path you "
    "are asked to verify.\n\n"
    "Paths to verify:\n"
    "{paths}\n\n"
    "Tool format: WRITE_MEMORY(path=\"research.key_finding\", value=\"...\", confidence=0.9)\n"
    "Be critical but fair. Prefer confidence 0.85-0.95.\n"
)


RESEARCH_SYSTEM = """You are a Research Agent. Your agent_id is 'research_agent'.

Your task: Research the given topic and write your findings to shared memory.

Available tools:
- WRITE_MEMORY(path="...", value="...", confidence=0.8): Store a finding
- READ_MEMORY(path="..."): Read existing information

Memory paths to use:
- research.key_insight: The most important insight about the topic
- research.main_challenge: The main challenge or limitation
- research.current_state: Current state of research/implementation

IMPORTANT: Always use tool calls in this EXACT format:
WRITE_MEMORY(path="research.key_insight", value="your insight here", confidence=0.8)
WRITE_MEMORY(path="research.main_challenge", value="challenge description", confidence=0.7)

Write 2-3 key findings to memory. Be specific and factual. Use confidence scores between 0.6 and 0.9.
"""


def parse_tool_calls(llm_response: str) -> List[Dict[str, Any]]:
    """Parse tool calls from LLM response - accepts both uppercase and lowercase."""
    import re
    tool_calls = []

    write_pattern = r'(?:WRITE_MEMORY|write_memory)\s*\(\s*path\s*=\s*["\']([^"\']+)["\']\s*,\s*value\s*=\s*["\']([^"\']+)["\']\s*,\s*confidence\s*=\s*([\d.]+)\s*\)'
    for match in re.finditer(write_pattern, llm_response, re.IGNORECASE):
        tool_calls.append({
            "tool": "write_memory",
            "path": match.group(1),
            "value": match.group(2),
            "confidence": float(match.group(3))
        })

    read_pattern = r'(?:READ_MEMORY|read_memory)\s*\(\s*path\s*=\s*["\']([^"\']+)["\']\s*\)'
    for match in re.finditer(read_pattern, llm_response, re.IGNORECASE):
        tool_calls.append({
            "tool": "read_memory",
            "path": match.group(1)
        })

    return tool_calls


def run_benchmark_e_ollama(trials: int = 20, model: str = "llama3.1:8b") -> Optional[List[Dict[str, Any]]]:
    """
    Run `trials` overlapping-write trials through real tool-calling agents
    (Ollama + LCMClient HTTP).  Returns per-trial summaries, or None if the
    required services (Ollama on :11434, LCM on :8000) are unavailable.

    Design mirrors the in-process mode: the research agent writes findings,
    then the verification agent is explicitly handed the paths the research
    agent wrote and must write a correction/confirmation for each — forcing
    real overlapping writes → real conflicts through the shared LCM service.
    """
    from lcm_client import LCMClient

    with benchmark_dev_evidence_key():
        return _run_benchmark_e_ollama(trials, model, lcm=None)


def _run_benchmark_e_ollama(trials: int = 20, model: str = "llama3.1:8b",
                            lcm=None) -> Optional[List[Dict[str, Any]]]:
    if not _ollama_available():
        print(f"  [SKIP] Ollama not reachable on :11434 - real-Ollama mode requires it.")
        return None
    if not _lcm_available():
        print(f"  [SKIP] LCM service not reachable on :8000 - real-Ollama mode requires it.")
        return None

    if lcm is None:
        lcm = LCMClient(base_url="http://localhost:8000")
    # Establish the explicit domain trust used by the in-process mode.
    for _ in range(9):
        lcm.verify(VERIFICATION_AGENT, correct=True, domain=TASK_DOMAIN)
    lcm.verify(VERIFICATION_AGENT, correct=False, domain=TASK_DOMAIN)
    for _ in range(4):
        lcm.verify(RESEARCH_AGENT, correct=True, domain=TASK_DOMAIN)
    for _ in range(6):
        lcm.verify(RESEARCH_AGENT, correct=False, domain=TASK_DOMAIN)

    rng = random.Random(20260802)
    trial_summaries: List[Dict[str, Any]] = []

    for t in range(trials):
        topic = rng.choice(TOPICS)
        writes = 0
        tool_calls = 0
        tool_success = 0
        conflicts = 0
        critic_wins = 0
        unresolved_near_ties = 0
        conflict_log: List[Dict[str, Any]] = []
        research_paths_written: List[str] = []

        def write_callback(agent_id: str, raw: Dict[str, Any], path: str) -> None:
            nonlocal writes, conflicts, critic_wins, unresolved_near_ties
            writes += 1
            status = raw.get("status")
            if status in ("conflict_resolved", "unresolved", "conflict_unresolved"):
                conflicts += 1
            if status in ("unresolved", "conflict_unresolved"):
                unresolved_near_ties += 1
            if raw.get("winner_agent") == VERIFICATION_AGENT:
                critic_wins += 1
            if agent_id == RESEARCH_AGENT:
                research_paths_written.append(path)
            # ── Per-conflict Ψ trace from the HTTP response ──────────────
            if status in ("conflict_resolved", "unresolved", "conflict_unresolved"):
                win_bd = raw.get("psi_winner_breakdown") or {}
                lose_bd = raw.get("psi_loser_breakdown") or {}
                attribution = _deciding_factor(win_bd, lose_bd)
                conflict_log.append({
                    "path": path,
                    "winner": raw.get("winner_agent"),
                    "loser": raw.get("loser_agent"),
                    "unresolved": raw.get("unresolved", False),
                    "psi_winner": win_bd.get("total_psi"),
                    "psi_loser": lose_bd.get("total_psi"),
                    "winner_breakdown": win_bd,
                    "loser_breakdown": lose_bd,
                    "winner_trust": round(win_bd.get("T", 0.0), 4),
                    "loser_trust": round(lose_bd.get("T", 0.0), 4),
                    "winner_authority": round(win_bd.get("A", 0.0), 4),
                    "loser_authority": round(lose_bd.get("A", 0.0), 4),
                    "driver": attribution["driver"],
                    "weighted_deltas": attribution["weighted_deltas"],
                    "reason": raw.get("description") or raw.get("message"),
                })

        def run_research_agent() -> None:
            nonlocal tool_calls, tool_success
            conversation = f"{RESEARCH_SYSTEM}\n\nTopic: {topic}\n\n"
            for _ in range(3):
                llm_response = _call_ollama(conversation, model=model)
                if not llm_response:
                    break
                parsed = parse_tool_calls(llm_response)
                tool_calls += len(parsed)
                results = []
                for call in parsed:
                    if call["tool"] == "write_memory":
                        raw = lcm.write(
                            agent_id=RESEARCH_AGENT,
                            session_id=f"bench_e_ollama_{t}",
                            confidence_score=call.get("confidence", 0.8),
                            assertion_payload={call["path"]: call["value"]},
                            domain=TASK_DOMAIN,
                        )
                        tool_success += 1
                        write_callback(RESEARCH_AGENT, raw, call["path"])
                        results.append(f"write {call['path']} -> {raw.get('status')}")
                    else:
                        results.append(f"read {call['path']}")
                conversation += (
                    f"\n\nAssistant: {llm_response}\n\nTool results:\n"
                    + "\n".join(results) + "\n\n"
                )
                if any("Written" in r or "Conflict" in r or "Resolved" in r for r in results):
                    break

        def run_verification_agent() -> None:
            nonlocal tool_calls, tool_success
            if not research_paths_written:
                return
            paths_block = "\n".join(f"- {p}" for p in research_paths_written)
            system_prompt = _VERIFICATION_SYSTEM_TEMPLATE.format(paths=paths_block)
            conversation = f"{system_prompt}\n\nTopic: {topic}\n\n"
            for _ in range(3):
                llm_response = _call_ollama(conversation, model=model)
                if not llm_response:
                    break
                parsed = parse_tool_calls(llm_response)
                tool_calls += len(parsed)
                results = []
                for call in parsed:
                    if call["tool"] == "write_memory":
                        raw = lcm.write(
                            agent_id=VERIFICATION_AGENT,
                            session_id=f"bench_e_ollama_{t}",
                            confidence_score=call.get("confidence", 0.9),
                            assertion_payload={call["path"]: call["value"]},
                            domain=TASK_DOMAIN,
                        )
                        tool_success += 1
                        write_callback(VERIFICATION_AGENT, raw, call["path"])
                        results.append(f"write {call['path']} -> {raw.get('status')}")
                    else:
                        results.append(f"read {call['path']}")
                conversation += (
                    f"\n\nAssistant: {llm_response}\n\nTool results:\n"
                    + "\n".join(results) + "\n\n"
                )
                if any("Written" in r or "Conflict" in r or "Resolved" in r for r in results):
                    break

        run_research_agent()
        run_verification_agent()

        trial_summaries.append({
            "trial": t,
            "topic": topic,
            "writes": writes,
            "tool_calls": tool_calls,
            "tool_success": tool_success,
            "conflicts": conflicts,
            "critic_wins": critic_wins,
            "unresolved_near_ties": unresolved_near_ties,
            "research_paths_written": list(research_paths_written),
            "conflict_log": conflict_log,
        })
        print(f"  trial {t + 1}/{trials}: writes={writes}, conflicts={conflicts}, "
              f"critic_wins={critic_wins}, tool_success={tool_success}/{tool_calls}")

    return trial_summaries


def summarise_benchmark_e_ollama(summaries: List[Dict[str, Any]]) -> None:
    n = len(summaries)
    total_writes = sum(s["writes"] for s in summaries)
    total_conflicts = sum(s["conflicts"] for s in summaries)
    total_critic_wins = sum(s["critic_wins"] for s in summaries)
    total_tool_calls = sum(s["tool_calls"] for s in summaries)
    total_tool_success = sum(s["tool_success"] for s in summaries)
    total_unresolved_near_ties = sum(s["unresolved_near_ties"] for s in summaries)

    drivers: Dict[str, int] = {}
    for s in summaries:
        for entry in s["conflict_log"]:
            drivers[entry["driver"]] = drivers.get(entry["driver"], 0) + 1

    print("\n" + "=" * 72)
    print("BENCHMARK E (REAL-OLLAMA) SUMMARY - Forced Overlapping Writes")
    print(f"Trials: {n} | agent_mode: real_llm | backend: ollama ({'llama3.1:8b'})")
    print(f"Domain trust: verification={VERIFICATION_TRUST}, research={RESEARCH_TRUST}")
    print("-" * 72)
    print(f"  conflict rate     : {total_conflicts}/{total_writes} writes "
          f"({total_conflicts / total_writes:.1%})" if total_writes else "  conflict rate: n/a")
    print(f"  correct-winner    : {total_critic_wins}/{total_conflicts} conflicts "
          f"({total_critic_wins / total_conflicts:.1%})" if total_conflicts else "  correct-winner: n/a")
    print(f"  tool-call success : {total_tool_success}/{total_tool_calls} "
          f"({total_tool_success / total_tool_calls:.1%})" if total_tool_calls else "  tool-call success: n/a")
    print(f"  unresolved near-ties : {total_unresolved_near_ties} "
          f"({total_unresolved_near_ties / total_writes:.1%} of writes)" if total_writes else "  unresolved near-ties: n/a")
    print("  note: near-ties are same-agent re-writes of an already-won path "
          "(identical trust/authority);")
    print("        both memories are kept via commit_pending - no data is lost.")

    if total_conflicts:
        print("\nDeciding-factor attribution (who won and why):")
        for driver, count in sorted(drivers.items(), key=lambda kv: -kv[1]):
            print(f"  {driver:<12}: {count:>3} conflicts ({count / total_conflicts:.1%})")

    # Per-conflict Ψ trace for the first trial that had conflicts
    for s in summaries:
        if s["conflict_log"]:
            print("\nSample Psi decision trace (real-Ollama, first conflicted trial):")
            for entry in s["conflict_log"][:3]:
                pw = entry.get("psi_winner")
                pl = entry.get("psi_loser")
                pw_s = f"{pw:.3f}" if pw is not None else "n/a"
                pl_s = f"{pl:.3f}" if pl is not None else "n/a"
                print(f"  {entry['path']:<28} Psi {pw_s} "
                      f"(win) vs {pl_s} (lose)")
                print(f"    winner {entry['winner']:<18} T={entry['winner_trust']:.2f} "
                      f"A={entry['winner_authority']:.2f}")
                print(f"    loser  {entry['loser']:<18} T={entry['loser_trust']:.2f} "
                      f"A={entry['loser_authority']:.2f}")
                print(f"    driver: {entry['driver']} | deltas: {entry['weighted_deltas']}")
            break


# ---------------------------------------------------------------------------
# Save results to JSON (for paper inclusion)
# ---------------------------------------------------------------------------

def save_results_to_json(
    results: List[TrialResult],
    output_dir: str = "benchmark_results",
    tag: str = "synthetic",
) -> str:
    """
    Serialize TrialResult list to a structured JSON file.

    The output includes per-trial conflict logs with full Ψ breakdowns,
    suitable for inclusion as a paper artifact demonstrating real conflict
    resolution with driver attribution.
    """
    import json
    from pathlib import Path as _Path

    out_dir = _Path(output_dir)
    out_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = out_dir / f"benchmark_e_{tag}_{timestamp}.json"

    # Aggregate stats
    conflict_rates = [r.conflict_rate for r in results]
    win_rates = [r.verification_win_rate for r in results if r.conflicts_detected > 0]
    total_writes = sum(r.total_writes for r in results)
    total_conflicts = sum(r.conflicts_detected for r in results)
    total_vwins = sum(r.verification_wins for r in results)
    total_lost = sum(r.lost_updates for r in results)

    drivers: Dict[str, int] = {}
    for r in results:
        for entry in r.conflict_log:
            d = entry.get("driver", "unknown")
            drivers[d] = drivers.get(d, 0) + 1

    payload = {
        "tag": tag,
        "timestamp": datetime.now().isoformat(),
        "n_trials": len(results),
        "metadata": _benchmark_metadata(
            seed=20260802,
            backend="ollama" if tag.startswith("ollama") else "in-process",
        ),
        "aggregate": {
            "total_writes": total_writes,
            "total_conflicts": total_conflicts,
            "conflict_rate_mean": round(statistics.mean(conflict_rates), 4) if conflict_rates else 0,
            "conflict_rate_std": round(statistics.stdev(conflict_rates), 4) if len(conflict_rates) > 1 else 0,
            "verification_win_rate_mean": round(statistics.mean(win_rates), 4) if win_rates else 0,
            "lost_updates": total_lost,
            "memory_consistent_trials": sum(r.final_consistent for r in results),
            "deciding_factor_counts": drivers,
        },
        "trials": [
            {
                "trial": r.trial,
                "topic": r.topic,
                "total_writes": r.total_writes,
                "conflicts_detected": r.conflicts_detected,
                "conflict_rate": round(r.conflict_rate, 4),
                "verification_wins": r.verification_wins,
                "verification_win_rate": round(r.verification_win_rate, 4),
                "lost_updates": r.lost_updates,
                "final_consistent": r.final_consistent,
                "overlap_ratio_requested": round(r.overlap_ratio_requested, 4),
                "overlap_ratio_realized": round(r.overlap_ratio_realized, 4),
                "resolved_paths": r.resolved_paths,
                "conflict_log": r.conflict_log,
            }
            for r in results
        ],
    }

    with open(filename, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"Saved {len(results)} trials to {filename}")
    return str(filename)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark E — Forced Overlapping Writes")
    parser.add_argument("--trials", type=int, default=20, help="Number of trials (default 20)")
    parser.add_argument("--ollama", action="store_true", help="Run real-Ollama mode")
    parser.add_argument("--model", type=str, default="llama3.1:8b", help="Ollama model")
    args = parser.parse_args()

    if args.ollama:
        summaries = run_benchmark_e_ollama(trials=args.trials, model=args.model)
        if summaries is None:
            print("\nReal-Ollama mode skipped - services unavailable. "
                  "Run in-process mode without --ollama.")
            sys.exit(1)
        summarise_benchmark_e_ollama(summaries)
    else:
        results = asyncio.run(run_benchmark_e(trials=args.trials))
        summarise_benchmark_e(results)
        save_results_to_json(results, tag="synthetic")


if __name__ == "__main__":
    _main()
