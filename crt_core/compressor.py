"""
Context compression and retrieval pipeline.

NOTE: This module is EXPERIMENTAL and RESEARCH-ONLY. It is not on the WritePipeline
hot path and should not be used in production without further validation.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from collections import defaultdict

from .conflict import ConflictResolutionEngine


class ContextRetriever:
    """
    Retrieval pipeline with path-targeted filtering and U-shaped placement.
    
    NOT semantic/embedding search - this is exact path-prefix lookup.
    """
    
    def __init__(self, storage: Dict[str, StampedUMF] = None):
        """
        Initialize retriever.
        
        Args:
            storage: In-memory dict mapping path -> StampedUMF (for Phases 1-7)
                     Will be replaced with SQLite in Phase 8
        """
        self.storage = storage if storage is not None else {}
        self.engine = ConflictResolutionEngine()
    
    def get_context(
        self,
        path_prefix: str,
        trust_table: Dict[str, float] = None,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Retrieve facts matching path prefix with U-shaped placement.
        
        Path-targeted filtering: "patient.vitals.*" matches all vitals paths.
        Deduplication: only the highest-Ψ version per exact path returned.
        U-shaped placement: highest-priority facts appear first AND last.
        
        Args:
            path_prefix: Path prefix to filter on (e.g., "patient.vitals")
            trust_table: Trust scores for Ψ calculation (defaults to 0.5 for all)
            max_results: Maximum facts to return
            
        Returns:
            List of fact dicts in U-shaped order
        """
        if trust_table is None:
            trust_table = defaultdict(lambda: 0.5)
        
        # Step 1: Filter by path prefix
        matching_facts = []
        for path, umf in self.storage.items():
            if self._path_matches(path, path_prefix):
                matching_facts.append((path, umf))
        
        # Step 2: Deduplicate - keep only highest-Ψ per exact path
        # (In this phase we have only one version per path, but this prepares for Phase 5)
        deduplicated = {}
        for path, umf in matching_facts:
            trust_score = trust_table.get(umf.agent_id, 0.5)
            psi = self.engine.calculate_psi(umf, trust_score)
            
            if path not in deduplicated or psi > deduplicated[path][1]:
                deduplicated[path] = (umf, psi)
        
        # Step 3: Sort by Ψ (highest first)
        sorted_facts = sorted(
            deduplicated.items(),
            key=lambda x: x[1][1],  # Sort by psi score
            reverse=True
        )[:max_results]
        
        # Step 4: U-shaped placement
        # Highest priority facts: first AND last
        # Lower priority facts: middle
        ordered_facts = self._apply_u_shaped_placement(sorted_facts)
        
        # Convert to dict format for return
        return [
            {
                "path": path,
                "value": umf.assertion_payload,
                "agent_id": umf.agent_id,
                "session_id": umf.session_id,
                "timestamp": umf.timestamp.isoformat(),
                "confidence_score": umf.confidence_score,
                "psi_score": psi
            }
            for path, (umf, psi) in ordered_facts
        ]
    
    def _path_matches(self, full_path: str, prefix: str) -> bool:
        """
        Check if full_path matches the prefix pattern.
        
        Examples:
            - "patient.vitals.temperature_c" matches "patient.vitals"
            - "patient.vitals.temperature_c" matches "patient"
            - "patient.vitals.temperature_c" does NOT match "patient.diagnosis"
        """
        # Handle wildcard
        if prefix.endswith("*"):
            prefix = prefix[:-1]
        
        # Exact match or prefix match
        return full_path == prefix or full_path.startswith(prefix + ".")
    
    def _apply_u_shaped_placement(
        self,
        sorted_facts: List[tuple[str, tuple[StampedUMF, float]]]
    ) -> List[tuple[str, tuple[StampedUMF, float]]]:
        """
        Apply U-shaped placement: high-priority facts at start AND end.
        
        For N facts sorted by priority [F1, F2, F3, F4, F5] where F1 is highest:
        - If N <= 3: return as-is (too short for U-shape)
        - If N > 3: [F1, F2, middle facts..., F2, F1]
        
        This reinforces the most important context at both ends.
        """
        n = len(sorted_facts)
        
        if n <= 3:
            # Too short for meaningful U-shape
            return sorted_facts
        
        # Determine how many high-priority facts to reinforce
        # Rule: top 20% or at least 1, max 3
        num_priority = max(1, min(3, n // 5))
        
        priority_facts = sorted_facts[:num_priority]
        middle_facts = sorted_facts[num_priority:]
        
        # U-shape: priority, middle, priority reversed
        return priority_facts + middle_facts + list(reversed(priority_facts))
    
    def store_fact(self, path: str, umf: StampedUMF):
        """
        Store a fact in the retriever's storage.
        
        For testing purposes in Phases 1-7.
        Phase 8+ will use SQLite directly.
        """
        self.storage[path] = umf
    
    def clear_storage(self):
        """Clear all stored facts (for testing)."""
        self.storage.clear()
