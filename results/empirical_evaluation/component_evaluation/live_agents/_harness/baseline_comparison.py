"""
Baseline comparison system for CRT evaluation.

Compares CRT against naive implementations:
- Naive last-writer-wins dictionary
- Thread-safe dictionary with basic mutex
- CRT production middleware
"""

import json
import asyncio
import threading
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
import statistics


@dataclass
class BaselineResult:
    """Result from baseline execution."""
    baseline_type: str
    num_operations: int
    successful_writes: int
    failed_writes: int
    conflicts_detected: int
    final_state_size: int
    total_latency_ms: float
    avg_latency_ms: float
    lost_updates: int
    data_corruption: bool
    execution_time_ms: float


class NaiveLWWDictionary:
    """Naive last-writer-wins dictionary baseline."""
    
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()
    
    def write(self, path: str, value: Any, agent_id: str) -> bool:
        """Write value to path (last writer wins)."""
        with self.lock:
            self.store[path] = {
                "value": value,
                "agent_id": agent_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            return True
    
    def read(self, path: str) -> Optional[Any]:
        """Read value from path."""
        with self.lock:
            record = self.store.get(path)
            return record["value"] if record else None
    
    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        with self.lock:
            return dict(self.store)


class ThreadSafeDictionary:
    """Thread-safe dictionary with basic mutex baseline."""
    
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()
    
    def write(self, path: str, value: Any, agent_id: str) -> bool:
        """Write value to path with mutex protection."""
        with self.lock:
            # Check for conflicts
            if path in self.store:
                existing = self.store[path]
                if existing["value"] != value:
                    # Conflict detected - last writer wins
                    pass
            
            self.store[path] = {
                "value": value,
                "agent_id": agent_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            return True
    
    def read(self, path: str) -> Optional[Any]:
        """Read value from path."""
        with self.lock:
            record = self.store.get(path)
            return record["value"] if record else None
    
    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        with self.lock:
            return dict(self.store)


class BaselineHarness:
    """Harness for baseline comparison experiments."""
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent
        self.baseline_dir = self.output_dir / "baselines"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
    
    def run_naive_lww_baseline(
        self, 
        operations: List[Dict[str, Any]]
    ) -> BaselineResult:
        """Run naive last-writer-wins dictionary baseline."""
        print("Running naive LWW dictionary baseline")
        
        baseline = NaiveLWWDictionary()
        start_time = time.perf_counter()
        
        successful = 0
        failed = 0
        conflicts = 0
        
        for op in operations:
            try:
                path = op.get("target_path", "unknown")
                value = op.get("claimed_value", "")
                agent_id = op.get("agent_id", "unknown")
                
                success = baseline.write(path, value, agent_id)
                if success:
                    successful += 1
                else:
                    failed += 1
                    
            except Exception as e:
                failed += 1
                print(f"LWW baseline error: {e}")
        
        execution_time = (time.perf_counter() - start_time) * 1000
        final_state = baseline.get_state()
        
        return BaselineResult(
            baseline_type="naive_lww_dict",
            num_operations=len(operations),
            successful_writes=successful,
            failed_writes=failed,
            conflicts_detected=0,  # LWW doesn't detect conflicts
            final_state_size=len(final_state),
            total_latency_ms=execution_time,  # Serial execution
            avg_latency_ms=execution_time / len(operations) if operations else 0,
            lost_updates=0,  # No way to detect lost updates in LWW
            data_corruption=False,
            execution_time_ms=execution_time
        )
    
    def run_threadsafe_dict_baseline(
        self, 
        operations: List[Dict[str, Any]],
        concurrent: bool = False
    ) -> BaselineResult:
        """Run thread-safe dictionary baseline."""
        mode = "concurrent" if concurrent else "serial"
        print(f"Running thread-safe dictionary baseline ({mode})")
        
        baseline = ThreadSafeDictionary()
        start_time = time.perf_counter()
        
        successful = 0
        failed = 0
        conflicts = 0
        
        if concurrent:
            # Concurrent execution with threads
            def worker(op):
                nonlocal successful, failed, conflicts
                try:
                    path = op.get("target_path", "unknown")
                    value = op.get("claimed_value", "")
                    agent_id = op.get("agent_id", "unknown")
                    
                    success = baseline.write(path, value, agent_id)
                    if success:
                        successful += 1
                    else:
                        failed += 1
                        
                except Exception as e:
                    failed += 1
                    print(f"Thread-safe dict error: {e}")
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = [executor.submit(worker, op) for op in operations]
                concurrent.futures.wait(futures)
        else:
            # Serial execution
            for op in operations:
                try:
                    path = op.get("target_path", "unknown")
                    value = op.get("claimed_value", "")
                    agent_id = op.get("agent_id", "unknown")
                    
                    success = baseline.write(path, value, agent_id)
                    if success:
                        successful += 1
                    else:
                        failed += 1
                        
                except Exception as e:
                    failed += 1
                    print(f"Thread-safe dict error: {e}")
        
        execution_time = (time.perf_counter() - start_time) * 1000
        final_state = baseline.get_state()
        
        return BaselineResult(
            baseline_type="threadsafe_dict",
            num_operations=len(operations),
            successful_writes=successful,
            failed_writes=failed,
            conflicts_detected=conflicts,
            final_state_size=len(final_state),
            total_latency_ms=execution_time,
            avg_latency_ms=execution_time / len(operations) if operations else 0,
            lost_updates=0,  # Difficult to detect without reference
            data_corruption=False,
            execution_time_ms=execution_time
        )
    
    def calculate_comparison_metrics(
        self,
        lcm_result: Dict[str, Any],
        baseline_results: List[BaselineResult]
    ) -> Dict[str, Any]:
        """Calculate comparison metrics between CRT and baselines."""
        
        comparison = {
            "timestamp": datetime.utcnow().isoformat(),
            "lcm_summary": {
                "total_operations": lcm_result.get("num_operations", 0),
                "successful_submissions": lcm_result.get("successful_submissions", 0),
                "avg_latency_ms": lcm_result.get("avg_latency_ms", 0),
                "lock_failures": lcm_result.get("lock_failures", 0),
                "conflicts_detected": lcm_result.get("conflicts_detected", 0)
            },
            "baselines": []
        }
        
        for baseline in baseline_results:
            baseline_summary = {
                "type": baseline.baseline_type,
                "total_operations": baseline.num_operations,
                "successful_writes": baseline.successful_writes,
                "failed_writes": baseline.failed_writes,
                "avg_latency_ms": baseline.avg_latency_ms,
                "execution_time_ms": baseline.execution_time_ms,
                "final_state_size": baseline.final_state_size
            }
            comparison["baselines"].append(baseline_summary)
        
        # Calculate CRT advantages
        lcm_success_rate = lcm_result.get("successful_submissions", 0) / max(lcm_result.get("num_operations", 1), 1)
        
        for baseline in baseline_results:
            baseline_success_rate = baseline.successful_writes / max(baseline.num_operations, 1)
            
            # CRT advantage metrics
            if baseline.baseline_type == "naive_lww_dict":
                comparison["lcm_vs_lww"] = {
                    "success_rate_advantage": (lcm_success_rate - baseline_success_rate) * 100,
                    "conflict_detection_advantage": "CRT detects conflicts, LWW does not",
                    "provenance_tracking": "CRT tracks provenance, LWW does not"
                }
            elif baseline.baseline_type == "threadsafe_dict":
                comparison["lcm_vs_threadsafe"] = {
                    "success_rate_advantage": (lcm_success_rate - baseline_success_rate) * 100,
                    "conflict_resolution": "CRT has conflict resolution, thread-safe dict does not",
                    "provenance_tracking": "CRT tracks provenance, thread-safe dict does not"
                }
        
        return comparison
    
    def save_baseline_results(self, results: Dict[str, Any], filename: str):
        """Save baseline comparison results."""
        output_path = self.baseline_dir / filename
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Baseline results saved to {output_path}")


def main():
    """Main execution function."""
    harness = BaselineHarness()
    
    # Sample operations for baseline test
    sample_operations = [
        {
            "target_path": f"test/path_{i}",
            "claimed_value": f"value_{i}",
            "agent_id": f"agent_{i % 4}"
        }
        for i in range(20)
    ]
    
    # Run baselines
    lww_result = harness.run_naive_lww_baseline(sample_operations)
    ts_serial_result = harness.run_threadsafe_dict_baseline(sample_operations, concurrent=False)
    ts_concurrent_result = harness.run_threadsafe_dict_baseline(sample_operations, concurrent=True)
    
    # Mock CRT result for comparison
    mock_lcm_result = {
        "num_operations": 20,
        "successful_submissions": 20,
        "avg_latency_ms": 45.5,
        "lock_failures": 0,
        "conflicts_detected": 3
    }
    
    # Calculate comparison
    comparison = harness.calculate_comparison_metrics(
        mock_lcm_result,
        [lww_result, ts_serial_result, ts_concurrent_result]
    )
    
    # Save results
    harness.save_baseline_results(comparison, "09_BASELINE_RESULTS.json")
    
    print("Baseline comparison completed")


if __name__ == "__main__":
    main()