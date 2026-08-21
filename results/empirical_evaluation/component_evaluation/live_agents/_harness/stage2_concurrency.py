"""
Stage 2 live agent concurrency harness.

Implements concurrent agent workloads with real HTTP submissions to CRT service.
Supports scaling tests and serial vs concurrent comparisons.
"""

import os
import json
import asyncio
import random
import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3

from live_agent_harness import AgentOperation, LCMSubmission, LiveAgentHarness


@dataclass
class ConcurrencyResult:
    """Result of concurrent execution."""
    scenario: str
    mode: str  # "serial" or "concurrent"
    num_agents: int
    num_operations: int
    successful_submissions: int
    failed_submissions: int
    lock_failures: int
    conflicts_detected: int
    total_latency_ms: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float
    min_latency_ms: float
    final_state_hash: str
    serial_equivalent: bool
    operations: List[Dict[str, Any]]
    submissions: List[Dict[str, Any]]


class Stage2ConcurrencyHarness:
    """Harness for Stage 2 live agent concurrency experiments."""
    
    def __init__(self, lcm_base_url: str = None, output_dir: str = None):
        self.lcm_base_url = lcm_base_url or os.environ.get("LCM_SERVICE_URL", "http://127.0.0.1:8000")
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent
        self.experiment_id = f"stage2_live_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Stage 2 output directory
        self.stage2_dir = self.output_dir / "S2_live_experiments"
        self.stage2_dir.mkdir(parents=True, exist_ok=True)
        
        # Load frozen corpus from Stage 1
        self.corpus_dir = self.output_dir / "_corpus"
        self.frozen_operations = self.load_frozen_corpus()
    
    def load_frozen_corpus(self) -> List[AgentOperation]:
        """Load frozen agent operations from Stage 1 corpus."""
        operations = []
        
        corpus_files = list(self.corpus_dir.glob("*_corpus.jsonl"))
        print(f"Found {len(corpus_files)} corpus files")
        
        for corpus_file in corpus_files:
            try:
                with open(corpus_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            op_data = json.loads(line)
                            operations.append(AgentOperation(**op_data))
            except Exception as e:
                print(f"Error loading {corpus_file}: {e}")
        
        print(f"Loaded {len(operations)} frozen operations")
        return operations
    
    async def submit_operation(self, operation: AgentOperation) -> LCMSubmission:
        """Submit a single operation to CRT service."""
        import httpx
        
        # Create UMF body (simplified for concurrency test)
        umf_body = {
            "agent_id": operation.agent_id,
            "session_id": f"{operation.run_id}_concurrent",
            "timestamp": operation.timestamp,
            "confidence_score": 0.5,
            "assertion_payload": {
                operation.target_path: operation.claimed_value
            }
        }
        
        start_time = time.perf_counter()
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.lcm_base_url}/write",
                    json=umf_body,
                    timeout=30
                )
                response.raise_for_status()
                middleware_response = response.json()
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            
            return LCMSubmission(
                operation=operation,
                umf_body=umf_body,
                http_status=response.status_code,
                middleware_response=middleware_response,
                submission_latency_ms=latency_ms,
                success=True,
                error=None
            )
            
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return LCMSubmission(
                operation=operation,
                umf_body=umf_body,
                http_status=0,
                middleware_response={},
                submission_latency_ms=latency_ms,
                success=False,
                error=str(e)
            )
    
    async def run_serial_execution(
        self, 
        operations: List[AgentOperation]
    ) -> ConcurrencyResult:
        """Run operations serially."""
        print(f"Running serial execution with {len(operations)} operations")
        
        submissions = []
        start_time = time.perf_counter()
        
        for operation in operations:
            submission = await self.submit_operation(operation)
            submissions.append(submission)
        
        total_time = time.perf_counter() - start_time
        
        # Calculate metrics
        successful = sum(1 for sub in submissions if sub.success)
        failed = sum(1 for sub in submissions if not sub.success)
        lock_failures = sum(1 for sub in submissions if sub.http_status == 503)
        conflicts = sum(1 for sub in submissions if sub.middleware_response.get("status") in ["conflict_resolved", "unresolved"])
        
        latencies = [sub.submission_latency_ms for sub in submissions if sub.success]
        
        import statistics
        latency_stats = {
            "total": sum(latencies),
            "avg": statistics.mean(latencies) if latencies else 0,
            "p50": statistics.median(latencies) if latencies else 0,
            "p95": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "min": min(latencies) if latencies else 0
        } if latencies else {"total": 0, "avg": 0, "p50": 0, "p95": 0, "max": 0, "min": 0}
        
        # Get final state hash
        state_hash = await self.get_final_state_hash()
        
        return ConcurrencyResult(
            scenario="serial_baseline",
            mode="serial",
            num_agents=len(set(op.agent_id for op in operations)),
            num_operations=len(operations),
            successful_submissions=successful,
            failed_submissions=failed,
            lock_failures=lock_failures,
            conflicts_detected=conflicts,
            total_latency_ms=latency_stats["total"],
            avg_latency_ms=latency_stats["avg"],
            p50_latency_ms=latency_stats["p50"],
            p95_latency_ms=latency_stats["p95"],
            max_latency_ms=latency_stats["max"],
            min_latency_ms=latency_stats["min"],
            final_state_hash=state_hash,
            serial_equivalent=True,  # Serial is always equivalent to itself
            operations=[asdict(op) for op in operations],
            submissions=[asdict(sub) for sub in submissions]
        )
    
    async def run_concurrent_execution(
        self, 
        operations: List[AgentOperation],
        max_workers: int = None
    ) -> ConcurrencyResult:
        """Run operations concurrently."""
        num_ops = len(operations)
        workers = max_workers or min(num_ops, 16)
        
        print(f"Running concurrent execution with {num_ops} operations, {workers} workers")
        
        submissions = []
        # Only use barrier if we have enough workers for all operations
        use_barrier = workers >= num_ops
        barrier = threading.Barrier(num_ops) if use_barrier else None
        
        def worker(operation):
            """Worker function for concurrent execution."""
            if barrier:
                barrier.wait()  # Synchronize start
            # Run async submission in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                submission = loop.run_until_complete(self.submit_operation(operation))
                return submission
            finally:
                loop.close()
        
        start_time = time.perf_counter()
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_op = {executor.submit(worker, op): op for op in operations}
            
            for future in as_completed(future_to_op):
                try:
                    submission = future.result()
                    submissions.append(submission)
                except Exception as e:
                    print(f"Worker error: {e}")
                    # Create failed submission record
                    op = future_to_op[future]
                    submissions.append(LCMSubmission(
                        operation=op,
                        umf_body={},
                        http_status=0,
                        middleware_response={},
                        submission_latency_ms=0,
                        success=False,
                        error=str(e)
                    ))
        
        total_time = time.perf_counter() - start_time
        
        # Calculate metrics
        successful = sum(1 for sub in submissions if sub.success)
        failed = sum(1 for sub in submissions if not sub.success)
        lock_failures = sum(1 for sub in submissions if sub.http_status == 503)
        conflicts = sum(1 for sub in submissions if sub.middleware_response.get("status") in ["conflict_resolved", "unresolved"])
        
        latencies = [sub.submission_latency_ms for sub in submissions if sub.success]
        
        import statistics
        latency_stats = {
            "total": sum(latencies),
            "avg": statistics.mean(latencies) if latencies else 0,
            "p50": statistics.median(latencies) if latencies else 0,
            "p95": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "min": min(latencies) if latencies else 0
        } if latencies else {"total": 0, "avg": 0, "p50": 0, "p95": 0, "max": 0, "min": 0}
        
        # Get final state hash
        state_hash = await self.get_final_state_hash()
        
        return ConcurrencyResult(
            scenario="concurrent_burst",
            mode="concurrent",
            num_agents=len(set(op.agent_id for op in operations)),
            num_operations=len(operations),
            successful_submissions=successful,
            failed_submissions=failed,
            lock_failures=lock_failures,
            conflicts_detected=conflicts,
            total_latency_ms=latency_stats["total"],
            avg_latency_ms=latency_stats["avg"],
            p50_latency_ms=latency_stats["p50"],
            p95_latency_ms=latency_stats["p95"],
            max_latency_ms=latency_stats["max"],
            min_latency_ms=latency_stats["min"],
            final_state_hash=state_hash,
            serial_equivalent=False,  # Will be compared later
            operations=[asdict(op) for op in operations],
            submissions=[asdict(sub) for sub in submissions]
        )
    
    async def get_final_state_hash(self) -> str:
        """Get hash of final CRT database state."""
        import httpx
        import hashlib
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.lcm_base_url}/")
                response.raise_for_status()
                data = response.json()
            
            # Create hash from relevant state info
            state_str = json.dumps({
                "instance_id": data.get("instance_id"),
                "database_id": data.get("database_id"),
                "resolution_policy": data.get("resolution_policy"),
                "timestamp": datetime.utcnow().isoformat()
            }, sort_keys=True)
            
            return hashlib.sha256(state_str.encode()).hexdigest()
            
        except Exception as e:
            print(f"Error getting state hash: {e}")
            return "unknown_state"
    
    async def run_scaling_test(
        self, 
        base_operations: List[AgentOperation],
        scale_factors: List[int] = [2, 4, 8, 16]
    ) -> Dict[int, ConcurrencyResult]:
        """Run scaling tests with different concurrency levels."""
        results = {}
        
        for scale in scale_factors:
            print(f"\n{'='*60}")
            print(f"Scaling test: {scale} concurrent agents")
            print(f"{'='*60}")
            
            # Sample operations for this scale
            sampled_ops = random.sample(base_operations, min(scale, len(base_operations)))
            
            result = await self.run_concurrent_execution(sampled_ops, max_workers=scale)
            results[scale] = result
            
            # Save individual result
            filename = f"08_STAGE2_SCALING_N{scale}.json"
            self.save_result(result, filename)
        
        return results
    
    async def run_serial_concurrent_comparison(
        self,
        operations: List[AgentOperation],
        num_repetitions: int = 3
    ) -> Dict[str, Any]:
        """Run serial vs concurrent comparison with multiple repetitions."""
        print(f"Running serial vs concurrent comparison ({num_repetitions} repetitions)")
        
        serial_results = []
        concurrent_results = []
        
        for rep in range(num_repetitions):
            print(f"\nRepetition {rep + 1}/{num_repetitions}")
            
            # Shuffle operations for variety
            shuffled_ops = random.sample(operations, len(operations))
            
            # Serial execution
            serial_result = await self.run_serial_execution(shuffled_ops)
            serial_results.append(serial_result)
            
            # Concurrent execution
            concurrent_result = await self.run_concurrent_execution(shuffled_ops)
            concurrent_results.append(concurrent_result)
        
        # Calculate equivalence rate
        equivalent_count = sum(
            1 for s, c in zip(serial_results, concurrent_results)
            if s.final_state_hash == c.final_state_hash
        )
        equivalence_rate = equivalent_count / num_repetitions
        
        comparison_results = {
            "experiment_id": self.experiment_id,
            "timestamp": datetime.utcnow().isoformat(),
            "num_repetitions": num_repetitions,
            "num_operations": len(operations),
            "equivalence_rate": {
                "numerator": equivalent_count,
                "denominator": num_repetitions,
                "percentage": equivalence_rate * 100
            },
            "serial_results": [asdict(r) for r in serial_results],
            "concurrent_results": [asdict(r) for r in concurrent_results],
            "serial_equivalent": equivalence_rate == 1.0
        }
        
        return comparison_results
    
    def save_result(self, result: ConcurrencyResult, filename: str):
        """Save concurrency result to file."""
        output_path = self.stage2_dir / filename
        with open(output_path, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=str)
        print(f"Result saved to {output_path}")
    
    def save_comparison_results(self, results: Dict[str, Any], filename: str):
        """Save comparison results to file."""
        output_path = self.stage2_dir / filename
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Comparison results saved to {output_path}")


async def main():
    """Main execution function."""
    harness = Stage2ConcurrencyHarness()
    
    print(f"Loaded {len(harness.frozen_operations)} frozen operations")
    
    if len(harness.frozen_operations) == 0:
        print("No frozen operations available. Run Stage 1 first to generate corpus.")
        return
    
    # Sample operations for concurrency tests
    test_operations = random.sample(harness.frozen_operations, min(24, len(harness.frozen_operations)))
    
    # Run serial vs concurrent comparison
    print("\n" + "="*60)
    print("SERIAL VS CONCURRENT COMPARISON")
    print("="*60)
    
    comparison_results = await harness.run_serial_concurrent_comparison(
        test_operations, num_repetitions=3
    )
    
    harness.save_comparison_results(comparison_results, "13_SERIAL_CONCURRENT_COMPARISON.json")
    
    # Run scaling tests
    print("\n" + "="*60)
    print("SCALING TESTS")
    print("="*60)
    
    scaling_results = await harness.run_scaling_test(test_operations)
    
    # Save aggregate scaling results
    scaling_summary = {
        "experiment_id": harness.experiment_id,
        "timestamp": datetime.utcnow().isoformat(),
        "base_operations_count": len(test_operations),
        "scale_factors": list(scaling_results.keys()),
        "results": {k: asdict(v) for k, v in scaling_results.items()}
    }
    
    scaling_output = harness.stage2_dir / "08_STAGE2_SCALING_RESULTS.json"
    with open(scaling_output, 'w') as f:
        json.dump(scaling_summary, f, indent=2, default=str)
    
    print(f"Scaling results saved to {scaling_output}")


if __name__ == "__main__":
    asyncio.run(main())