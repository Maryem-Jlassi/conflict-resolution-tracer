"""
Memory management - staging, committed store, eviction, cold storage.

NOTE: This module is EXPERIMENTAL and RESEARCH-ONLY. It is not on the WritePipeline
hot path and should not be used in production without further validation.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass

from .schema import StampedUMF


@dataclass
class FactMetadata:
    """Metadata for tracking fact usage and lifecycle."""
    umf: StampedUMF
    read_count: int = 0
    last_read_at: Optional[datetime] = None
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class MemoryManager:
    """
    Manages fact lifecycle: staging → committed → cold storage.
    
    Implements:
    - Staging buffer for pre-scoring claims
    - Confidence-attenuated eviction (unread or Ψ < threshold → cold)
    - Lossless aggregation with history preservation
    """
    
    def __init__(
        self,
        eviction_threshold: float = 0.35,
        unread_ttl_seconds: int = 86400 * 7  # 7 days default
    ):
        """
        Initialize memory manager.
        
        Args:
            eviction_threshold: Ψ score below which facts move to cold storage
            unread_ttl_seconds: Seconds unread before eligible for cold storage
        """
        self.eviction_threshold = eviction_threshold
        self.unread_ttl = unread_ttl_seconds
        
        # Staging buffer - new claims before conflict resolution
        self.staging: List[tuple[str, StampedUMF]] = []
        
        # Committed store - hot path, active facts
        self.committed: Dict[str, FactMetadata] = {}
        
        # Cold storage - evicted but preserved facts
        self.cold_storage: Dict[str, List[FactMetadata]] = defaultdict(list)
        
        # History - all versions of each path (lossless)
        self.history: Dict[str, List[StampedUMF]] = defaultdict(list)
    
    def stage_fact(self, path: str, umf: StampedUMF):
        """Add fact to staging buffer before conflict resolution."""
        self.staging.append((path, umf))
    
    def commit_fact(self, path: str, umf: StampedUMF):
        """
        Move fact from staging to committed store.
        
        Also preserves in history for lossless retrieval.
        """
        metadata = FactMetadata(umf=umf)
        self.committed[path] = metadata
        self.history[path].append(umf)
    
    def read_fact(self, path: str) -> Optional[StampedUMF]:
        """
        Read a fact from committed store, updating read tracking.
        
        Returns None if path not in committed store.
        """
        if path not in self.committed:
            return None
        
        metadata = self.committed[path]
        metadata.read_count += 1
        metadata.last_read_at = datetime.utcnow()
        
        return metadata.umf
    
    def evict_to_cold(
        self,
        engine,  # ConflictResolutionEngine
        trust_table: Dict[str, float],
        current_time: datetime = None
    ) -> int:
        """
        Evict facts below threshold or unread for too long to cold storage.
        
        Returns count of facts evicted.
        """
        if current_time is None:
            current_time = datetime.utcnow()
        
        evicted_count = 0
        paths_to_evict = []
        
        for path, metadata in self.committed.items():
            # Calculate current Ψ score
            trust_score = trust_table.get(metadata.umf.agent_id, 0.5)
            psi = engine.calculate_psi(metadata.umf, trust_score, current_time)
            
            # Check eviction criteria
            should_evict = False
            
            # Criterion 1: Ψ below threshold
            if psi < self.eviction_threshold:
                should_evict = True
            
            # Criterion 2: Unread for too long
            if metadata.read_count == 0 and metadata.created_at:
                age = (current_time - metadata.created_at).total_seconds()
                if age > self.unread_ttl:
                    should_evict = True
            
            if should_evict:
                paths_to_evict.append(path)
        
        # Perform eviction
        for path in paths_to_evict:
            metadata = self.committed.pop(path)
            self.cold_storage[path].append(metadata)
            evicted_count += 1
        
        return evicted_count
    
    def aggregate_path_history(self, path: str) -> Dict[str, Any]:
        """
        Aggregate sequential updates on one path into summary.
        
        Returns aggregated summary while preserving raw history.
        This is lossless - raw entries remain in self.history.
        """
        if path not in self.history:
            return None
        
        versions = self.history[path]
        if not versions:
            return None
        
        # Aggregate: take latest value, count versions, track agents
        latest = versions[-1]
        
        agents_involved = list(set(v.agent_id for v in versions))
        
        summary = {
            "path": path,
            "current_value": latest.assertion_payload,
            "version_count": len(versions),
            "agents_involved": agents_involved,
            "first_seen": versions[0].timestamp.isoformat(),
            "last_updated": latest.timestamp.isoformat(),
            "latest_confidence": latest.confidence_score
        }
        
        return summary
    
    def get_full_history(self, path: str) -> List[StampedUMF]:
        """
        Retrieve complete history for a path (lossless).
        
        Proves no data is deleted during aggregation.
        """
        return self.history.get(path, [])
    
    def retrieve_from_cold(self, path: str) -> List[FactMetadata]:
        """Retrieve facts from cold storage."""
        return self.cold_storage.get(path, [])
    
    def clear_staging(self):
        """Clear staging buffer after commit."""
        self.staging.clear()
    
    def get_stats(self) -> Dict[str, int]:
        """Get memory management statistics."""
        return {
            "staging_count": len(self.staging),
            "committed_count": len(self.committed),
            "cold_storage_count": sum(len(v) for v in self.cold_storage.values()),
            "total_history_entries": sum(len(v) for v in self.history.values())
        }
