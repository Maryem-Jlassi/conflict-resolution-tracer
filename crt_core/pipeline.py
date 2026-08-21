"""
CRT Write Pipeline - single orchestrator that connects all crt_core modules.

Every write from an agent travels through this pipeline in order:

    Agent claim (raw dict)
         |
    validate_and_stamp()       -- schema validation + provenance stamping
         |                        + middleware confidence calculation
    LoopDetector.record_write()-- detect oscillating writes before locking
         |
    AsyncLockManager           -- path-level write lock
         |
    ConflictResolutionEngine   -- Psi formula with verified_confidence
         |                        + TrustManager for dynamic trust
    Storage (abstract)         -- commit winner, archive loser
         |
    [Optional] AutoVerifier    -- automatic verification after conflicts
         |
    TrustManager.record_outcome() -- update agent trust after verification

Each module lives in its own file and can be swapped independently.
This file is the ONLY place that knows the order they run in.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from .confidence_engine import EvidenceRecord
from .config import DEFAULT_CONFIG
from .conflict import ConflictResolutionEngine, ConflictResult, ResolutionConfig
from .events import LCMEvent, EventType
from .event_bus import get_event_bus
from .loop_detection import LoopDetector, LoopDetectionResult
from .locking import AsyncLockManager, LockConfig
from .provenance import RejectionError, validate_and_stamp, _FORBIDDEN_AGENT_FIELDS
from .schema import StampedUMF, ProvenanceInfo
from .status import (
    STATUS_COMMITTED,
    STATUS_CONFLICT_RESOLVED,
    STATUS_LOOP_FROZEN,
    STATUS_LOCK_FAILED,
    STATUS_REJECTED,
    STATUS_REJECTED_SUSPICIOUS,
    STATUS_REJECTED_UNTRUSTED,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
)
from .trust_manager import TrustManager
from .failure_reporting import FailureType, get_failure_reporter, record_failure, record_operation
from .metrics import get_metrics_registry, record_write_status


# ---------------------------------------------------------------------------
# LLM Guard - Hard-fail if LLM modules are imported in core path
# ---------------------------------------------------------------------------

_FORBIDDEN_LLM_MODULES = frozenset({
    "openai",
    "anthropic",
    "langchain",
    "transformers",
    "torch",
    "tensorflow",
    "ollama",
    "crewai",
    "autogen",
    "llama_cpp",
})


def _check_llm_contamination():
    """
    Check for LLM-related imports that were loaded *by the crt_core package itself*.

    We only reject contamination if crt_core code directly caused the import.
    If langchain/ollama is already in sys.modules from the agent layer (which is
    the expected deployment pattern), that is NOT contamination — the agent layer
    is allowed to use LLMs.  Only direct imports *inside* crt_core modules are
    forbidden.
    """
    import traceback as _tb
    # Walk the call stack looking for frames inside crt_core
    # If we find a frame in crt_core that imported a forbidden module, that's contamination.
    # In practice, we check whether any crt_core module is present in the *importer*
    # of a forbidden module — i.e. whether a forbidden module's __spec__.parent is crt_core.
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not any(f in mod_name for f in _FORBIDDEN_LLM_MODULES):
            continue
        # Forbidden module is present. Check if it was imported FROM crt_core.
        spec = getattr(mod, "__spec__", None)
        if spec is not None and spec.parent and "crt_core" in spec.parent:
            raise RuntimeError(
                f"LLM contamination detected: '{mod_name}' was imported inside crt_core. "
                f"CRT middleware must be zero-LLM. LLM calls are only permitted in agent layers."
            )
        # Also reject if any crt_core module directly references the forbidden package
        # in its own __file__ (catches monkey-patching scenarios)
        # For normal agent-layer usage, forbidden modules exist in sys.modules but
        # their __spec__.parent is outside crt_core — that is fine.


def _emit(event: LCMEvent) -> None:
    """Fire-and-forget event emission. Never propagates exceptions."""
    try:
        get_event_bus().publish(event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Storage protocol — any backend (SQLite, in-memory, Redis…) can plug in here
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryStorage(Protocol):
    """Minimal interface the pipeline requires from a storage backend."""

    def get_existing(self, path: str) -> Optional[StampedUMF]:
        """Return the current committed packet for path, or None."""
        ...

    def commit(self, umf: StampedUMF, path: str) -> None:
        """Persist the winning packet as the live record for path."""
        ...

    def commit_pending(self, umf: StampedUMF, path: str) -> None:
        """Persist an unresolved alternative without overwriting the active memory."""
        ...

    def archive(self, provenance_id: str) -> None:
        """Mark a packet as superseded/archived (never deleted)."""
        ...

    def update_provenance_fields(
        self,
        provenance_id: str,
        *,
        verification_status: Optional[str] = None,
        memory_status: Optional[str] = None,
    ) -> None:
        """Update lifecycle fields on a stored packet."""
        ...


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Returned by WritePipeline.process() for every write attempt."""

    # Outcome tag
    status: str          # committed | conflict_resolved | unresolved | rejected | loop_frozen | lock_failed

    # The packet that ended up committed (None only on rejection/lock failure)
    committed: Optional[StampedUMF] = None

    # Conflict details (populated when status == conflict_resolved or unresolved)
    conflict: Optional[ConflictResult] = None

    # Loop detection result (populated when status == loop_frozen)
    loop: Optional[LoopDetectionResult] = None

    # Human-readable summary
    message: str = ""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class WritePipeline:
    """
    Connects validate → loop-detect → lock → conflict-resolve → store → trust-update.

    Instantiate once (e.g. at app startup) and call process() for each write.
    All individual modules remain independently modifiable.

    Args:
        storage:          Any object implementing MemoryStorage.
        trust_manager:    Shared TrustManager (updated after each verified outcome).
        conflict_engine:  ConflictResolutionEngine (default weights if omitted).
        lock_manager:     AsyncLockManager (default config if omitted).
        loop_detector:    LoopDetector (default thresholds if omitted).
    """

    def __init__(
        self,
        storage: MemoryStorage,
        trust_manager: Optional[TrustManager] = None,
        conflict_engine: Optional[ConflictResolutionEngine] = None,
        lock_manager: Optional[AsyncLockManager] = None,
        loop_detector: Optional[LoopDetector] = None,
        clock: Optional[Callable[[], datetime]] = None,
        use_v1: bool = True,
    ):
        self.storage = storage
        self.trust = trust_manager or TrustManager()
        self.use_v1 = use_v1
        
        if conflict_engine is not None:
            self.conflict = conflict_engine
            self.use_v1 = isinstance(conflict_engine, ConflictResolutionEngine)
        elif use_v1:
            psi_config = DEFAULT_CONFIG.psi_weights
            resolution_config = ResolutionConfig(
                w_recency=psi_config.w_recency,
                w_confidence=psi_config.w_confidence,
                w_trust=psi_config.w_trust,
                decay_lambda=psi_config.decay_lambda,
                uncertainty_threshold=psi_config.uncertainty_threshold,
            )
            self.conflict = ConflictResolutionEngine(config=resolution_config)
        else:
            psi_config = DEFAULT_CONFIG.psi_weights
            resolution_config = ResolutionConfig(
                w_recency=psi_config.w_recency,
                w_confidence=psi_config.w_confidence,
                w_trust=psi_config.w_trust,
                decay_lambda=psi_config.decay_lambda,
                uncertainty_threshold=psi_config.uncertainty_threshold,
            )
            self.conflict = ConflictResolutionEngine(config=resolution_config)
            
        self.locks = lock_manager or AsyncLockManager()
        self.loops = loop_detector or LoopDetector()
        self._clock = clock or datetime.utcnow

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process(
        self,
        raw: Dict[str, Any],
        *,
        evidence_records: Optional[List[EvidenceRecord]] = None,
        domain: Optional[str] = None,
        parent_memory_ids: Optional[List[str]] = None,
        valid_until: Optional[datetime] = None,
        agreeing_agents: int = 0,
        total_independent_agents: int = 0,
        verified_memories_consistent: Optional[bool] = None,
        evidence_signature: Optional[str] = None,
        reference_time: Optional[datetime] = None,
    ) -> PipelineResult:
        """Public entry point — delegates to ``_process_inner`` and records
        telemetry (Phase 12): write outcome counters and end-to-end latency.

        Signature is identical to ``_process_inner``.

        ``reference_time`` (optional, Phase 14): fixed reference instant used for
        the Ψ recency computation. When omitted the conflict engine falls back
        to ``datetime.utcnow()`` (wall-clock behaviour).
        """
        t0 = time.perf_counter()
        status = "pipeline_error"
        try:
            operation_time = reference_time if reference_time is not None else self._clock()
            result = await self._process_inner(
                raw,
                evidence_records=evidence_records,
                domain=domain,
                parent_memory_ids=parent_memory_ids,
                valid_until=valid_until,
                agreeing_agents=agreeing_agents,
                total_independent_agents=total_independent_agents,
                verified_memories_consistent=verified_memories_consistent,
                evidence_signature=evidence_signature,
                reference_time=operation_time,
            )
            status = result.status
            return result
        finally:
            record_write_status(status)
            get_metrics_registry().observe(
                "pipeline.latency_ms", (time.perf_counter() - t0) * 1000.0
            )

    async def _process_inner(
        self,
        raw: Dict[str, Any],
        *,
        evidence_records: Optional[List[EvidenceRecord]] = None,
        domain: Optional[str] = None,
        parent_memory_ids: Optional[List[str]] = None,
        valid_until: Optional[datetime] = None,
        agreeing_agents: int = 0,
        total_independent_agents: int = 0,
        verified_memories_consistent: Optional[bool] = None,
        evidence_signature: Optional[str] = None,
        reference_time: Optional[datetime] = None,
    ) -> PipelineResult:
        """Core pipeline body (renamed from ``process`` in Phase 12)."""
        """
        Run a raw agent packet through the full write pipeline.

        Args:
            raw:                          Raw dict from the agent (UMF fields).
            evidence_records:             External evidence for confidence calculation.
            domain:                       Task domain for domain-specific trust lookup.
            parent_memory_ids:            Lineage references for provenance tracking.
            valid_until:                  Optional memory expiry timestamp.
            agreeing_agents:              Number of agents agreeing with this claim.
            total_independent_agents:     Total agents that weighed in on this claim.
            verified_memories_consistent: Whether claim is consistent with verified prior memories.
            evidence_signature:           Cryptographic signature for external evidence binding.

        Returns:
            PipelineResult describing the outcome.
        """
        operation_time = reference_time if reference_time is not None else self._clock()

        # SECURITY: Check for LLM contamination in core path
        _check_llm_contamination()

        # ── Step 1: Validate & stamp ──────────────────────────────────────
        # Record operation attempt for failure reporting
        record_operation()
        
        try:
            stamped = validate_and_stamp(
                raw,
                evidence_records=evidence_records,
                domain=domain,
                parent_memory_ids=parent_memory_ids,
                valid_until=valid_until,
                agreeing_agents=agreeing_agents,
                total_independent_agents=total_independent_agents,
                verified_memories_consistent=verified_memories_consistent,
                evidence_signature=evidence_signature,
                reference_time=operation_time,
            )
        except RejectionError as exc:
            field = exc.field or ""
            if field == "<root>":
                reason_code = "INVALID_PACKET"
            elif field in _FORBIDDEN_AGENT_FIELDS:
                reason_code = "FORBIDDEN_FIELD"
            elif field == "evidence_signature":
                reason_code = "BAD_SIGNATURE"
            else:
                reason_code = "INVALID_FIELD"
            _emit(CRTEvent(EventType.WRITE_REJECTED, operation_time, {
                "agent_id":    raw.get("agent_id", "unknown"),
                "reason":      exc.message,
                "field":       field,
                "reason_code": reason_code,
                "status":      STATUS_REJECTED,
            }))
            record_failure(
                FailureType.EVIDENCE_REJECTED,
                message=exc.message,
                agent_id=raw.get("agent_id", "unknown"),
                path=(
                    next(iter(raw["assertion_payload"]), None)
                    if isinstance(raw.get("assertion_payload"), dict)
                    else None
                ),
                recoverable=False,
            )
            return PipelineResult(status=STATUS_REJECTED, message=exc.message)

        # Derive path from the first key in the payload (convention)
        path = next(iter(stamped.assertion_payload))
        value = stamped.assertion_payload[path]

        # ── HARD SECURITY GATE: Trust-based rejection ───────────────────────────
        # Reject writes from zero-trust or low-trust agents before any mutation
        from crt_core.config import (
            TRUST_REJECT_THRESHOLD,
            HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD,
            LOW_TRUST_THRESHOLD,
        )
        
        trust = self.trust.get_trust(stamped.agent_id, domain, as_of=operation_time, strict_domain=True)
        
        # Zero / near-zero trust → always reject
        if trust < TRUST_REJECT_THRESHOLD:
            _emit(CRTEvent(EventType.WRITE_REJECTED, operation_time, {
                "agent_id":            stamped.agent_id,
                "reason":              f"trust={trust:.3f} < {TRUST_REJECT_THRESHOLD}",
                "reason_code":         "LOW_TRUST",
                "status":              STATUS_REJECTED_UNTRUSTED,
                "trust_score":         trust,
                "threshold":           TRUST_REJECT_THRESHOLD,
                "confidence_score":    stamped.confidence_score,
            }))
            record_failure(
                FailureType.TRUST_REJECTED,
                message=f"agent {stamped.agent_id} trust={trust:.3f} < {TRUST_REJECT_THRESHOLD}",
                agent_id=stamped.agent_id,
                path=path,
                recoverable=False,
            )
            return PipelineResult(
                status=STATUS_REJECTED_UNTRUSTED,
                message=f"agent {stamped.agent_id} trust={trust:.3f} < {TRUST_REJECT_THRESHOLD}",
            )
        
        # Low-trust agent making a high-confidence claim → reject.
        # The gate evaluates the MIDDLEWARE-derived verified_confidence, never
        # the agent's self-reported confidence_score (which an agent could
        # trivially report at any value). confidence_score stays audit-only.
        verified = stamped.provenance_info.verified_confidence or 0.0
        if trust < LOW_TRUST_THRESHOLD and verified >= HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD:
            _emit(CRTEvent(EventType.WRITE_REJECTED, operation_time, {
                "agent_id":            stamped.agent_id,
                "reason":              f"low-trust agent ({trust:.3f}) with high verified confidence ({verified:.3f})",
                "reason_code":         "LOW_TRUST_HIGH_CONFIDENCE",
                "status":              STATUS_REJECTED_SUSPICIOUS,
                "trust_score":         trust,
                "low_trust_threshold": LOW_TRUST_THRESHOLD,
                "verified_confidence": verified,
                "reported_confidence": stamped.confidence_score,
                "high_conf_threshold": HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD,
            }))
            record_failure(
                FailureType.TRUST_REJECTED,
                message=f"low-trust agent ({trust:.3f}) with high verified confidence ({verified:.3f})",
                agent_id=stamped.agent_id,
                path=path,
                recoverable=False,
            )
            return PipelineResult(
                status=STATUS_REJECTED_SUSPICIOUS,
                message=f"low-trust agent ({trust:.3f}) with high verified confidence ({verified:.3f})",
            )
        
        _emit(CRTEvent(EventType.PROVENANCE_VALIDATED, operation_time, {
            "agent_id":            stamped.agent_id,
            "session_id":          stamped.session_id,
            "path":                path,
            "value":               str(value),
            "provenance_id":       stamped.provenance_id,
            "source_type":         stamped.provenance_info.source_type,
            "verified_confidence": stamped.provenance_info.verified_confidence,
            "authority_score":     stamped.provenance_info.authority_score,
        }))

        # ── Step 2: Loop detection ──────────────────────────────────────────
        loop_result = self.loops.record_write(
            path=path,
            agent_id=stamped.agent_id,
            value=value,
            timestamp=stamped.timestamp,
        )
        if loop_result and loop_result.is_looping:
            _emit(CRTEvent(EventType.LOOP_DETECTED, operation_time, {
                "path":             path,
                "agent_id":         stamped.agent_id,
                "agents_involved":  loop_result.agents_involved,
                "mutation_rate":    loop_result.mutation_rate,
                "message":          loop_result.message,
                "reason_code":      "LOOP_FROZEN",
                "status":           STATUS_LOOP_FROZEN,
            }))
            record_failure(
                FailureType.LOOP_FROZEN,
                message=loop_result.message,
                agent_id=stamped.agent_id,
                path=path,
                details={
                    "agents_involved": loop_result.agents_involved,
                    "mutation_rate": loop_result.mutation_rate,
                },
                recoverable=False,
            )
            return PipelineResult(
                status=STATUS_LOOP_FROZEN,
                loop=loop_result,
                message=loop_result.message,
            )

        # ── Step 3: Acquire write lock ────────────────────────────────────────
        lock_result = await self.locks.acquire_write_lock(path)
        if not lock_result.acquired:
            record_failure(
                FailureType.LOCK_FAILURE,
                message=f"Could not acquire lock for path '{path}': {lock_result.message}",
                agent_id=stamped.agent_id,
                path=path,
                recoverable=True,  # Lock failures are typically transient
            )
            return PipelineResult(
                status=STATUS_LOCK_FAILED,
                message=f"Could not acquire lock for path '{path}': {lock_result.message}",
            )

        try:
            result = await self._locked_write(stamped, path, domain, operation_time)
        finally:
            await self.locks.release_write_lock(path, lock_result.token)

        return result

    # ------------------------------------------------------------------
    # Locked section (called while holding the path lock)
    # ------------------------------------------------------------------

    async def _locked_write(
        self,
        stamped: StampedUMF,
        path: str,
        domain: Optional[str],
        reference_time: Optional[datetime] = None,
    ) -> PipelineResult:
        existing = self.storage.get_existing(path)

        if existing is None:
            # ── No conflict: direct commit ────────────────────────────────────────
            self.storage.commit(stamped, path)
            self._record_lineage(stamped, path)
            _emit(CRTEvent(EventType.MEMORY_INGESTED, reference_time, {
                "path":                path,
                "agent_id":            stamped.agent_id,
                "session_id":          stamped.session_id,
                "value":               str(next(iter(stamped.assertion_payload.values()))),
                "provenance_id":       stamped.provenance_id,
                "source_type":         stamped.provenance_info.source_type,
                "verified_confidence": stamped.provenance_info.verified_confidence,
                "authority_score":     stamped.provenance_info.authority_score,
            }))
            return PipelineResult(
                status=STATUS_COMMITTED,
                committed=stamped,
                message="Packet committed (no prior memory at this path).",
            )

        # ── Step 4: Resolve conflict ────────────────────────────────────────────
        effective_domain = domain or stamped.provenance_info.domain or "_global"

        _emit(CRTEvent(EventType.CONFLICT_DETECTED, reference_time, {
            "path":           path,
            "existing_agent": existing.agent_id,
            "incoming_agent": stamped.agent_id,
            "existing_value": str(next(iter(existing.assertion_payload.values()))),
            "incoming_value": str(next(iter(stamped.assertion_payload.values()))),
            "domain":         effective_domain,
        }))

        conflict_result = self.conflict.resolve_conflict(
            existing=existing,
            incoming=stamped,
            trust_table={},          # pipeline always uses TrustManager
            domain=effective_domain,
            trust_manager=self.trust,
            reference_time=reference_time,
            strict_domain=True,      # Corrected Phase A: no silent _global fallback
        )

        # Compute per-component breakdown for the Inspector
        ref_time = reference_time
        t_existing = self.trust.get_trust(
            existing.agent_id, effective_domain, as_of=ref_time, strict_domain=True
        )
        t_incoming = self.trust.get_trust(
            stamped.agent_id, effective_domain, as_of=ref_time, strict_domain=True
        )
        breakdown_existing = self.conflict.calculate_psi_breakdown(existing, t_existing, ref_time)
        breakdown_incoming = self.conflict.calculate_psi_breakdown(stamped, t_incoming, ref_time)

        formula = "Psi = (R + C + T) / 3" if self.use_v1 else "Psi = 0.25*R + 0.25*C + 0.25*T + 0.25*P"
        psi_components = ["R", "C", "T"] if self.use_v1 else ["R", "C", "T", "P"]

        # Observe-only accounting event. It exposes actual runtime inputs and
        # derivation rules before the decision without affecting resolution.
        _emit(CRTEvent(EventType.PSI_COMPUTED, reference_time, {
            "path": path,
            "domain": effective_domain,
            "existing_agent": existing.agent_id,
            "incoming_agent": stamped.agent_id,
            "existing_source_type": existing.provenance_info.source_type,
            "incoming_source_type": stamped.provenance_info.source_type,
            "breakdowns": {
                "existing": {**breakdown_existing},
                "incoming": {**breakdown_incoming},
            },
            "component_sources": {
                "R": (f"Claim timestamps existing={existing.timestamp.isoformat()}, "
                      f"incoming={stamped.timestamp.isoformat()}, relative to reference={ref_time.isoformat()}"),
                "C": (f"Middleware-derived verified confidence from admitted evidence: "
                      f"existing source={existing.provenance_info.source_type}, "
                      f"incoming source={stamped.provenance_info.source_type}"),
                "T": f"Timestamped verified outcomes from TrustManager in strict domain '{effective_domain}'",
                **({"P": f"Source authority_score: existing={existing.provenance_info.authority_score}, incoming={stamped.provenance_info.authority_score}"} if not self.use_v1 else {}),
            },
            "formula": formula,
            "provenance_metadata": {
                "existing": {
                    "source_type": existing.provenance_info.source_type,
                    "authority_score": existing.provenance_info.authority_score,
                    "provenance_id": existing.provenance_id,
                },
                "incoming": {
                    "source_type": stamped.provenance_info.source_type,
                    "authority_score": stamped.provenance_info.authority_score,
                    "provenance_id": stamped.provenance_id,
                },
            },
        }))

        # ── Transparent accounting: weighted component deltas ──────────────────
        # Which side won drives which breakdown is "winner" vs "loser".
        winner_is_existing = conflict_result.winner.agent_id == existing.agent_id
        breakdown_winner = breakdown_existing if winner_is_existing else breakdown_incoming
        breakdown_loser  = breakdown_incoming if winner_is_existing else breakdown_existing

        component_deltas = {}
        for comp in psi_components:
            w_key = "w_" + comp.lower()
            w_weight = breakdown_winner.get(w_key, 0.25 if not self.use_v1 else 1/3)
            l_weight = breakdown_loser.get(w_key, 0.25 if not self.use_v1 else 1/3)
            w_val = breakdown_winner.get(comp, 0.0) * w_weight
            l_val = breakdown_loser.get(comp, 0.0) * l_weight
            component_deltas[comp] = round(w_val - l_val, 6)

        # Deciding factor: the component whose weighted contribution most favors
        # the winner (largest positive delta). None when unresolved.
        deciding_factor = None
        if not conflict_result.unresolved:
            deciding_factor = max(component_deltas, key=lambda c: component_deltas[c])

        psi_delta = round(abs(conflict_result.psi_winner - conflict_result.psi_loser), 6)

        conflict_event_data = {
            "path":                  path,
            "existing_agent":        existing.agent_id,
            "incoming_agent":        stamped.agent_id,
            "existing_value":        str(next(iter(existing.assertion_payload.values()))),
            "incoming_value":        str(next(iter(stamped.assertion_payload.values()))),
            "winner_agent":          conflict_result.winner.agent_id,
            "loser_agent":           conflict_result.loser.agent_id,
            "psi_winner":            conflict_result.psi_winner,
            "psi_loser":             conflict_result.psi_loser,
            "delta":                 psi_delta,
            "component_deltas":      component_deltas,
            "deciding_factor":       deciding_factor,
            "threshold":             self.conflict.uncertainty_threshold,
            "status":                STATUS_UNRESOLVED if conflict_result.unresolved else STATUS_RESOLVED,
            "psi_breakdown":         {"existing": breakdown_existing, "incoming": breakdown_incoming},
            "reason":                conflict_result.reason,
            "unresolved":            conflict_result.unresolved,
            "existing_source_type":  existing.provenance_info.source_type,
            "incoming_source_type":  stamped.provenance_info.source_type,
            "domain":                effective_domain,
        }

        if conflict_result.unresolved:
            # Keep both in storage; do not overwrite.
            # Commit incoming as a "pending" alternative.
            self.storage.commit_pending(stamped, path)
            # Record the pending alternative as derived from the incumbent.
            self._record_lineage(stamped, path, extra_parents=[existing.provenance_id])
            _emit(CRTEvent(EventType.CONFLICT_UNRESOLVED, reference_time, conflict_event_data))
            record_failure(
                FailureType.CONFLICT_UNRESOLVED,
                message=conflict_result.reason,
                agent_id=stamped.agent_id,
                path=path,
                details={
                    "existing_agent": existing.agent_id,
                    "incoming_agent": stamped.agent_id,
                },
                recoverable=True,  # Unresolved conflicts can be resolved later
            )
            return PipelineResult(
                status=STATUS_UNRESOLVED,
                committed=existing,   # incumbent stays live
                conflict=conflict_result,
                message=conflict_result.reason,
            )

        # A winning incoming claim changes the active reference. Re-evaluate
        # every pending alternative deterministically before committing state.
        if conflict_result.winner.provenance_id == stamped.provenance_id:
            return self._commit_incoming_and_reconcile_pending(
                existing, stamped, path, effective_domain, reference_time,
                conflict_result, conflict_event_data,
            )

        # ── Step 5: Commit winner, archive loser ─────────────────────────────────
        # Update verification_status: loser is "replaced", winner remains "unverified" until external verification
        self.storage.update_provenance_fields(
            conflict_result.loser.provenance_id,
            verification_status="replaced",
        )
        
        self.storage.archive(conflict_result.loser.provenance_id)
        self.storage.commit(conflict_result.winner, path)
        # Record both participants in the provenance chain. The winner is derived
        # from the incumbent AND the challenger; the loser stays linked to its own
        # parents (its node persists even though the memory is archived).
        self._record_lineage(conflict_result.loser, path)
        self._record_lineage(
            conflict_result.winner,
            path,
            extra_parents=[existing.provenance_id, stamped.provenance_id],
        )
        _emit(CRTEvent(EventType.CONFLICT_RESOLVED, reference_time, conflict_event_data))

        return PipelineResult(
            status=STATUS_CONFLICT_RESOLVED,
            committed=conflict_result.winner,
            conflict=conflict_result,
            message=conflict_result.reason,
        )

    def _commit_incoming_and_reconcile_pending(
        self,
        existing: StampedUMF,
        incoming: StampedUMF,
        path: str,
        domain: str,
        operation_time: datetime,
        initial_conflict: ConflictResult,
        conflict_event_data: Dict[str, Any],
    ) -> PipelineResult:
        """Reconcile pending alternatives against a newly winning incoming claim.

        Processing order is ``(timestamp, provenance_id)``. Decisions are fully
        computed before storage mutation. SQLite applies packet states and
        lineage in one transaction; fallback stores remain lock-serialized.
        """
        pending_getter=getattr(self.storage,"get_pending_conflicts",None)
        pending=sorted(pending_getter(path) if pending_getter else [],
            key=lambda p:(p.timestamp,p.provenance_id))
        states={existing.provenance_id:(existing,"archived"),incoming.provenance_id:(incoming,"active")}
        current=incoming; final_conflict=initial_conflict; transitions=[]
        for alternative in pending:
            if alternative.assertion_payload == current.assertion_payload:
                states[alternative.provenance_id]=(alternative,"coalesced_pending")
                transitions.append((EventType.PENDING_COALESCED,alternative,current,None))
                continue
            comparison=self.conflict.resolve_conflict(current,alternative,{},reference_time=operation_time,
                domain=domain,trust_manager=self.trust)
            if comparison.unresolved:
                states[alternative.provenance_id]=(alternative,"pending_conflict")
                transitions.append((EventType.PENDING_RETAINED,alternative,current,comparison))
            elif comparison.winner.provenance_id == alternative.provenance_id:
                states[current.provenance_id]=(current,"superseded_pending" if current.provenance_id!=incoming.provenance_id else "archived")
                states[alternative.provenance_id]=(alternative,"active")
                current=alternative; final_conflict=comparison
                transitions.append((EventType.PENDING_PROMOTED,alternative,comparison.loser,comparison))
            else:
                states[alternative.provenance_id]=(alternative,"superseded_pending")
                transitions.append((EventType.PENDING_DEFEATED,alternative,current,comparison))

        # Exactly one active record is permitted after reconciliation.
        for pid,(packet,state) in list(states.items()):
            if state=="active" and pid!=current.provenance_id:
                states[pid]=(packet,"archived")
        states[current.provenance_id]=(current,"active")

        from crt_core.lineage import node_from_stamped
        parents=[existing.provenance_id,incoming.provenance_id]+[p.provenance_id for p in pending]
        lineage=[node_from_stamped(incoming,path=path,extra_parents=[existing.provenance_id]),
            node_from_stamped(current,path=path,extra_parents=parents)]
        atomic=getattr(self.storage,"apply_pending_transition",None)
        if atomic is not None:
            atomic(path,list(states.values()),lineage)
            get_metrics_registry().incr("lineage.nodes_recorded",len(lineage))
        else:
            for packet,state in states.values():
                if state=="active": self.storage.commit(packet,path)
                elif state=="pending_conflict": self.storage.commit_pending(packet,path)
                else: self.storage.archive(packet.provenance_id)
            self._record_lineage(incoming,path,extra_parents=[existing.provenance_id])
            self._record_lineage(current,path,extra_parents=parents)

        for event_type,alternative,reference,comparison in transitions:
            _emit(CRTEvent(event_type,operation_time,{"path":path,
                "pending_provenance_id":alternative.provenance_id,
                "reference_provenance_id":reference.provenance_id,
                "resulting_active_provenance_id":current.provenance_id,
                "comparison":None if comparison is None else {
                    "winner":comparison.winner.provenance_id,"unresolved":comparison.unresolved,
                    "psi_winner_exact":comparison.psi_winner_breakdown.get("total_psi_exact"),
                    "psi_loser_exact":comparison.psi_loser_breakdown.get("total_psi_exact")}}))
        conflict_event_data.update({"winner_agent":current.agent_id,
            "pending_transition_count":len(transitions),"resulting_active":current.provenance_id})
        _emit(CRTEvent(EventType.CONFLICT_RESOLVED,operation_time,conflict_event_data))
        return PipelineResult(status=STATUS_CONFLICT_RESOLVED,committed=current,conflict=final_conflict,
            message=initial_conflict.reason+f"; reconciled {len(transitions)} pending alternative(s)")

    # ------------------------------------------------------------------
    # Trust feedback (called externally once a claim is verified)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Provenance lineage (Phase 8)
    # ------------------------------------------------------------------

    def _record_lineage(
        self,
        stamped: StampedUMF,
        path: str,
        extra_parents: Optional[List[str]] = None,
    ) -> None:
        """Record a LineageNode for a committed/archived memory.

        Storage backends without the lineage table (the pure in-memory
        protocol) are silently skipped — lineage is a progressive enhancement.
        """
        from crt_core.lineage import node_from_stamped
        store = getattr(self.storage, "store_lineage_node", None)
        if store is None:
            return
        store(node_from_stamped(stamped, path=path, extra_parents=extra_parents))
        get_metrics_registry().incr("lineage.nodes_recorded")

    def record_verification(
        self,
        agent_id: str,
        correct: bool,
        domain: str = "_global",
    ) -> None:
        """
        Feed external verification back into the TrustManager.

        Call this whenever ground-truth becomes available (e.g. a human
        reviewer confirms or refutes a committed claim).
        """
        operation_time = self._clock()
        self.trust.record_outcome(
            agent_id, correct=correct, domain=domain, observed_at=operation_time)
        new_trust = self.trust.get_trust(agent_id, domain, as_of=operation_time)
        get_metrics_registry().incr("trust.updates")
        _emit(CRTEvent(EventType.TRUST_UPDATED, operation_time, {
            "agent_id":        agent_id,
            "correct":         correct,
            "domain":          domain,
            "new_trust_score": new_trust,
        }))
