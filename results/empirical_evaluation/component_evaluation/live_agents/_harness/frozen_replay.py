"""
Frozen corpus replay system for deterministic evaluation.

Replays frozen agent operations under different execution modes
to separate agent generation variability from middleware behavior.
"""

import json
import asyncio
import random
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict

from live_agent_harness import AgentOperation, LCMSubmission


@dataclass
class ReplayConfig:
    """Configuration for corpus replay."""
    corpus_file: str
    execution_mode: str  # "serial", "concurrent", "scaling"
    repetitions: int = 3
    random_seed: int = 42
    max_workers: int = 16


class FrozenCorpusReplay:
    """System for replaying frozen agent operations deterministically."""
    
    def __init__(self, corpus_dir: str, output_dir: str = None):
        self.corpus_dir = Path(corpus_dir)
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent
        
        # Create replay output directory
        self.replay_dir = self.output_dir / "_replay"
        self.replay_dir.mkdir(parents=True, exist_ok=True)
    
    def load_corpus(self, corpus_file: str) -> List[AgentOperation]:
        """Load frozen operations from corpus file."""
        corpus_path = self.corpus_dir / corpus_file
        
        if not corpus_path.exists():
            raise FileNotFoundError(f"Corpus file not found: {corpus_path}")
        
        operations = []
        with open(corpus_path, 'r') as f:
            for line in f:
                if line.strip():
                    op_data = json.loads(line)
                    operations.append(AgentOperation(**op_data))
        
        print(f"Loaded {len(operations)} operations from {corpus_file}")
        return operations
    
    def shuffle_operations(self, operations: List[AgentOperation], seed: int = 42) -> List[AgentOperation]:
        """Shuffle operations deterministically for variety."""
        random.seed(seed)
        return random.sample(operations, len(operations))
    
    def sample_operations(self, operations: List[AgentOperation], sample_size: int, seed: int = 42) -> List[AgentOperation]:
        """Sample operations deterministically."""
        random.seed(seed)
        return random.sample(operations, min(sample_size, len(operations)))
    
    def validate_corpus_integrity(self, operations: List[AgentOperation]) -> Dict[str, Any]:
        """Validate corpus integrity and completeness."""
        issues = []
        
        # Check for required fields
        for i, op in enumerate(operations):
            if not op.agent_id:
                issues.append(f"Operation {i}: missing agent_id")
            if not op.target_path:
                issues.append(f"Operation {i}: missing target_path")
            if not op.claimed_value:
                issues.append(f"Operation {i}: missing claimed_value")
            if not op.timestamp:
                issues.append(f"Operation {i}: missing timestamp")
        
        # Check for hash consistency
        operation_hashes = set()
        for i, op in enumerate(operations):
            if op.prompt_hash in operation_hashes:
                issues.append(f"Operation {i}: duplicate prompt hash")
            operation_hashes.add(op.prompt_hash)
        
        return {
            "total_operations": len(operations),
            "issues_found": len(issues),
            "issues": issues,
            "integrity_ok": len(issues) == 0
        }
    
    def create_replay_manifest(self, config: ReplayConfig, operations: List[AgentOperation]) -> Dict[str, Any]:
        """Create manifest for replay execution."""
        return {
            "replay_id": f"replay_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.utcnow().isoformat(),
            "config": asdict(config),
            "corpus_info": {
                "source_file": config.corpus_file,
                "total_operations": len(operations),
                "integrity_check": self.validate_corpus_integrity(operations)
            },
            "operations_manifest": [
                {
                    "index": i,
                    "agent_id": op.agent_id,
                    "provider": op.provider,
                    "model": op.model,
                    "target_path": op.target_path,
                    "timestamp": op.timestamp,
                    "prompt_hash": op.prompt_hash
                }
                for i, op in enumerate(operations)
            ]
        }
    
    def save_replay_manifest(self, manifest: Dict[str, Any], filename: str):
        """Save replay manifest to file."""
        output_path = self.replay_dir / filename
        with open(output_path, 'w') as f:
            json.dump(manifest, f, indent=2, default=str)
        print(f"Replay manifest saved to {output_path}")
    
    def load_replay_manifest(self, filename: str) -> Dict[str, Any]:
        """Load replay manifest from file."""
        manifest_path = self.replay_dir / filename
        with open(manifest_path, 'r') as f:
            return json.load(f)
    
    def execute_replay(
        self, 
        manifest: Dict[str, Any],
        lcm_harness
    ) -> Dict[str, Any]:
        """Execute replay according to manifest configuration."""
        config = ReplayConfig(**manifest["config"])
        
        # Load operations from corpus
        operations = self.load_corpus(config.corpus_file)
        
        results = {
            "replay_id": manifest["replay_id"],
            "execution_mode": config.execution_mode,
            "timestamp": datetime.utcnow().isoformat(),
            "config": asdict(config),
            "repetitions": []
        }
        
        for rep in range(config.repetitions):
            print(f"Replay repetition {rep + 1}/{config.repetitions}")
            
            # Shuffle operations for this repetition
            if config.execution_mode != "serial":
                ops = self.shuffle_operations(operations, config.random_seed + rep)
            else:
                ops = operations  # Keep order for serial
            
            # Execute according to mode
            if config.execution_mode == "serial":
                rep_result = await lcm_harness.run_serial_execution(ops)
            elif config.execution_mode == "concurrent":
                rep_result = await lcm_harness.run_concurrent_execution(ops, config.max_workers)
            elif config.execution_mode == "scaling":
                scale_factors = [2, 4, 8, 16]
                rep_result = await lcm_harness.run_scaling_test(ops, scale_factors)
            else:
                raise ValueError(f"Unknown execution mode: {config.execution_mode}")
            
            results["repetitions"].append(asdict(rep_result))
        
        # Calculate determinism metrics
        results["determinism_metrics"] = self.calculate_determinism_metrics(results["repetitions"])
        
        return results
    
    def calculate_determinism_metrics(self, repetitions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate determinism metrics across repetitions."""
        if not repetitions:
            return {"error": "No repetitions to analyze"}
        
        # Extract final state hashes
        state_hashes = [rep.get("final_state_hash") for rep in repetitions]
        
        # Check if all hashes are identical
        all_identical = len(set(state_hashes)) == 1
        
        # Count unique states
        unique_states = len(set(state_hashes))
        
        # Calculate pairwise equivalence
        equivalent_pairs = 0
        total_pairs = 0
        
        for i in range(len(repetitions)):
            for j in range(i + 1, len(repetitions)):
                total_pairs += 1
                if state_hashes[i] == state_hashes[j]:
                    equivalent_pairs += 1
        
        equivalence_rate = equivalent_pairs / total_pairs if total_pairs > 0 else 0
        
        return {
            "total_repetitions": len(repetitions),
            "identical_states": all_identical,
            "unique_states": unique_states,
            "equivalent_pairs": equivalent_pairs,
            "total_pairs": total_pairs,
            "equivalence_rate": {
                "numerator": equivalent_pairs,
                "denominator": total_pairs,
                "percentage": equivalence_rate * 100
            },
            "state_hashes": state_hashes
        }
    
    def save_replay_results(self, results: Dict[str, Any], filename: str):
        """Save replay results to file."""
        output_path = self.replay_dir / filename
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Replay results saved to {output_path}")


async def main():
    """Main execution function."""
    replay_system = FrozenCorpusReplay(
        corpus_dir="C:/Users/asus/Downloads/living-context-memory-FRESH/results/empirical_evaluation/component_evaluation/live_agents/_corpus",
        output_dir="C:/Users/asus/Downloads/living-context-memory-FRESH/results/empirical_evaluation/component_evaluation/live_agents"
    )
    
    # List available corpus files
    corpus_files = list(replay_system.corpus_dir.glob("*_corpus.jsonl"))
    print(f"Available corpus files: {[f.name for f in corpus_files]}")
    
    if not corpus_files:
        print("No corpus files found. Run Stage 1 first.")
        return
    
    # Use first available corpus for demo
    corpus_file = corpus_files[0].name
    print(f"Using corpus file: {corpus_file}")
    
    # Create replay configuration
    config = ReplayConfig(
        corpus_file=corpus_file,
        execution_mode="concurrent",
        repetitions=3,
        random_seed=42,
        max_workers=8
    )
    
    # Load and validate corpus
    operations = replay_system.load_corpus(corpus_file)
    integrity_check = replay_system.validate_corpus_integrity(operations)
    
    print(f"Corpus integrity: {integrity_check}")
    
    # Create manifest
    manifest = replay_system.create_replay_manifest(config, operations)
    replay_system.save_replay_manifest(manifest, "replay_manifest.json")
    
    print("Replay system ready. Use with stage2_concurrency harness for execution.")


if __name__ == "__main__":
    asyncio.run(main())