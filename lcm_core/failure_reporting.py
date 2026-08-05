"""
Failure Mode Reporting System

Provides detailed failure analysis and reporting for LCM benchmarks and experiments.
Instead of just reporting success rates, this system categorizes and reports
different types of failures for better understanding of system behavior.

Failure categories:
- tool_call_failure: Agent failed to call required tools
- verification_failure: Evidence verification failed
- conflict_unresolved: Conflict could not be resolved
- lock_failure: Could not acquire write lock
- loop_frozen: Write loop detected and frozen
- evidence_rejected: Evidence failed validation
- pipeline_error: General pipeline error
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class FailureType(Enum):
    """Categories of failures in the LCM system."""
    TOOL_CALL_FAILURE = "tool_call_failure"
    VERIFICATION_FAILURE = "verification_failure"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    LOCK_FAILURE = "lock_failure"
    LOOP_FROZEN = "loop_frozen"
    EVIDENCE_REJECTED = "evidence_rejected"
    TRUST_REJECTED = "trust_rejected"
    PIPELINE_ERROR = "pipeline_error"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


@dataclass
class FailureRecord:
    """A single failure record with detailed context."""
    failure_type: FailureType
    timestamp: datetime
    agent_id: Optional[str] = None
    path: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    recoverable: bool = False
    retry_count: int = 0


class FailureReporter:
    """
    Collects and analyzes failure modes across LCM operations.
    
    Provides:
    - Failure categorization by type
    - Failure rate calculation by category
    - Detailed failure analysis
    - Trend analysis over time
    """
    
    def __init__(self):
        self.failures: List[FailureRecord] = []
        self.total_operations: int = 0
    
    def record_failure(
        self,
        failure_type: FailureType,
        message: str = "",
        agent_id: Optional[str] = None,
        path: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = False,
    ) -> None:
        """Record a failure."""
        self.failures.append(FailureRecord(
            failure_type=failure_type,
            timestamp=datetime.utcnow(),
            agent_id=agent_id,
            path=path,
            message=message,
            details=details or {},
            recoverable=recoverable,
        ))
    
    def record_operation(self) -> None:
        """Record that an operation was attempted (for rate calculation)."""
        self.total_operations += 1
    
    def get_failure_rate(self, failure_type: Optional[FailureType] = None) -> float:
        """
        Get failure rate for a specific type or overall.
        
        Args:
            failure_type: Specific failure type, or None for overall rate
            
        Returns:
            Failure rate as a fraction (0.0 to 1.0)
        """
        if self.total_operations == 0:
            return 0.0
        
        if failure_type is None:
            return len(self.failures) / self.total_operations
        
        type_failures = [f for f in self.failures if f.failure_type == failure_type]
        return len(type_failures) / self.total_operations
    
    def get_failure_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive failure summary.
        
        Returns:
            Summary with breakdown by failure type
        """
        # Count failures by type
        failure_counts: Dict[FailureType, int] = {}
        for failure in self.failures:
            failure_counts[failure.failure_type] = failure_counts.get(failure.failure_type, 0) + 1
        
        # Calculate rates by type
        failure_rates: Dict[str, float] = {}
        for failure_type, count in failure_counts.items():
            failure_rates[failure_type.value] = count / self.total_operations if self.total_operations > 0 else 0.0
        
        # Calculate recoverable vs unrecoverable
        recoverable_count = sum(1 for f in self.failures if f.recoverable)
        unrecoverable_count = len(self.failures) - recoverable_count
        
        # Agent-specific failure rates
        agent_failures: Dict[str, int] = {}
        for failure in self.failures:
            if failure.agent_id:
                agent_failures[failure.agent_id] = agent_failures.get(failure.agent_id, 0) + 1
        
        return {
            "total_operations": self.total_operations,
            "total_failures": len(self.failures),
            "overall_failure_rate": self.get_failure_rate(),
            "failure_counts": {ft.value: count for ft, count in failure_counts.items()},
            "failure_rates": failure_rates,
            "recoverable_failures": recoverable_count,
            "unrecoverable_failures": unrecoverable_count,
            "recoverable_rate": recoverable_count / len(self.failures) if self.failures else 0.0,
            "agent_failures": agent_failures,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def get_failure_details(self, failure_type: Optional[FailureType] = None) -> List[Dict[str, Any]]:
        """
        Get detailed failure records.
        
        Args:
            failure_type: Filter by specific type, or None for all
            
        Returns:
            List of failure details
        """
        failures = self.failures if failure_type is None else [f for f in self.failures if f.failure_type == failure_type]
        
        return [
            {
                "failure_type": f.failure_type.value,
                "timestamp": f.timestamp.isoformat(),
                "agent_id": f.agent_id,
                "path": f.path,
                "message": f.message,
                "details": f.details,
                "recoverable": f.recoverable,
                "retry_count": f.retry_count,
            }
            for f in failures
        ]
    
    def get_agent_failure_rate(self, agent_id: str) -> float:
        """
        Get failure rate for a specific agent.
        
        Args:
            agent_id: Agent ID to analyze
            
        Returns:
            Failure rate for this agent
        """
        agent_operations = sum(1 for f in self.failures if f.agent_id == agent_id)
        agent_failures = sum(1 for f in self.failures if f.agent_id == agent_id)
        
        # This is approximate since we don't track total operations per agent
        # In practice, this would need to be tracked separately
        return agent_failures / len(self.failures) if self.failures else 0.0
    
    def reset(self) -> None:
        """Reset all failure records."""
        self.failures = []
        self.total_operations = 0
    
    def merge(self, other: 'FailureReporter') -> None:
        """Merge failure records from another reporter."""
        self.failures.extend(other.failures)
        self.total_operations += other.total_operations


# Global failure reporter instance
_global_reporter: Optional[FailureReporter] = None


def get_failure_reporter() -> FailureReporter:
    """Get the global failure reporter."""
    global _global_reporter
    if _global_reporter is None:
        _global_reporter = FailureReporter()
    return _global_reporter


def record_failure(
    failure_type: FailureType,
    message: str = "",
    agent_id: Optional[str] = None,
    path: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    recoverable: bool = False,
) -> None:
    """Record a failure using the global reporter."""
    reporter = get_failure_reporter()
    reporter.record_failure(
        failure_type=failure_type,
        message=message,
        agent_id=agent_id,
        path=path,
        details=details,
        recoverable=recoverable,
    )


def record_operation() -> None:
    """Record an operation using the global reporter."""
    reporter = get_failure_reporter()
    reporter.record_operation()


def get_failure_summary() -> Dict[str, Any]:
    """Get failure summary using the global reporter."""
    reporter = get_failure_reporter()
    return reporter.get_failure_summary()
