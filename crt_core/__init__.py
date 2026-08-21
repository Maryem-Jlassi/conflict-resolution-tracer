"""CRT Core - Deterministic memory coherence middleware."""

from crt_core.schema import UMF, StampedUMF, ProvenanceInfo
from crt_core.provenance import validate_and_stamp, RejectionError
from crt_core.conflict import ConflictResolutionEngine, ConflictResult, ResolutionConfig
from crt_core.locking import AsyncLockManager, StateCode, LockResult, LockConfig
from crt_core.compressor import ContextRetriever
from crt_core.memory_mgmt import MemoryManager
from crt_core.loop_detection import LoopDetector, LoopDetectionResult
from crt_core.confidence_engine import ConfidenceEngine, EvidenceRecord, EvidenceType, ConfidenceWeights
from crt_core.trust_manager import TrustManager, AgentTrustRecord
from crt_core.pipeline import WritePipeline, PipelineResult, MemoryStorage
from crt_core.config import DEFAULT_CONFIG
from crt_core.status import (
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
    # Conflict Resolution (V1 engine — Ψ = (R + C + T) / 3)
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
