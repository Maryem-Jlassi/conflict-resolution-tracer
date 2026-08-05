"""
Benchmark B — False-Memory (Mandela Injection)

Question: Does repetition of a false claim by low-trust sources override
          a high-trust baseline?

Metrics:
  - contradiction_trapping_efficiency : false claims rejected / total false claims
  - final_is_correct                  : committed value matches baseline truth

Trust-gap sweep:
  Instead of a fixed (trusted=1.0, attacker=0.0) pairing, run_lcm_mandela_test
  accepts an arbitrary trust_pair and seeds both agents' trust histories to
  those exact scores (trust = verified_correct / total_claims). Sweeping the
  trust gap shows how trapping efficiency degrades as the two agents become
  harder to distinguish.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List
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


class _DictStorage:
    def __init__(self):
        self._live = {}

    def get_existing(self, path):
        return self._live.get(path)

    def commit(self, umf, path):
        self._live[path] = umf

    def commit_pending(self, umf, path):
        # Mock implementation for benchmark
        pass

    def archive(self, provenance_id):
        pass

    def update_provenance_fields(self, provenance_id, **fields):
        # Mock implementation for benchmark
        pass

    def read(self, path):
        umf = self._live.get(path)
        if umf is None:
            return None
        return {"agent_id": umf.agent_id, "value": next(iter(umf.assertion_payload.values()))}


TRUST_GAP_SWEEP = [
    # Trusted / attacker scores seeded via correct/total outcome history.
    (0.9, 0.1),   # realized gap 0.80
    (0.7, 0.3),   # realized gap 0.40
    (0.6, 0.4),   # realized gap 0.20  (ΔΨ = 0.25·0.2 = 0.05 = uncertainty threshold)
    (0.55, 0.45), # realized gap 0.10
]

# Extend the sweep across the uncertainty threshold boundary (0.05 / w_t = 0.20)
# so the trapping cliff is resolved, not hidden between coarse pairs.
TRUST_GAP_SWEEP_FINE = TRUST_GAP_SWEEP + [
    (1.0, 0.0),   # realized gap 1.00
    (0.8, 0.2),   # realized gap 0.60
    (0.65, 0.35), # realized gap 0.30
    (0.525, 0.475),  # realized gap 0.00 (both agents near-identical trust)
]

# Production-default uncertainty threshold: |ΔΨ| below this ⇒ unresolved.
PRODUCTION_UNCERTAINTY_THRESHOLD = 0.05

# Freeze temporal trust decay during the sweep so the ONLY varied input is the
# seeded trust gap (temporal decay is studied separately in Benchmark E).
SWEEP_TRUST_HALF_LIFE_DAYS = 365_000.0


def _seed_trust_target(trust: TrustManager, agent_id: str, target: float,
                       total: int = 20) -> float:
    """Seed an agent's trust history so its score ≈ target (correct/total).

    Outcomes are stamped with a future timestamp so temporal decay computes
    delta_t = 0 and the queried trust equals the raw correct/total exactly
    (deterministic; decay is studied separately in Benchmark E).

    Returns the realized score (correct/total) after 0.05-grid quantization.
    """
    future = datetime.utcnow() + timedelta(days=1)
    correct = int(round(target * total))
    for _ in range(correct):
        trust.record_outcome(agent_id, correct=True, timestamp=future)
    for _ in range(total - correct):
        trust.record_outcome(agent_id, correct=False, timestamp=future)
    return correct / total


async def run_lcm_mandela_test(repetitions, path="patient.blood_type", bypass_rate=0.05,
                               trust_pair=(1.0, 0.0), uncertainty_threshold=0.0,
                               trust_half_life_days=30.0):
    storage = _DictStorage()
    trust = TrustManager(half_life_days=trust_half_life_days)
    realized_trusted = _seed_trust_target(trust, "trusted_doctor", trust_pair[0])
    realized_attacker = _seed_trust_target(trust, "low_trust_agent", trust_pair[1])

    pipeline = WritePipeline(
        storage=storage,
        trust_manager=trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=uncertainty_threshold),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(rate_threshold=1000),
    )

    now = datetime.utcnow()
    # All packets carry timestamps in the future relative to wall clock, so
    # recency clamps to exactly 1.0 for baseline and attacker alike (ΔR ≡ 0).
    # This isolates the trust gap as the sole differentiator, and the 1s
    # spacing keeps the loop detector at a benign ~1 write/sec.
    base_ts = now + timedelta(hours=1)
    await pipeline.process({
        "agent_id": "trusted_doctor", "session_id": "baseline",
        "timestamp": base_ts, "confidence_score": 0.95,
        "assertion_payload": {path: "A+"},
    })

    gate_rejections = 0  # Rejected before conflict resolution (e.g., evidence verification)
    conflict_losses = 0  # Entered conflict resolution but lost to trusted_doctor
    unresolved_count = 0  # Declined to resolve (|ΔΨ| < uncertainty_threshold)
    bypassed = 0

    for i in range(repetitions):
        # Simulate occasional bypass of trust mechanism (e.g., compromised credentials)
        if random.random() < bypass_rate:
            bypassed += 1
            # In bypass case, the false claim might succeed
            result = await pipeline.process({
                "agent_id": "low_trust_agent", "session_id": f"attack_{i}",
                "timestamp": base_ts + timedelta(seconds=i + 1),
                "confidence_score": 0.95,  # Higher confidence during bypass
                "assertion_payload": {path: "O+"},
            })
        else:
            result = await pipeline.process({
                "agent_id": "low_trust_agent", "session_id": f"attack_{i}",
                "timestamp": base_ts + timedelta(seconds=i + 1),
                "confidence_score": 0.8,
                "assertion_payload": {path: "O+"},
            })
        
        # Separate gate rejections from conflict losses. Count every rejection
        # status the pipeline can return (hard-trust gate, loop freeze, evidence).
        if result.status in ("rejected_untrusted", "rejected_suspicious",
                             "loop_frozen", "evidence_rejected", "rejected"):
            gate_rejections += 1
        elif result.status == "conflict_resolved":
            # Entered conflict resolution - check if trusted_doctor won
            if result.committed and result.committed.agent_id == "trusted_doctor":
                conflict_losses += 1
        elif result.status == "unresolved":
            # Declined to resolve: attacker stored as pending, incumbent stays live
            unresolved_count += 1
        elif result.status == "committed":
            # Direct commit - check if it was the trusted baseline
            if result.committed and result.committed.agent_id == "trusted_doctor":
                conflict_losses += 1

    final = storage.read(path)
    final_value = final["value"] if final else None
    final_agent = final["agent_id"] if final else None
    
    # Calculate metrics
    total_rejections = gate_rejections + conflict_losses
    trapping_efficiency = total_rejections / repetitions if repetitions > 0 else 0
    unresolved_rate = unresolved_count / repetitions if repetitions > 0 else 0

    # Variance score: measure consistency of rejection across trials
    # Higher variance = less predictable behavior
    variance_score = (gate_rejections / repetitions) if repetitions > 0 else 0

    return {
        "system": "LCM", "repetitions": repetitions,
        "false_claims_submitted": repetitions,
        "gate_rejections": gate_rejections,
        "conflict_losses": conflict_losses,
        "unresolved_count": unresolved_count,
        "trapping_efficiency": trapping_efficiency,
        "unresolved_rate": unresolved_rate,
        "final_value": final_value, "final_agent": final_agent,
        "final_is_correct": final_value == "A+" and final_agent == "trusted_doctor",
        "bypassed_attacks": bypassed,
        "variance_score": variance_score,
        "trust_pair": list(trust_pair),
        "trust_pair_realized": [realized_trusted, realized_attacker],
        "trust_gap": round(realized_trusted - realized_attacker, 4),
        "uncertainty_threshold": uncertainty_threshold,
    }


async def run_no_lcm_mandela_test(repetitions, path="patient.blood_type"):
    storage = NoLCMStorage(":memory:")
    now = datetime.utcnow()
    storage.write(path, "trusted_doctor", "A+", now)
    for i in range(repetitions):
        await asyncio.sleep(0)
        storage.write(path, "low_trust_agent", "O+", now + timedelta(seconds=i + 1))
    final = storage.read(path)
    final_value = final["value"] if final else None
    final_agent = final["agent_id"] if final else None
    return {
        "system": "No-LCM", "repetitions": repetitions,
        "false_claims_submitted": repetitions, "false_claims_rejected": 0,
        "contradiction_trapping_efficiency": 0.0,
        "final_value": final_value, "final_agent": final_agent,
        "final_is_correct": (final_value == "A+"),
        "trust_pair": None, "trust_gap": None,
    }


async def run_benchmark_b(repetition_counts=None, trials=50, on_trial=None,
                          trust_pairs=None, uncertainty_threshold=0.0,
                          trust_half_life_days=30.0):
    if repetition_counts is None:
        repetition_counts = [1, 10, 50, 200]
    if trust_pairs is None:
        trust_pairs = [(1.0, 0.0)]
    results = []
    for reps in repetition_counts:
        print(f"\n=== Benchmark B: R={reps} repetitions ===")
        for pair in trust_pairs:
            print(f"  trust_pair=({pair[0]:.2f}, {pair[1]:.2f}) gap={pair[0] - pair[1]:.2f}")
            for trial in range(trials):
                lcm = await run_lcm_mandela_test(reps, trust_pair=pair,
                                                 uncertainty_threshold=uncertainty_threshold,
                                                 trust_half_life_days=trust_half_life_days)
                lcm["trial"] = trial
                results.append(lcm)
                no_lcm = await run_no_lcm_mandela_test(reps)
                no_lcm["trial"] = trial
                results.append(no_lcm)
                if on_trial is not None:
                    on_trial(results)
                if (trial + 1) % 10 == 0:
                    print(f"  {trial + 1}/{trials} trials done")
    return results


if __name__ == "__main__":
    import statistics
    print("Benchmark B — False-Memory (Mandela Injection)")
    print("=" * 60)
    results = asyncio.run(run_benchmark_b(repetition_counts=[1, 10, 50], trials=30))
    for system in ["LCM", "No-LCM"]:
        for reps in [1, 10, 50]:
            filtered = [r for r in results if r["system"] == system and r["repetitions"] == reps]
            if filtered:
                eff = [r["contradiction_trapping_efficiency"] for r in filtered]
                cor = [r["final_is_correct"] for r in filtered]
                print(f"{system} R={reps}: Trap Eff={statistics.mean(eff):.3f}, "
                      f"Final Correct={sum(cor)/len(cor):.3f}")
