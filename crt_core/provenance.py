"""Provenance enforcement - validates and stamps incoming UMF packets."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, List, Optional
from uuid import uuid4
from pydantic import ValidationError

from .schema import UMF, StampedUMF, ProvenanceInfo
from .confidence_engine import ConfidenceEngine, EvidenceRecord, EvidenceType, EVIDENCE_AUTHORITY
from .canonical import canonical_json
from .crypto import canonical_assertion_hash, verify_evidence_signature_crypto
from .user_input_policy import get_user_input_policy
from .config import (
    AGENT_CLAIM_DEFAULT_AUTHORITY,
    AGENT_CLAIM_DEFAULT_CONFIDENCE,
    UNVERIFIED_AUTHORITY_FALLBACK,
    UNVERIFIED_CONFIDENCE_FALLBACK,
)


class RejectionError(Exception):
    """Raised when a packet fails validation and must be rejected."""

    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(message)


_confidence_engine = ConfidenceEngine()

# Fields that agents are never allowed to supply in the raw packet.
# Middleware alone may compute these.
_FORBIDDEN_AGENT_FIELDS = frozenset({
    "evidence_records",
    "authority_score",
    "verified_confidence",
    "content_hash",
    "provenance_id",
    "ingested_at",
    "provenance_info",
})


def _compute_content_hash(
    agent_id: str,
    timestamp: datetime,
    assertion_payload: dict,
    parent_hashes: Optional[List[str]] = None,
) -> str:
    """
    Deterministic SHA-256 over content + lineage.

    Any change to agent_id, timestamp, payload, or parent chain produces a
    different hash, making the provenance chain tamper-evident.
    """
    # Canonical encoding (Phase 7): order-independent and platform-stable,
    # unlike the previous repr()-based tuple encoding.
    payload_repr = canonical_json(assertion_payload)
    parts = [
        agent_id,
        timestamp.isoformat(),
        payload_repr,
    ]
    if parent_hashes:
        parts.append("|".join(sorted(parent_hashes)))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def verify_evidence_signature(
    evidence_type: EvidenceType,
    source_id: Optional[str],
    evidence_signature: Optional[str],
    content_hash: Optional[str] = None,
    issued_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    reference_time: Optional[datetime] = None,
    nonce: Optional[str] = None,
    provider_id: Optional[str] = None,
    key_id: Optional[str] = None,
    assertion_hash: Optional[str] = None,
) -> bool:
    """
    Verify the cryptographic evidence signature from a trusted Data Provider.

    For external evidence types (database, document, tool_output) the signature
    must be a valid Ed25519 signature (base64) over the canonical evidence
    message, verifiable against the configured provider public key
    (``CRT_EVIDENCE_PUBLIC_KEY`` env var, or the dev fallback key) AND still
    temporally valid (Phase 4) AND not a replay (Phase 5): an expired binding
    (``expires_at`` in the past), one issued in the future, or one whose
    ``nonce`` has already been consumed all fail verification.

    user_input now carries the SAME signature requirement as the other
    elevated source types (Phase 9 user-input policy) — it is no longer
    auto-accepted. Only pure agent_claim / agent_claim_default evidence
    bypasses the crypto gate entirely.

    Without a valid signature the provenance layer degrades authority_score to
    0.1 (unverified claim).
    """
    if evidence_type in (EvidenceType.AGENT_CLAIM, EvidenceType.AGENT_CLAIM_DEFAULT):
        return True
    return verify_evidence_signature_crypto(
        evidence_type, source_id, evidence_signature, content_hash,
        issued_at=issued_at,
        expires_at=expires_at,
        reference_time=reference_time,
        nonce=nonce,
        provider_id=provider_id,
        key_id=key_id or "",
        assertion_hash=assertion_hash,
    )


def validate_and_stamp(
    raw: dict[str, Any],
    evidence_records: Optional[List[EvidenceRecord]] = None,
    domain: Optional[str] = None,
    parent_memory_ids: Optional[List[str]] = None,
    valid_until: Optional[datetime] = None,
    agreeing_agents: int = 0,
    total_independent_agents: int = 0,
    verified_memories_consistent: Optional[bool] = None,
    evidence_signature: Optional[str] = None,
    reference_time: Optional[datetime] = None,
) -> StampedUMF:
    """
    Validate incoming raw packet against UMF schema and attach provenance stamps.

    The agent-provided confidence_score is preserved on the packet for
    reference only. Middleware-calculated verified_confidence is stored
    inside ProvenanceInfo and used by the conflict resolver — never the
    LLM self-report.

    Agents may not supply evidence_records, authority_score, verified_confidence,
    content_hash, or other middleware-owned fields inside the raw dict.
    Evidence must be supplied by the *caller* of this function (pipeline /
    trusted integration layer), never by the agent payload itself.

    If external evidence (database/document/tool_output) lacks a valid cryptographic
    evidence_signature, authority_score is degraded to 0.1 (unverified claim).

    Raises:
        RejectionError: If validation fails or forbidden fields are present.
    """
    if not isinstance(raw, dict):
        raise RejectionError(
            f"Expected dict, got {type(raw).__name__}. "
            "Plain strings or non-structured data are not valid UMF packets.",
            field="<root>",
        )

    # ── Reject agent-supplied middleware fields ───────────────────────────
    for key in _FORBIDDEN_AGENT_FIELDS:
        if key in raw:
            raise RejectionError(
                f"Agents may not supply '{key}'. "
                "This field is computed exclusively by middleware.",
                field=key,
            )

    try:
        umf = UMF(**raw)
    except ValidationError as e:
        errors = e.errors()
        if errors:
            first_error = errors[0]
            field_name = ".".join(str(loc) for loc in first_error["loc"])
            error_msg = first_error["msg"]
            raise RejectionError(
                f"Validation failed for field '{field_name}': {error_msg}",
                field=field_name,
            )
        raise RejectionError("Unknown validation error", field="<unknown>")
    except Exception as e:
        raise RejectionError(f"Unexpected error during validation: {str(e)}", field="<unknown>")

    # Recomputed from the validated write. Caller-supplied assertion hashes are
    # never accepted as authority.
    assertion_hash = canonical_assertion_hash(
        umf.agent_id, umf.timestamp, umf.assertion_payload, domain=domain
    )

    # evidence_records come from the trusted caller (pipeline), never from raw
    records = evidence_records or []

    # Compute middleware-verified confidence from external signals only.
    # Never use umf.confidence_score for conflict resolution.
    if records:
        # Legacy HTTP scalars are reported claims, not independently verified
        # confidence inputs. They remain auditable below but cannot raise score.
        verified_confidence = _confidence_engine.calculate(records)
    else:
        # Pure agent claim with no external evidence — treat as weak agent_claim_default.
        verified_confidence = AGENT_CLAIM_DEFAULT_CONFIDENCE
    # Infer source_type from highest-authority evidence record (if any)
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    authority_score: Optional[float] = None
    confidence_signals = []
    if records:
        best = max(
            records,
            key=lambda r: EVIDENCE_AUTHORITY[r.evidence_type] * r.relevance_score,
        )
        source_type = best.evidence_type.value
        source_id = best.source_id

        # Cryptographic Evidence Binding Check
        if best.evidence_type in (
            EvidenceType.DATABASE,
            EvidenceType.DOCUMENT,
            EvidenceType.TOOL_OUTPUT,
            EvidenceType.USER_INPUT,
        ):
            valid_sig = verify_evidence_signature(
                best.evidence_type, source_id, evidence_signature,
                content_hash=best.content_hash,
                issued_at=best.issued_at,
                expires_at=best.expires_at,
                reference_time=reference_time,
                nonce=best.nonce,
                provider_id=best.provider_id,
                key_id=best.key_id,
                assertion_hash=assertion_hash,
            )
        else:
            valid_sig = True

        # User-input policy (Phase 9): user_input is the highest-authority tier,
        # so it is gated by gateway attestation and (optionally) an explicit
        # delegation allowlist. A non-delegated relay is rejected fail-closed;
        # an unattested claim degrades to the policy's fallback source.
        if best.evidence_type == EvidenceType.USER_INPUT:
            if evidence_signature and not valid_sig:
                raise RejectionError(
                    "evidence signature is invalid for the received assertion",
                    field="evidence_signature",
                )
            decision = get_user_input_policy().evaluate(umf.agent_id, valid_sig)
            if not decision.accepted:
                raise RejectionError(
                    f"{decision.policy}: {decision.reason}",
                    field="evidence_records",
                )
            if decision.source_type is not None:
                source_type = decision.source_type
            if decision.authority_score is not None:
                authority_score = decision.authority_score
            if decision.verified_confidence_cap is not None:
                verified_confidence = min(
                    verified_confidence, decision.verified_confidence_cap
                )
        elif valid_sig:
            authority_score = _confidence_engine.authority_score_from_source_type(source_type)
        else:
            if evidence_signature:
                raise RejectionError(
                    "evidence signature is invalid for the received assertion",
                    field="evidence_signature",
                )
            # Signature missing or invalid -> Force authority down to the
            # configured fail-closed fallback (never let unsigned evidence claim
            # elevated status).
            authority_score = UNVERIFIED_AUTHORITY_FALLBACK
            verified_confidence = min(verified_confidence, UNVERIFIED_CONFIDENCE_FALLBACK)
        for record in records:
            is_selected = record is best
            confidence_signals.append({
                "signal_type": record.evidence_type.value,
                "supplying_provider": record.provider_id,
                "verification_status": "verified" if is_selected and valid_sig else "unverified",
                "verification_method": "ed25519_v1" if is_selected and valid_sig else "caller_report_only",
                "source_id": record.source_id,
                "evidence_hash": record.content_hash,
                "independence_group": record.independence_group,
                "timestamp": record.issued_at,
                "valid_until": record.expires_at,
            })
    else:
        source_type = EvidenceType.AGENT_CLAIM_DEFAULT.value
        authority_score = AGENT_CLAIM_DEFAULT_AUTHORITY

    parent_ids = parent_memory_ids or []

    prov_info = ProvenanceInfo(
        source_type=source_type,
        source_id=source_id,
        authority_score=authority_score,
        verification_status="unverified",
        memory_status="active",
        parent_memory_ids=parent_ids,
        domain=domain,
        verified_confidence=verified_confidence,
        valid_until=valid_until,
        confidence_signals=confidence_signals,
        reported_agreeing_agents=agreeing_agents,
        reported_total_independent_agents=total_independent_agents,
        reported_memories_consistent=verified_memories_consistent,
    )

    # Compute content hash *before* constructing the frozen model
    content_hash = _compute_content_hash(
        agent_id=umf.agent_id,
        timestamp=umf.timestamp,
        assertion_payload=umf.assertion_payload,
        parent_hashes=parent_ids,
    )

    stamped = StampedUMF(
        agent_id=umf.agent_id,
        session_id=umf.session_id,
        timestamp=umf.timestamp,
        confidence_score=umf.confidence_score,
        assertion_payload=umf.assertion_payload,
        media_uri=umf.media_uri,
        media_hash=umf.media_hash,
        provenance_id=str(uuid4()),
        ingested_at=reference_time or datetime.utcnow(),
        content_hash=content_hash,
        provenance_info=prov_info,
    )

    return stamped
