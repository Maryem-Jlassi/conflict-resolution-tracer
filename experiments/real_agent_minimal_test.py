"""
Minimal Real Agent Test - Direct LCM interaction without LLM dependencies

This test demonstrates the trust gate and multi-agent conflict resolution
using direct LCM calls without requiring LangChain or Ollama.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

# Use in-memory storage for testing
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.schema import StampedUMF, ProvenanceInfo


class DictStorage:
    """Simple in-memory storage for testing."""
    def __init__(self):
        self._live = {}
        self._committed = {}

    def get_existing(self, path):
        return self._live.get(path)

    def commit(self, umf, path):
        self._live[path] = umf
        self._committed[path] = umf

    def commit_pending(self, umf, path):
        pass

    def archive(self, provenance_id):
        pass

    def update_provenance_fields(self, provenance_id, **fields):
        pass

    def read(self, path):
        umf = self._committed.get(path)
        if umf is None:
            return None
        return {"agent_id": umf.agent_id, "value": next(iter(umf.assertion_payload.values()))}


async def run_minimal_multi_agent_test():
    """Test multi-agent scenario with trust gate and conflict resolution."""
    print("=" * 70)
    print("MINIMAL MULTI-AGENT TEST")
    print("=" * 70)
    
    storage = DictStorage()
    trust = TrustManager()
    
    # Initialize trust for different agent types
    trust.record_outcome("trusted_researcher", correct=True)
    trust.record_outcome("trusted_researcher", correct=True)
    trust.record_outcome("trusted_researcher", correct=True)
    
    trust.record_outcome("new_agent", correct=True)
    trust.record_outcome("new_agent", correct=False)
    trust.record_outcome("new_agent", correct=True)
    trust.record_outcome("new_agent", correct=False)
    
    trust.record_outcome("adversarial_agent", correct=False)
    trust.record_outcome("adversarial_agent", correct=False)
    trust.record_outcome("adversarial_agent", correct=False)
    
    pipeline = WritePipeline(
        storage=storage,
        trust_manager=trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.0),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(rate_threshold=1000),
    )
    
    results = {
        "writes": [],
        "conflicts": [],
        "trust_scores": {},
        "final_state": {}
    }
    
    # Record initial trust scores
    for agent_id in ["trusted_researcher", "new_agent", "adversarial_agent"]:
        results["trust_scores"][agent_id] = trust.get_trust(agent_id)
    
    print(f"\nInitial trust scores:")
    for agent_id, score in results["trust_scores"].items():
        print(f"  {agent_id}: {score:.3f}")
    
    # Test 1: Trusted agent writes successfully
    print("\n--- Test 1: Trusted agent write ---")
    result1 = await pipeline.process({
        "agent_id": "trusted_researcher",
        "session_id": "test_session",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.85,
        "assertion_payload": {"research.key_insight": "Quantum computing shows promise for drug discovery"},
    })
    results["writes"].append({
        "agent": "trusted_researcher",
        "status": result1.status,
        "message": result1.message
    })
    print(f"  Status: {result1.status}")
    print(f"  Message: {result1.message.encode('ascii', 'ignore').decode('ascii')}")
    
    # Test 2: New agent with low trust tries to write with high confidence (should be rejected or conflict resolved)
    print("\n--- Test 2: Low-trust agent with high confidence (should be rejected or conflict resolved) ---")
    result2 = await pipeline.process({
        "agent_id": "new_agent",
        "session_id": "test_session",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.95,  # High confidence from low-trust agent
        "assertion_payload": {"research.key_insight": "Quantum computing is ineffective for drug discovery"},
    })
    results["writes"].append({
        "agent": "new_agent",
        "status": result2.status,
        "message": result2.message
    })
    if result2.status in ["conflict_resolved", "conflict_unresolved"]:
        winner = result2.committed.agent_id if result2.committed else "unknown"
        results["conflicts"].append({
            "path": "research.key_insight",
            "winner": winner,
            "loser": "new_agent" if winner != "new_agent" else "trusted_researcher",
            "reason": result2.message
        })
    print(f"  Status: {result2.status}")
    print(f"  Message: {result2.message.encode('ascii', 'ignore').decode('ascii')}")
    
    # Test 3: Adversarial agent with very low trust (should be rejected)
    print("\n--- Test 3: Adversarial agent with very low trust (should be rejected) ---")
    result3 = await pipeline.process({
        "agent_id": "adversarial_agent",
        "session_id": "test_session",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.5,
        "assertion_payload": {"research.key_insight": "Fake claim designed to mislead"},
    })
    results["writes"].append({
        "agent": "adversarial_agent",
        "status": result3.status,
        "message": result3.message
    })
    print(f"  Status: {result3.status}")
    print(f"  Message: {result3.message.encode('ascii', 'ignore').decode('ascii')}")
    
    # Test 4: New agent writes with appropriate confidence (should succeed)
    print("\n--- Test 4: Medium-trust agent with appropriate confidence (should succeed) ---")
    result4 = await pipeline.process({
        "agent_id": "new_agent",
        "session_id": "test_session",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.6,  # Moderate confidence
        "assertion_payload": {"research.main_challenge": "High computational costs remain a barrier"},
    })
    results["writes"].append({
        "agent": "new_agent",
        "status": result4.status,
        "message": result4.message
    })
    print(f"  Status: {result4.status}")
    print(f"  Message: {result4.message.encode('ascii', 'ignore').decode('ascii')}")
    
    # Test 5: Conflict resolution - trusted agent vs new agent
    print("\n--- Test 5: Conflict resolution (trusted vs new agent) ---")
    result5 = await pipeline.process({
        "agent_id": "new_agent",
        "session_id": "test_session",
        "timestamp": datetime.utcnow(),
        "confidence_score": 0.75,
        "assertion_payload": {"research.main_challenge": "Computational costs are manageable"},
    })
    results["writes"].append({
        "agent": "new_agent",
        "status": result5.status,
        "message": result5.message
    })
    if result5.status in ["conflict_resolved", "conflict_unresolved"]:
        winner = result5.committed.agent_id if result5.committed else "unknown"
        loser = "new_agent" if winner != "new_agent" else "trusted_researcher"
        results["conflicts"].append({
            "path": "research.main_challenge",
            "winner": winner,
            "loser": loser,
            "reason": result5.message
        })
    print(f"  Status: {result5.status}")
    print(f"  Message: {result5.message.encode('ascii', 'ignore').decode('ascii')}")
    if result5.status in ["conflict_resolved", "conflict_unresolved"]:
        winner = result5.committed.agent_id if result5.committed else "unknown"
        print(f"  Winner: {winner}")
        if winner != "new_agent":
            print(f"  Loser: new_agent")
        else:
            print(f"  Loser: trusted_researcher")
    
    # Collect final state
    for path in ["research.key_insight", "research.main_challenge"]:
        final = storage.read(path)
        if final:
            results["final_state"][path] = {
                "value": final["value"],
                "agent_id": final["agent_id"]
            }
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    successful_writes = sum(1 for w in results["writes"] if w["status"] == "committed")
    rejected_writes = sum(1 for w in results["writes"] if "rejected" in w["status"])
    conflicts_resolved = len(results["conflicts"])
    
    print(f"Total writes: {len(results['writes'])}")
    print(f"Successful writes: {successful_writes}")
    print(f"Rejected writes: {rejected_writes}")
    print(f"Conflicts resolved: {conflicts_resolved}")
    
    print(f"\nFinal memory state:")
    for path, state in results["final_state"].items():
        value_safe = state['value'].encode('ascii', 'ignore').decode('ascii')
        print(f"  {path}: '{value_safe}' (by {state['agent_id']})")
    
    # Security verification
    print(f"\nSecurity verification:")
    adversarial_rejected = any(w["agent"] == "adversarial_agent" and "rejected" in w["status"] for w in results["writes"])
    high_confidence_rejected = any(w["agent"] == "new_agent" and "rejected" in w["status"] and "confidence" in w["message"].lower() for w in results["writes"])
    
    print(f"  Adversarial agent rejected: {'PASS' if adversarial_rejected else 'FAIL'}")
    print(f"  High-confidence low-trust rejected: {'PASS' if high_confidence_rejected else 'FAIL'}")
    print(f"  Trusted agent writes succeed: {'PASS' if any(w['agent'] == 'trusted_researcher' and w['status'] == 'committed' for w in results['writes']) else 'FAIL'}")
    
    # Save results
    save_path = "experiments/results/minimal_multi_agent_test.json"
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {save_path}")
    
    # Generate visualizations
    print("\nGenerating multi-agent visualizations...")
    try:
        from visualization.multi_agent_plots import generate_multi_agent_dashboard
        plots_dir = "experiments/results/plots"
        plots = generate_multi_agent_dashboard(results, plots_dir)
        print(f"Generated {len(plots)} visualizations in {plots_dir}")
    except Exception as e:
        print(f"  [SKIP] Visualization generation failed: {e}")
    
    return results


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_minimal_multi_agent_test())