"""Infinite loop detection for oscillating writes."""

from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class LoopDetectionResult:
    """Result of loop detection check."""
    is_looping: bool
    path: str
    agents_involved: List[str]
    mutation_rate: float  # writes per second
    message: str


class LoopDetector:
    """
    Detects infinite loops via sliding-window mutation rate tracking.
    
    If mutation rate exceeds threshold AND values oscillate,
    freeze the path and return structured interrupt.
    """
    
    def __init__(
        self,
        rate_threshold: float = 10.0,  # writes per second
        window_seconds: int = 5,
        oscillation_threshold: int = 3  # min oscillations to confirm loop
    ):
        """
        Initialize loop detector.
        
        Args:
            rate_threshold: Max mutations per second before flagging
            window_seconds: Time window for rate calculation
            oscillation_threshold: Min back-and-forth changes to confirm loop
        """
        self.rate_threshold = rate_threshold
        self.window_seconds = window_seconds
        self.oscillation_threshold = oscillation_threshold
        
        # Track recent writes per path
        self.write_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )
        
        # Track frozen paths
        self.frozen_paths: Dict[str, LoopDetectionResult] = {}
    
    def record_write(
        self,
        path: str,
        agent_id: str,
        value: any,
        timestamp: datetime = None
    ) -> Optional[LoopDetectionResult]:
        """
        Record a write and check for loop condition.
        
        Args:
            path: The data path being written
            agent_id: Agent performing the write
            value: The value being written
            timestamp: When the write occurred
            
        Returns:
            LoopDetectionResult if loop detected, None otherwise
        """
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        # Check if path already frozen
        if path in self.frozen_paths:
            return self.frozen_paths[path]
        
        # Record this write
        self.write_history[path].append({
            "agent_id": agent_id,
            "value": value,
            "timestamp": timestamp
        })
        
        # Check for loop condition
        return self._check_loop_condition(path, timestamp)
    
    def _check_loop_condition(
        self,
        path: str,
        current_time: datetime
    ) -> Optional[LoopDetectionResult]:
        """Check if current write pattern indicates a loop."""
        history = self.write_history[path]
        
        if len(history) < self.oscillation_threshold * 2:
            # Not enough data
            return None
        
        # Calculate mutation rate in the window
        window_start = current_time - timedelta(seconds=self.window_seconds)
        recent_writes = [
            w for w in history
            if w["timestamp"] >= window_start
        ]
        
        if len(recent_writes) < 2:
            return None
        
        # Calculate rate
        time_span = (current_time - recent_writes[0]["timestamp"]).total_seconds()
        if time_span == 0:
            time_span = 0.001  # Avoid division by zero
        
        mutation_rate = len(recent_writes) / time_span
        
        # Check if rate exceeds threshold
        if mutation_rate < self.rate_threshold:
            return None
        
        # Check for oscillation pattern
        if not self._is_oscillating(recent_writes):
            return None
        
        # Loop detected - freeze path
        agents_involved = list(set(w["agent_id"] for w in recent_writes))
        
        result = LoopDetectionResult(
            is_looping=True,
            path=path,
            agents_involved=agents_involved,
            mutation_rate=mutation_rate,
            message=f"Infinite loop detected on {path}: {len(agents_involved)} agents "
                    f"writing at {mutation_rate:.1f} Hz. Path frozen."
        )
        
        self.frozen_paths[path] = result
        return result
    
    def _is_oscillating(self, writes: List[dict]) -> bool:
        """
        Check if values are oscillating between alternatives.
        
        Oscillation = value changes back and forth repeatedly between
        a small set of alternatives (e.g., A->B->A->B).
        """
        if len(writes) < self.oscillation_threshold * 2:
            return False
        
        # Track unique values and their positions
        values_seen = {}
        for i, write in enumerate(writes):
            val = write["value"]
            if val not in values_seen:
                values_seen[val] = []
            values_seen[val].append(i)
        
        # If there are too many unique values, it's not oscillating
        if len(values_seen) < 2 or len(values_seen) > 3:
            return False
        
        # Check for back-and-forth pattern
        # Count how many times we return to a previously seen value AFTER a change
        revisits = 0
        seen_values = set()
        previous_val = None
        
        for write in writes:
            val = write["value"]
            if previous_val is not None and val != previous_val:
                if val in seen_values:
                    revisits += 1
            
            seen_values.add(val)
            previous_val = val
        
        # If we revisit values frequently, it's oscillating
        return revisits >= self.oscillation_threshold
    
    def is_path_frozen(self, path: str) -> bool:
        """Check if a path is currently frozen due to loop detection."""
        return path in self.frozen_paths
    
    def unfreeze_path(self, path: str):
        """Manually unfreeze a path (for administrative override)."""
        if path in self.frozen_paths:
            del self.frozen_paths[path]
    
    def get_frozen_paths(self) -> List[str]:
        """Get list of all frozen paths."""
        return list(self.frozen_paths.keys())
    
    def clear_history(self, path: str = None):
        """Clear write history for a path or all paths."""
        if path:
            if path in self.write_history:
                self.write_history[path].clear()
        else:
            self.write_history.clear()
