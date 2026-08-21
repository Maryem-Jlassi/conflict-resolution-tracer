"""
FastAPI application for CRT service.

POST /write, GET /context/{path}, and GET /trust/{agent_id}

The endpoint handlers are thin:  they parse HTTP input, call the
WritePipeline, and map PipelineResult → HTTP response.
All business logic lives in crt_core.pipeline.WritePipeline.
"""

import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dateutil import parser as date_parser

from crt_core.pipeline import WritePipeline, PipelineResult
from crt_core.trust_manager import TrustManager
from crt_core.conflict import ConflictResolutionEngine
from crt_core.locking import AsyncLockManager
from crt_core.loop_detection import LoopDetector
from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import set_provider_registry, set_replay_guard
from crt_core.metrics import compute_metrics_snapshot, reset_metrics_registry
from crt_core.verifier import (
    EXPERIMENT_ORACLE_VERIFIER,
    RESERVED_VERIFIER_IDS,
    authenticate_verifier,
    canonical_verifier_message,
    get_verifier_secret,
)
from .storage import SQLiteProviderRegistry, SQLiteReplayGuard, SQLiteStorage
from .trust_ledger import TrustLedger, VerifyOutcomeStatus

# Inspector — observe-only layer
app = FastAPI(
    title="Conflict Resolution Tracer (CRT) Service",
    description="Deterministic multi-agent memory coherence middleware",
    version="0.2.1",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount inspector endpoints (/inspector/*) if available
# ---------------------------------------------------------------------------
# Shared singletons — one pipeline owns all modules
# ---------------------------------------------------------------------------

# Storage backend selection: an explicit CRT_SQLITE_PATH env var overrides the
# default in-memory store (used by the release gate's real-component acceptance,
# which runs the server as a subprocess against a temp-file SQLite DB). The
# replay guard and provider registry share the same DB so rotation/revocation
# and consumed nonces survive restarts.
_storage = SQLiteStorage(os.environ.get("CRT_SQLITE_PATH") or ":memory:")
_trust   = TrustManager()

# Durable verification outcome ledger (corrected Phase A /verify boundary).
# Shares the same SQLite DB as packets so the trust summary survives restarts.
_ledger = TrustLedger(_storage)

# Hydrate the runtime TrustManager from durable state at service start so the
# live write/conflict-resolution path consumes persistent domain-specific trust
# (Section B). This is idempotent: on a fresh DB there is nothing to load.
_ledger.load_into(_trust)

# Replay protection (Phase 5): persist consumed evidence nonces in the same
# SQLite DB that stores packets, so replays are caught across requests.
set_replay_guard(SQLiteReplayGuard(_storage))
# Provider key lifecycle (Phase 6): persist trusted provider keys so rotation
# and revocation survive restarts.
set_provider_registry(SQLiteProviderRegistry(_storage))

_resolution_policy = os.environ.get("CRT_RESOLUTION_POLICY", "full_crt")
_instance_id = os.environ.get("LCM_INSTANCE_ID", "default")
_database_id = os.environ.get("LCM_DATABASE_ID", "memory")
_evaluation_mode = os.environ.get("CRT_EVALUATION_MODE") == "1"
_evaluation_reference_raw = os.environ.get("LCM_EVALUATION_REFERENCE_TIME")
_evaluation_reference_time = date_parser.isoparse(_evaluation_reference_raw).replace(tzinfo=None) if _evaluation_reference_raw else None
_pipeline = WritePipeline(
    storage=_storage,
    trust_manager=_trust,
    conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.05, resolution_policy=_resolution_policy),
    lock_manager=AsyncLockManager(),
    loop_detector=LoopDetector(),
    clock=(lambda: _evaluation_reference_time) if _evaluation_reference_time else None,
)


def reset_for_testing():
    """Replace storage + pipeline with fresh in-memory instances (tests only)."""
    global _storage, _trust, _pipeline, _resolution_policy
    reset_metrics_registry()
    _storage = SQLiteStorage(":memory:")
    _trust   = TrustManager()
    set_replay_guard(SQLiteReplayGuard(_storage))
    set_provider_registry(SQLiteProviderRegistry(_storage))
    _resolution_policy = "full_crt"
    _pipeline = WritePipeline(
        storage=_storage,
        trust_manager=_trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.05, resolution_policy=_resolution_policy),
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
    content_hash: Optional[str] = Field(None, description="SHA-256 of the referenced evidence content")
    independence_group: Optional[str] = Field(None, description="Independently controlled source group")
    verification_method: Optional[str] = Field(None, description="Caller-reported method; middleware derives authoritative status")

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
            content_hash=self.content_hash,
            independence_group=self.independence_group,
            verification_method=self.verification_method,
        )


class WriteRequest(BaseModel):
    # Track P1 (schema hardening): unknown top-level fields (e.g. forged
    # provenance_id / authority_score / invalid_field) are REJECTED with 422
    # instead of being silently stripped by Pydantic's default extra="ignore".
    model_config = ConfigDict(extra="forbid")

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
    operation_time: Optional[str] = None
    psi_winner_exact: Optional[str] = None
    psi_loser_exact: Optional[str] = None
    margin_exact: Optional[str] = None
    threshold_exact: Optional[str] = None
    numeric_specification: Optional[Dict[str, Any]] = None


class ContextResponse(BaseModel):
    path: str
    facts: List[Dict[str, Any]]
    count: int


class VerificationRequest(BaseModel):
    """Corrected Phase A controlled-experiment verification boundary.

    Design rules enforced here:
      * ``verifier_id`` is NOT a field — the verifier identity is assigned
        internally by the server after HMAC authentication succeeds.
        ``extra="forbid"`` rejects any caller-supplied verifier identity, so a
        source/consumer agent cannot impersonate the experiment oracle.
      * ``verifier_token`` is the HMAC-SHA256 authentication of the immutable
        outcome payload; missing or invalid tokens are rejected.
      * ``target_agent_id`` is the agent whose claim is being verified — never
        ``experiment_oracle`` itself (self-verification is rejected).
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: str = Field(
        ..., min_length=1,
        description="Unique, caller-chosen id for this semantic verification event (replay + collision key)",
    )
    target_agent_id: str = Field(
        ..., min_length=1,
        description="Agent whose committed claim is being verified",
    )
    correct: bool = Field(..., description="Whether the memory content is correct")
    domain: str = Field("_global", min_length=1, description="Domain for trust tracking")
    target_provenance_id: Optional[str] = Field(
        None, description="Provenance ID of the verified memory (optional for trust-only feedback)"
    )
    observed_at: Optional[str] = Field(
        None, description="ISO-8601 time the verification observation was made (defaults to now)"
    )
    verifier_token: str = Field(
        ..., min_length=1,
        description="HMAC-SHA256 token authenticating this outcome payload with the server-held secret",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    health = {
        "service": "Conflict Resolution Tracer",
        "version": "0.2.1",
        "protocol_version": 1,
        "status": "operational",
        "readiness": "ready",
    }
    if _evaluation_mode:
        health.update({
            "resolution_policy": _resolution_policy,
            "instance_id": _instance_id,
            "database_id": _database_id,
            "evaluation_reference_time": _evaluation_reference_raw,
            "process_id": os.getpid(),
        })
    return health


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
    from crt_core.numeric import numeric_specification

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
        operation_time=result.conflict.operation_time.isoformat() if result.conflict and result.conflict.operation_time else None,
        psi_winner_exact=result.conflict.psi_winner_breakdown.get("total_psi_exact") if result.conflict else None,
        psi_loser_exact=result.conflict.psi_loser_breakdown.get("total_psi_exact") if result.conflict else None,
        margin_exact=result.conflict.psi_margin_exact if result.conflict else None,
        threshold_exact=result.conflict.threshold_exact if result.conflict else None,
        numeric_specification=numeric_specification() if result.conflict else None,
    )


@app.post("/verify", status_code=status.HTTP_200_OK)
async def verify(request: VerificationRequest):
    """
    Feed externally-verified ground truth back to the TrustManager.

    Corrected Phase A controlled-experiment boundary:
      1. Verifier authentication — the request must carry an HMAC-SHA256
         ``verifier_token`` over the immutable outcome payload, keyed by the
         server/held secret (``CRT_VERIFIER_SECRET``). The secret never lives in
         source, logs, or test artifacts. Missing or invalid authentication is
         rejected.
      2. Internal verifier identity — on successful authentication the verifier
         identity is assigned internally as ``experiment_oracle``; it is never
         read from request JSON (the request schema forbids extra fields).
      3. Provenance target validation — an optional ``target_provenance_id``
         stamps the memory's verification status.
      4. Durable outcome ledger — the (idempotent/collision-guarded) result is
         committed to SQLite, updating the durable ``agent_trust`` counters in the
         SAME transaction.
      5. Persistent trust — the runtime TrustManager is resynced from the durable
         row so the live write/conflict-resolution path consumes persistent
         domain-specific trust (Section B).
    """
    # --- 1. Authentication: server secret must be configured (fail-closed) ---
    secret = get_verifier_secret()
    if not secret:
        raise HTTPException(
            status_code=403,
            detail="verifier authentication not configured",
        )

    # --- 2. Reject unauthorized self-verification (verifier != target) ---
    if request.target_agent_id in RESERVED_VERIFIER_IDS:
        raise HTTPException(
            status_code=403,
            detail="verifier cannot verify itself",
        )

        # --- 3. Validate observed_at (optional). The canonical message and the
    #     durable fingerprint use the observed_at value VERBATIM from the
    #     request so the authenticated payload and the 7-field fingerprint are
    #     reproducible across idempotent replays (Section C). Parsing here only
    #     validates the value; the literal string is forwarded to the ledger.
    if request.observed_at:
        try:
            date_parser.isoparse(request.observed_at)
        except (ValueError, TypeError, date_parser.ParserError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid observed_at: {exc}",
            )

    # --- 1b. Authenticate the immutable outcome payload (HMAC-SHA256) ---
    message = canonical_verifier_message(
        outcome_id=request.outcome_id,
        target_agent_id=request.target_agent_id,
        domain=request.domain,
        correct=request.correct,
        target_provenance_id=request.target_provenance_id,
        observed_at=request.observed_at,
    )
    if not authenticate_verifier(message, request.verifier_token, secret):
        raise HTTPException(
            status_code=401,
            detail="invalid verifier authentication",
        )

    # --- 2b. Assign verifier identity INTERNALLY (never from the caller) ---
    verifier_identity = EXPERIMENT_ORACLE_VERIFIER

    # --- 4. Durable, atomic outcome + trust commit ---
    outcome_status = _ledger.record_verified_outcome(
        outcome_id=request.outcome_id,
        target_agent_id=request.target_agent_id,
        domain=request.domain,
        correct=request.correct,
        verifier_identity=verifier_identity,
        target_provenance_id=request.target_provenance_id,
        observed_at=request.observed_at,
    )

    if outcome_status == VerifyOutcomeStatus.COLLISION:
        raise HTTPException(
            status_code=409,
            detail=(
                "outcome_id_collision: same outcome_id reused with a different "
                "semantic payload"
            ),
        )

    # --- 3b. Provenance target validation: stamp verification status ---
    if request.target_provenance_id:
        verification_status = (
            "verified" if request.correct else "contradicted"
        )
        _storage.update_provenance_fields(
            request.target_provenance_id,
            verification_status=verification_status,
        )

    # --- 5. Resync runtime trust from the durable ledger (NEW outcomes only) ---
    # record_verified_outcome updates ONLY the request domain (corrected-protocol
    # domain isolation — no silent _global aggregate). Refresh that domain cache
    # so the running pipeline consumes persistent domain-specific trust.
    if outcome_status == VerifyOutcomeStatus.NEW:
        _ledger.refresh_trust(_trust, request.target_agent_id, request.domain)

    trust_score = _trust.get_trust(
        request.target_agent_id, domain=request.domain, strict_domain=True
    )

    return {
        "status": outcome_status,
        "verifier_identity": verifier_identity,
        "trust_score": trust_score,
        "outcome_id": request.outcome_id,
    }


@app.get("/lock_telemetry")
async def lock_telemetry():
    """
    Evaluation-only lock contention telemetry (Track 3.2).

    Served only when CRT_EVALUATION_MODE=1. Observation-only: exposes how long
    writers queued behind an already-held path lock (p50/p95/p99/max) as well
    as contention/timeout counters, so the stress track can report a lock wait
    distribution instead of only pass/fail counts.
    """
    if not _evaluation_mode:
        raise HTTPException(status_code=404, detail="not available")
    return _pipeline.locks.lock_telemetry()


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

    Algorithm (mirrors ContextRetriever in crt_core/compressor.py):
      1. Fetch all packets whose stored path starts with the requested prefix.
      2. For each exact path keep only the highest-Ψ packet (deduplication).
      3. Sort surviving packets by Ψ descending.
      4. Apply U-shaped placement so the top-ranked facts appear at both the
         start and end of the list — reinforcing high-priority context at both
         attention ends for LLM consumers.
         Rule: top min(3, max(1, N//5)) facts are mirrored at the tail.
         Short lists (N ≤ 3) are returned as-is; no duplication needed.
    """
    from crt_core.conflict import ConflictResolutionEngine as _CRE2
    _engine = _CRE2()

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
            from crt_core.schema import StampedUMF, ProvenanceInfo
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


# Deliberately no demo import or router registration here.  The default ASGI
# application is the production/research service.  The explicit ``demo.service``
# launcher constructs an isolated process-local database and attaches demo
# routes to that separate service instance.
