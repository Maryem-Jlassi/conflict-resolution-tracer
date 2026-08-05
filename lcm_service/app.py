"""
FastAPI application for LCM service.

POST /write, GET /context/{path}, and GET /trust/{agent_id}

The endpoint handlers are thin:  they parse HTTP input, call the
WritePipeline, and map PipelineResult → HTTP response.
All business logic lives in lcm_core.pipeline.WritePipeline.
"""

import os

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dateutil import parser as date_parser

from lcm_core.pipeline import WritePipeline, PipelineResult
from lcm_core.trust_manager import TrustManager
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.crypto import set_provider_registry, set_replay_guard
from lcm_core.metrics import compute_metrics_snapshot, reset_metrics_registry
from .storage import SQLiteProviderRegistry, SQLiteReplayGuard, SQLiteStorage

# Inspector — observe-only layer
try:
    from inspector.backend.api import router as inspector_router
    from inspector.backend.event_store import get_event_store
    INSPECTOR_AVAILABLE = True
except ImportError:
    INSPECTOR_AVAILABLE = False


app = FastAPI(
    title="Living Context Memory (LCM) Service",
    description="Deterministic multi-agent memory coherence middleware",
    version="0.2.1",
)

# Mount inspector endpoints (/inspector/*) if available
if INSPECTOR_AVAILABLE:
    app.include_router(inspector_router)
    # Initialise the event store (subscribes to the EventBus)
    event_store = get_event_store()

# ---------------------------------------------------------------------------
# Shared singletons — one pipeline owns all modules
# ---------------------------------------------------------------------------

# Storage backend selection: an explicit LCM_SQLITE_PATH env var overrides the
# default in-memory store (used by the release gate's real-component acceptance,
# which runs the server as a subprocess against a temp-file SQLite DB). The
# replay guard and provider registry share the same DB so rotation/revocation
# and consumed nonces survive restarts.
_storage = SQLiteStorage(os.environ.get("LCM_SQLITE_PATH") or ":memory:")
_trust   = TrustManager()

# Replay protection (Phase 5): persist consumed evidence nonces in the same
# SQLite DB that stores packets, so replays are caught across requests.
set_replay_guard(SQLiteReplayGuard(_storage))
# Provider key lifecycle (Phase 6): persist trusted provider keys so rotation
# and revocation survive restarts.
set_provider_registry(SQLiteProviderRegistry(_storage))

_pipeline = WritePipeline(
    storage=_storage,
    trust_manager=_trust,
    conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.05),
    lock_manager=AsyncLockManager(),
    loop_detector=LoopDetector(),
)


def reset_for_testing():
    """Replace storage + pipeline with fresh in-memory instances (tests only)."""
    global _storage, _trust, _pipeline
    reset_metrics_registry()
    _storage = SQLiteStorage(":memory:")
    _trust   = TrustManager()
    set_replay_guard(SQLiteReplayGuard(_storage))
    set_provider_registry(SQLiteProviderRegistry(_storage))
    _pipeline = WritePipeline(
        storage=_storage,
        trust_manager=_trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.05),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(),
    )


# kept for backward compat with existing tests that call reset_storage_for_testing()
def reset_storage_for_testing():
    reset_for_testing()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class EvidenceRecordInput(BaseModel):
    """
    Evidence record supplied by the caller to inform confidence calculation.
    
    LIMITATION: Agents can still fabricate evidence metadata (e.g. claim "database"
    evidence that doesn't exist). The current design trusts the agent's claim about
    the evidence type and source.
    
    FUTURE WORK: Evidence should be collected by the middleware itself — the agent
    submits a claim, and the middleware calls the actual database/tool to verify it,
    then generates the EvidenceRecord internally. This eliminates trust in agent-provided
    evidence metadata entirely.
    
    Current mitigation: We reject verified=true with no source (prevents the cheapest
    fabrication vector), but agents can still lie about unverified evidence or
    provide fake source identifiers.
    """
    type: str = Field(..., description="Evidence type: user_input | database | tool_output | document | agent_claim")
    source: Optional[str] = Field(None, description="Source identifier (table name, tool name, URI, etc.)")
    relevance: float = Field(1.0, ge=0.0, le=1.0, description="How relevant this evidence is (0-1)")
    verified: bool = Field(False, description="Whether this evidence has been externally verified")
    issued_at: Optional[str] = Field(None, description="ISO-8601 evidence issue time (temporal binding)")
    expires_at: Optional[str] = Field(None, description="ISO-8601 evidence expiry time (temporal binding)")
    nonce: Optional[str] = Field(None, description="Unique per-binding nonce (replay protection)")
    provider_id: Optional[str] = Field(None, description="Evidence provider identity")
    key_id: Optional[str] = Field(None, description="Provider key identifier")

    def to_evidence_record(self) -> EvidenceRecord:
        try:
            ev_type = EvidenceType(self.type)
        except ValueError:
            ev_type = EvidenceType.AGENT_CLAIM
        
        # CRITICAL: Prevent fabrication of verified evidence without source
        # An agent claiming verified=true with no source field is lying by omission
        if self.verified and not self.source:
            raise ValueError(
                "Evidence cannot be marked verified=true without providing a source identifier. "
                "This prevents agents from fabricating evidence provenance."
            )
        
        return EvidenceRecord(
            evidence_type=ev_type,
            source_id=self.source,
            relevance_score=self.relevance,
            verified=self.verified,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            nonce=self.nonce,
            provider_id=self.provider_id,
            key_id=self.key_id,
        )


class WriteRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    timestamp: str = Field(..., description="ISO 8601 or flexible datetime string")
    confidence_score: float = Field(0.8, ge=0.0, le=1.0)
    assertion_payload: Any
    media_uri: Optional[str] = None
    media_hash: Optional[str] = None
    domain: Optional[str] = None
    evidence_records: List[EvidenceRecordInput] = Field(
        default_factory=list,
        description="External evidence supporting this claim — drives ConfidenceEngine"
    )
    agreeing_agents: int = Field(
        0,
        ge=0,
        description="Number of independent agents asserting the same claim"
    )
    total_independent_agents: int = Field(
        0,
        ge=0,
        description="Total independent agents that weighed in on this claim"
    )
    evidence_signature: Optional[str] = Field(
        None,
        description="Cryptographic signature from trusted Data Provider for evidence binding"
    )
    verified_memories_consistent: Optional[bool] = Field(
        None,
        description="Whether this claim is consistent with verified prior memories"
    )


class WriteResponse(BaseModel):
    status: str          # committed | conflict_resolved | unresolved | rejected | loop_frozen | lock_failed
    provenance_id: Optional[str] = None
    message: str
    winner_agent: Optional[str] = None
    loser_agent: Optional[str] = None
    unresolved: bool = False
    psi_winner_breakdown: Optional[Dict[str, Any]] = None  # Per-component Ψ scores for winner
    psi_loser_breakdown: Optional[Dict[str, Any]] = None    # Per-component Ψ scores for loser
    description: Optional[str] = None                       # Human-readable adjudicator audit string


class ContextResponse(BaseModel):
    path: str
    facts: List[Dict[str, Any]]
    count: int


class VerificationRequest(BaseModel):
    provenance_id: Optional[str] = Field(None, description="Provenance ID of the memory to verify (optional: trust-only feedback)")
    agent_id: str = Field(..., description="Agent ID for trust tracking")
    correct: bool = Field(..., description="Whether the memory content is correct")
    domain: str = Field("_global", description="Domain for trust tracking")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"service": "Living Context Memory", "version": "0.2.1", "status": "operational"}


@app.post("/write", response_model=WriteResponse, status_code=status.HTTP_201_CREATED)
async def write(request: WriteRequest):
    """
    Accept a memory assertion from an agent and run it through the full pipeline:
    validate → loop-detect → lock → conflict-resolve → commit → (trust updated on /verify).
    """
    try:
        raw = request.model_dump()
        parsed_ts = date_parser.parse(raw.pop("timestamp"))
        # Normalize to naive UTC: the pipeline/conflict engine use datetime.utcnow()
        # throughout, so aware datetimes (e.g. "Z"/offset strings) must be stripped.
        if parsed_ts.tzinfo is not None:
            parsed_ts = parsed_ts.astimezone(timezone.utc).replace(tzinfo=None)
        raw["timestamp"] = parsed_ts
        if isinstance(raw["assertion_payload"], str):
            raw["assertion_payload"] = {"raw_claim": raw["assertion_payload"]}
        domain = raw.pop("domain", None)
        evidence_signature = raw.pop("evidence_signature", None)
        # Convert the Pydantic evidence models (NOT the plain dicts in `raw`).
        evidence_records = [e.to_evidence_record() for e in request.evidence_records]
        raw.pop("evidence_records", None)
        agreeing_agents = raw.pop("agreeing_agents", 0)
        total_independent_agents = raw.pop("total_independent_agents", 0)
        verified_memories_consistent = raw.pop("verified_memories_consistent", None)
    except (ValueError, TypeError, date_parser.ParserError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}")

    result: PipelineResult = await _pipeline.process(
        raw,
        domain=domain,
        evidence_records=evidence_records or None,
        agreeing_agents=agreeing_agents,
        total_independent_agents=total_independent_agents,
        verified_memories_consistent=verified_memories_consistent,
        evidence_signature=evidence_signature,
    )

    if result.status == "rejected":
        raise HTTPException(status_code=400, detail=result.message)

    if result.status == "lock_failed":
        raise HTTPException(status_code=503, detail=result.message)

    if result.status == "loop_frozen":
        raise HTTPException(status_code=409, detail=result.message)

    provenance_id = result.committed.provenance_id if result.committed else None
    winner_agent  = result.conflict.winner.agent_id if result.conflict else None
    loser_agent   = result.conflict.loser.agent_id  if result.conflict else None
    
    # Extract psi_breakdown and description from conflict result for explainability
    psi_winner_breakdown = result.conflict.psi_winner_breakdown if result.conflict else None
    psi_loser_breakdown = result.conflict.psi_loser_breakdown if result.conflict else None
    description = result.conflict.description if result.conflict else None

    return WriteResponse(
        status=result.status,
        provenance_id=provenance_id,
        message=result.message,
        winner_agent=winner_agent,
        loser_agent=loser_agent,
        unresolved=(result.status == "unresolved"),
        psi_winner_breakdown=psi_winner_breakdown,
        psi_loser_breakdown=psi_loser_breakdown,
        description=description,
    )


@app.post("/verify", status_code=status.HTTP_200_OK)
async def verify(request: VerificationRequest):
    """
    Feed external verification back to the TrustManager and update memory verification status.
    Call this when ground-truth about a committed claim becomes available.
    
    Updates:
    1. verification_status on the memory (verified/contradicted/replaced)
    2. Trust score for the agent
    """
    # Update verification status on the memory (when a provenance_id is supplied)
    verification_status = None
    if request.provenance_id:
        verification_status = "verified" if request.correct else "contradicted"
        _storage.update_provenance_fields(
            request.provenance_id,
            verification_status=verification_status,
        )
    
    # Update trust for the agent
    _pipeline.record_verification(
        agent_id=request.agent_id,
        correct=request.correct,
        domain=request.domain,
    )
    
    return {
        "status": "ok",
        "message": (
            f"Verification status updated to '{verification_status}' for memory "
            f"'{request.provenance_id}', trust updated for agent '{request.agent_id}'"
        )
    }


@app.get("/metrics", response_model=Dict[str, Any])
async def get_metrics():
    """
    Runtime telemetry snapshot (Phase 12): pipeline outcome counters, crypto
    verification counters, replay/temporal rejection counters, and derived
    rates (gate rejection, conflict resolution, replay/temporal rejection, …).

    No PII: all counters are agent-agnostic.
    """
    return compute_metrics_snapshot()


@app.get("/trust/{agent_id}", response_model=Dict[str, Any])
async def get_trust(agent_id: str, domain: str = "_global"):
    """
    Get the current trust score and outcome metadata for an agent.
    """
    meta = _trust.get_trust_with_meta(agent_id, domain=domain)
    return {
        "agent_id": agent_id,
        "domain": domain,
        "trust_score": meta["trust_score"],
        "outcome_count": meta["outcome_count"],
        "correct_count": meta["correct_count"],
        "incorrect_count": meta["incorrect_count"],
    }


@app.get("/context/{path:path}", response_model=ContextResponse)
async def get_context(path: str):
    """
    Return deduplicated, U-shaped prioritised facts for the given path.

    Algorithm (mirrors ContextRetriever in lcm_core/compressor.py):
      1. Fetch all packets whose stored path starts with the requested prefix.
      2. For each exact path keep only the highest-Ψ packet (deduplication).
      3. Sort surviving packets by Ψ descending.
      4. Apply U-shaped placement so the top-ranked facts appear at both the
         start and end of the list — reinforcing high-priority context at both
         attention ends for LLM consumers.
         Rule: top min(3, max(1, N//5)) facts are mirrored at the tail.
         Short lists (N ≤ 3) are returned as-is; no duplication needed.
    """
    from lcm_core.conflict import ConflictResolutionEngine as _CRE
    _engine = _CRE()

    packets = _storage.get_packets_by_path(path)
    if not packets:
        return ContextResponse(path=path, facts=[], count=0)

    def _psi(p: dict) -> float:
        """Compute Ψ from stored provenance fields; fall back to 0 on any gap."""
        prov = p.get("provenance_info") or {}
        if not isinstance(prov, dict):
            return 0.0
        vc = prov.get("verified_confidence")
        auth = prov.get("authority_score")
        if vc is None or auth is None:
            return 0.0
        agent_id = p.get("agent_id", "")
        trust = _trust.get_trust(agent_id)
        try:
            from lcm_core.schema import StampedUMF, ProvenanceInfo
            from datetime import datetime as _dt
            ts_raw = p.get("timestamp")
            ts = _dt.fromisoformat(ts_raw) if isinstance(ts_raw, str) else (ts_raw or _dt.utcnow())
            umf = StampedUMF(
                agent_id=agent_id,
                session_id=p.get("session_id", ""),
                timestamp=ts,
                confidence_score=p.get("agent_confidence_score") or p.get("confidence_score") or 0.5,
                assertion_payload=p.get("assertion_payload") or {"_": ""},
                provenance_id=p.get("provenance_id", ""),
                ingested_at=ts,
                provenance_info=ProvenanceInfo(
                    verified_confidence=vc,
                    authority_score=auth,
                ),
            )
            return _engine.calculate_psi(umf, trust)
        except Exception:
            return 0.0

    # Step 1 — build fact dicts with their Ψ scores
    scored: list[tuple[float, dict]] = []
    for p in packets:
        psi = _psi(p)
        prov = p.get("provenance_info") or {}
        fact = {
            "provenance_id": p["provenance_id"],
            "agent_id": p["agent_id"],
            "session_id": p["session_id"],
            "timestamp": p["timestamp"],
            "confidence_score": p.get("agent_confidence_score") or p.get("confidence_score"),
            "assertion_payload": p["assertion_payload"],
            "verified_confidence": prov.get("verified_confidence") if isinstance(prov, dict) else None,
            "authority_score": prov.get("authority_score") if isinstance(prov, dict) else None,
            "source_type": prov.get("source_type") if isinstance(prov, dict) else None,
            "verification_status": prov.get("verification_status", "unverified") if isinstance(prov, dict) else "unverified",
            "psi_score": round(psi, 4),
        }
        scored.append((psi, fact))

    # Step 2 — deduplicate: one packet per exact path (highest Ψ wins)
    # Packets from storage are already one-per-path (live committed state),
    # but guard against any future multi-version storage by deduplicating here.
    seen: dict[str, tuple[float, dict]] = {}
    for psi, fact in scored:
        pid = fact["provenance_id"]
        if pid not in seen or psi > seen[pid][0]:
            seen[pid] = (psi, fact)

    # Step 3 — sort by Ψ descending
    sorted_facts = [f for _, f in sorted(seen.values(), key=lambda x: x[0], reverse=True)]

    # Step 4 — U-shaped placement
    n = len(sorted_facts)
    if n > 3:
        num_priority = max(1, min(3, n // 5))
        priority = sorted_facts[:num_priority]
        middle = sorted_facts[num_priority:]
        sorted_facts = priority + middle + list(reversed(priority))

    return ContextResponse(path=path, facts=sorted_facts, count=len(sorted_facts))
