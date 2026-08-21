"""
Shared test factories and fixtures for the entire CRT test suite.

Import these from any test file:
    from tests.conftest import make_memory, make_provenance, engine

Or use pytest fixtures directly (reference_time, engine, trust_manager).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytest

# The crypto layer signs with the development Ed25519 provider key and
# verifies against the SAME key (see crt_core.crypto). That dev fallback
# requires an explicit opt-in (fail-closed by default). Tests and benchmarks
# are exactly the self-consistent consumers the dev key exists for, so enable
# it for the whole suite unless a real provider key is configured.
if not os.environ.get("CRT_EVIDENCE_PUBLIC_KEY"):
    os.environ.setdefault("CRT_ALLOW_DEV_EVIDENCE_KEY", "1")

from crt_core.confidence_engine import ConfidenceEngine, EvidenceRecord, EvidenceType
from crt_core.conflict import ConflictResolutionEngine
from crt_core.schema import ProvenanceInfo, StampedUMF
from crt_core.trust_manager import TrustManager

# ---------------------------------------------------------------------------
# Fixed reference point — all tests use this so timestamps are deterministic
# ---------------------------------------------------------------------------

REFERENCE_TIME = datetime(2026, 7, 14, 10, 0, 0)

_AUTHORITY_MAP: Dict[str, float] = {
    "user_input":  1.0,
    "database":    0.9,
    "tool_output": 0.85,
    "document":    0.75,
    "agent_claim": 0.3,
}

_ce = ConfidenceEngine()


# ---------------------------------------------------------------------------
# Core factories
# ---------------------------------------------------------------------------


def make_provenance(source: str = "database") -> ProvenanceInfo:
    """Return a ProvenanceInfo whose verified_confidence and authority match the source type."""
    return ProvenanceInfo(
        source_type=source,
        authority_score=_AUTHORITY_MAP.get(source, 0.5),
        verified_confidence=_ce.score_from_source_type(source),
    )


def make_memory(
    agent: str = "agent",
    source: str = "database",
    age_days: float = 0,
    confidence: float = 0.8,
    payload: Optional[Dict[str, Any]] = None,
    path: str = "test.fact",
    *,
    reference_time: datetime = REFERENCE_TIME,
) -> StampedUMF:
    """
    Create a StampedUMF with all Ψ-relevant fields set correctly.

    Parameters
    ----------
    agent       : agent_id string
    source      : evidence source type — drives both verified_confidence and authority_score
    age_days    : how many days old the memory is (0 = fresh at reference_time)
    confidence  : agent self-reported confidence (stored for transparency only)
    payload     : assertion payload dict; defaults to {path: agent}
    path        : key used when payload is auto-generated
    reference_time : point-in-time relative to which age is measured
    """
    ts = reference_time - timedelta(days=age_days)
    return StampedUMF(
        agent_id=agent,
        session_id=str(uuid.uuid4()),
        timestamp=ts,
        confidence_score=confidence,
        assertion_payload=payload or {path: agent},
        provenance_id=str(uuid.uuid4()),
        ingested_at=ts,
        provenance_info=make_provenance(source),
    )


def make_evidence(source: str = "database", relevance: float = 1.0) -> EvidenceRecord:
    """Return an EvidenceRecord for the given source type."""
    type_map = {
        "user_input":  EvidenceType.USER_INPUT,
        "database":    EvidenceType.DATABASE,
        "tool_output": EvidenceType.TOOL_OUTPUT,
        "document":    EvidenceType.DOCUMENT,
        "agent_claim": EvidenceType.AGENT_CLAIM,
    }
    return EvidenceRecord(
        evidence_type=type_map.get(source, EvidenceType.AGENT_CLAIM),
        relevance_score=relevance,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ref_time() -> datetime:
    """Fixed reference time shared across all tests."""
    return REFERENCE_TIME


@pytest.fixture
def engine() -> ConflictResolutionEngine:
    """Fresh ConflictResolutionEngine with uncertainty_threshold=0 (always resolves)."""
    return ConflictResolutionEngine(uncertainty_threshold=0.0)


@pytest.fixture
def trust_manager() -> TrustManager:
    """Fresh TrustManager with neutral cold-start prior."""
    return TrustManager()
