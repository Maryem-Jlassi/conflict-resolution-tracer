"""
Enhanced Benchmark B — False-Memory (Mandela Injection) with multiple attack types

Attack types:
1. Repetition: same false claim many times
2. Confidence attack: fake high confidence
3. Freshness attack: many new false claims
4. Collusion: many different low-trust agents
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
from benchmarks.baseline import NoLCMStorage


class _DictStorage:
    def __init__(self):
        self._live = {}

    def get_existing(self, path):
        return self._live.get(path)

    def commit(self, umf, path):
        self._live[path] = umf

    def commit_pending(self, umf, path):
        pass

    def archive(self, provenance_id):
        pass

    def update_provenance_fields(self, provenance_id, **kwargs):
        pass

    def read(self, path):
        umf = self._live.get(path)
        if umf is None:
            return None
        return {"agent_id": umf.agent_id, "value": next(iter(umf.assertion_payload.values()))}


class AttackType:
    REPETITION = "repetition"
    CONFIDENCE = "confidence"
    FRESHNESS = "freshness"
    COLLUSION = "collusion"


async def run_attack(
    attack_type: str,
    repetitions: int,
    use_lcm: bool = True,
    path: str = "patient.blood_type"
) -> Dict[str, Any]:
    if use_lcm:
        storage = _DictStorage()
        trust = TrustManager()
        # Trusted doctor
        for _ in range(20):
            trust.record_outcome("trusted_doctor", correct=True)

        # For collusion, create many low-trust agents
        attack_agents = []
        if attack_type == AttackType.COLLUSION:
            attack_agents = [f"attacker_{i}" for i in range(repetitions)]
            for agent in attack_agents:
                for _ in range(15):
                    trust.record_outcome(agent, correct=False)
        else:
            # Single low-trust agent for other attacks
            attack_agents = ["low_trust_agent"]
            for _ in range(20):
                trust.record_outcome("low_trust_agent", correct=False)

        pipeline = WritePipeline(
            storage=storage,
            trust_manager=trust,
            conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.0),
            lock_manager=AsyncLockManager(),
            loop_detector=LoopDetector(rate_threshold=1000),
        )
        now = datetime.utcnow()

        # Initial true value from trusted doctor
        await pipeline.process({
            "agent_id": "trusted_doctor", "session_id": "baseline",
            "timestamp": now, "confidence_score": 0.95,
            "assertion_payload": {path: "A+"},
        })

        false_rejected = 0
        for i in range(repetitions):
            agent_id = random.choice(attack_agents)

            # Attack-specific parameters
            confidence = 0.8
            if attack_type == AttackType.CONFIDENCE:
                confidence = 0.99
            timestamp = now + timedelta(seconds=i + 1)

            result = await pipeline.process({
                "agent_id": agent_id,
                "session_id": f"attack_{i}",
                "timestamp": timestamp,
                "confidence_score": confidence,
                "assertion_payload": {path: "O+"},
            })

            if result.committed and result.committed.agent_id == "trusted_doctor":
                false_rejected +=1

        final = storage.read(path)
        final_value = final["value"] if final else None
        final_agent = final["agent_id"] if final else None
        trap_eff = false_rejected / repetitions if repetitions >0 else 0

        return {
            "system": "LCM",
            "attack_type": attack_type,
            "repetitions": repetitions,
            "false_claims_submitted": repetitions,
            "false_claims_rejected": false_rejected,
            "contradiction_trapping_efficiency": trap_eff,
            "final_value": final_value,
            "final_agent": final_agent,
            "final_is_correct": final_value == "A+",
        }

    else:
        # No LCM
        storage = NoLCMStorage(":memory:")
        now = datetime.utcnow()
        storage.write(path, "trusted_doctor", "A+", now)
        attack_agents = [f"attacker_{i}" for i in range(repetitions)] if attack_type == AttackType.COLLUSION else ["low_trust_agent"]

        for i in range(repetitions):
            agent_id = random.choice(attack_agents)
            await asyncio.sleep(0)
            storage.write(path, agent_id, "O+", now + timedelta(seconds=i+1))

        final = storage.read(path)
        final_value = final["value"] if final else None
        final_agent = final["agent_id"] if final else None

        return {
            "system": "No-LCM",
            "attack_type": attack_type,
            "repetitions": repetitions,
            "false_claims_submitted": repetitions,
            "false_claims_rejected": 0,
            "contradiction_trapping_efficiency": 0.0,
            "final_value": final_value,
            "final_agent": final_agent,
            "final_is_correct": final_value == "A+",
        }


async def run_enhanced_benchmark(
    attack_types: List[str] = None,
    repetition_counts: List[int] = None,
    trials: int = 20
) -> List[Dict]:
    if attack_types is None:
        attack_types = [
            AttackType.REPETITION,
            AttackType.CONFIDENCE,
            AttackType.FRESHNESS,
            AttackType.COLLUSION
        ]
    if repetition_counts is None:
        repetition_counts = [5, 25, 100]

    results = []
    for attack_type in attack_types:
        print(f"\n--- RUNNING ATTACK TYPE: {attack_type} ---")
        for reps in repetition_counts:
            print(f"  Repetitions: {reps}")
            for trial in range(trials):
                lcm_result = await run_attack(attack_type, reps, use_lcm=True)
                lcm_result["trial"] = trial
                results.append(lcm_result)

                no_lcm_result = await run_attack(attack_type, reps, use_lcm=False)
                no_lcm_result["trial"] = trial
                results.append(no_lcm_result)

                if (trial +1) %10 ==0:
                    print(f"    Trial {trial+1}/{trials} done")
    return results


if __name__ == "__main__":
    import statistics
    print("=== ENHANCED MANDELA ATTACK BENCHMARK ===")
    results = asyncio.run(run_enhanced_benchmark(trials=10))

    # Print summary
    for system in ["LCM", "No-LCM"]:
        print(f"\n--- {system} SUMMARY ---")
        for attack_type in [AttackType.REPETITION, AttackType.CONFIDENCE, AttackType.FRESHNESS, AttackType.COLLUSION]:
            for reps in [5,25,100]:
                filtered = [
                    r for r in results
                    if r["system"] == system
                    and r["attack_type"] == attack_type
                    and r["repetitions"] == reps
                ]
                if not filtered:
                    continue

                eff = [r["contradiction_trapping_efficiency"] for r in filtered]
                cor = [r["final_is_correct"] for r in filtered]
                print(f"{attack_type} R={reps}: Trap Eff={statistics.mean(eff):.3f}, Final Correct={sum(cor)/len(cor):.3f}")
