"""LCM Core - Deterministic memory coherence middleware."""

from lcm_core.schema import UMF, StampedUMF, ProvenanceInfo
from lcm_core.provenance import validate_and_stamp, RejectionError
from lcm_core.conflict import ConflictResolutionEngine, ConflictResult, ResolutionConfig
from lcm_core.locking import AsyncLockManager, StateCode, LockResult, LockConfig
from lcm_core.compressor import ContextRetriever
from lcm_core.memory_mgmt import MemoryManager
from lcm_core.loop_detection import LoopDetector, LoopDetectionResult
from lcm_core.confidence_engine import ConfidenceEngine, EvidenceRecord, EvidenceType, ConfidenceWeights
from lcm_core.trust_manager import TrustManager, AgentTrustRecord
from lcm_core.pipeline import WritePipeline, PipelineResult, MemoryStorage
from lcm_core.config import DEFAULT_CONFIG
from lcm_core.status import (
    STATUS_COMMITTED,
    STATUS_REJECTED,
    STATUS_REJECTED_UNTRUSTED,
    STATUS_REJECTED_SUSPICIOUS,
    STATUS_REJECTED_NO_EVIDENCE,
    STATUS_EVIDENCE_REJECTED,
    STATUS_CONFLICT_RESOLVED,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    STATUS_LOOP_FROZEN,
    STATUS_LOCK_FAILED,
    COMMIT_STATUSES,
    CONFLICT_STATUSES,
    REJECTION_STATUSES,
    ALL_PIPELINE_STATUSES,
)

__version__ = "0.2.1"

__all__ = [
    # Schema
    "UMF",
    "StampedUMF",
    "ProvenanceInfo",
    # Provenance
    "validate_and_stamp",
    "RejectionError",
    # Conflict Resolution
    "ConflictResolutionEngine",
    "ConflictResult",
    "ResolutionConfig",
    # Concurrency
    "AsyncLockManager",
    "StateCode",
    "LockResult",
    "LockConfig",
    # Retrieval
    "ContextRetriever",
    # Memory Management
    "MemoryManager",
    # Loop Detection
    "LoopDetector",
    "LoopDetectionResult",
    # Confidence Engine
    "ConfidenceEngine",
    "EvidenceRecord",
    "EvidenceType",
    "ConfidenceWeights",
    # Trust Manager
    "TrustManager",
    "AgentTrustRecord",
    # Pipeline (orchestrator)
    "WritePipeline",
    "PipelineResult",
    "MemoryStorage",
    # Shared configuration
    "DEFAULT_CONFIG",
    # Shared status constants
    "STATUS_COMMITTED",
    "STATUS_REJECTED",
    "STATUS_REJECTED_UNTRUSTED",
    "STATUS_REJECTED_SUSPICIOUS",
    "STATUS_REJECTED_NO_EVIDENCE",
    "STATUS_EVIDENCE_REJECTED",
    "STATUS_CONFLICT_RESOLVED",
    "STATUS_RESOLVED",
    "STATUS_UNRESOLVED",
    "STATUS_LOOP_FROZEN",
    "STATUS_LOCK_FAILED",
    "COMMIT_STATUSES",
    "CONFLICT_STATUSES",
    "REJECTION_STATUSES",
    "ALL_PIPELINE_STATUSES",
]
