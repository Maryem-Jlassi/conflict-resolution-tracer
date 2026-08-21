"""
Corrected Phase A — durable verification outcome ledger and SQLite trust.

Backs the corrected-protocol ``POST /verify`` boundary with a durable SQLite
receipt ledger (``trust_outcomes``) and a materialized per (agent, domain)
trust summary (``agent_trust``).

Outcome-event semantics (supervisor corrected protocol):

* NEW event    -> insert receipt + update trust counts, atomically.
* Exact replay -> existing ``outcome_id`` with the identical semantic
                  fingerprint is an idempotent no-op; trust unchanged.
* Collision    -> existing ``outcome_id`` reused with a *different* semantic
                  payload is a collision; trust unchanged and the caller
                  receives ``outcome_id_collision``.

Outcome events are never treated as ordinary duplicates; a collision is
distinguished from a replay.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dateutil import parser as date_parser

from crt_core.verifier import canonical_verification_fingerprint


class VerifyOutcomeStatus:
    NEW = "new"
    IDEMPOTENT = "idempotent"
    COLLISION = "collision"


class TrustLedger:
    """Durable, concurrent-safe SQLite verification outcome ledger."""

    def __init__(self, storage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Authoritative write path
    # ------------------------------------------------------------------

    def record_verified_outcome(
        self,
        *,
        outcome_id: str,
        target_agent_id: str,
        domain: str,
        correct: bool,
        verifier_identity: str,
        target_provenance_id: Optional[str] = None,
        observed_at: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
    ) -> str:
        """Persist a verified outcome atomically, returning the status.

        Returns one of ``VerifyOutcomeStatus.NEW / IDEMPOTENT / COLLISION``.
        For IDEMPOTENT and COLLISION no trust counter is changed.
        """
        # Resolve a datetime for durable storage + last_verified_at. The
        # fingerprint below uses ``observed_at`` VERBATIM (str/None preserved)
        # so it stays reproducible from the authenticated payload (Section C).
        if isinstance(observed_at, datetime):
            observed = observed_at
        elif isinstance(observed_at, str):
            observed = date_parser.isoparse(observed_at)
        else:
            observed = datetime.utcnow()
        # Normalize to naive UTC for durable storage + the TrustManager
        # (pipeline convention); the fingerprint below uses ``observed_at``
        # verbatim so authentication remains consistent.
        if observed.tzinfo is not None:
            observed = observed.astimezone(timezone.utc).replace(tzinfo=None)
        created = created_at or datetime.utcnow()
        correct_int = 1 if correct else 0
        fingerprint = canonical_verification_fingerprint(
            outcome_id=outcome_id,
            target_agent_id=target_agent_id,
            domain=domain,
            correct=correct,
            verifier_identity=verifier_identity,
            target_provenance_id=target_provenance_id,
            observed_at=observed_at,
        )

        with self._storage._get_connection() as conn:
            conn.isolation_level = None  # manual transaction control
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT semantic_fingerprint FROM trust_outcomes WHERE outcome_id = ?",
                    (outcome_id,),
                ).fetchone()
                if existing is not None:
                    conn.rollback()
                    if existing["semantic_fingerprint"] == fingerprint:
                        return VerifyOutcomeStatus.IDEMPOTENT
                    return VerifyOutcomeStatus.COLLISION

                conn.execute(
                    """
                    INSERT INTO trust_outcomes
                        (outcome_id, target_agent_id, domain, correct,
                         verifier_identity, target_provenance_id,
                         observed_at, created_at, semantic_fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome_id,
                        target_agent_id,
                        domain,
                        correct_int,
                        verifier_identity,
                        target_provenance_id,
                        observed.isoformat(),
                        created.isoformat(),
                        fingerprint,
                    ),
                )
                self._increment(conn, target_agent_id, domain, correct_int, observed)
                # NOTE: We intentionally do NOT update a "_global" aggregate here.
                # Domain-specific trust must remain isolated; cross-domain transfer
                # would violate the corrected-protocol invariant (Section 1).
                # A separate explicit global-policy mode may be added in Phase B.
                conn.commit()
                return VerifyOutcomeStatus.NEW
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _increment(conn, agent: str, domain: str, correct_int: int, observed: datetime) -> None:
        observed_iso = observed.isoformat()
        row = conn.execute(
            "SELECT confirmed_correct, confirmed_incorrect FROM agent_trust "
            "WHERE agent_id = ? AND domain = ?",
            (agent, domain),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO agent_trust (agent_id, domain, confirmed_correct, "
                "confirmed_incorrect, last_verified_at) VALUES (?, ?, ?, ?, ?)",
                (agent, domain, correct_int, 1 - correct_int, observed_iso),
            )
        else:
            conn.execute(
                "UPDATE agent_trust SET confirmed_correct = confirmed_correct + ?, "
                "confirmed_incorrect = confirmed_incorrect + ?, last_verified_at = ? "
                "WHERE agent_id = ? AND domain = ?",
                (correct_int, 1 - correct_int, observed_iso, agent, domain),
            )
# ------------------------------------------------------------------
    # Hydration / read path
    # ------------------------------------------------------------------
    def refresh_trust(self, trust_manager, agent: str, domain: str) -> None:
        """Resync one agent/domain's in-memory TrustManager record from the
        durable ``agent_trust`` row.

        Called by the live ``POST /verify`` path after a NEW outcome is
        committed so the running WritePipeline consumes the durable
        domain-specific trust state immediately (Section B) without
        double-counting. ``seed_from_counts`` overwrites the in-memory counters
        from the single source-of-truth row, so this is idempotent and keeps the
        runtime cache strictly consistent with durable state.
        """
        row = self.agent_trust_summary(agent, domain)
        last_verified_at = None
        if row["last_verified_at"]:
            try:
                last_verified_at = datetime.fromisoformat(row["last_verified_at"])
                if last_verified_at.tzinfo is not None:
                    last_verified_at = last_verified_at.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                last_verified_at = None
        trust_manager.seed_from_counts(
            agent_id=agent,
            domain=domain,
            confirmed_correct=row["confirmed_correct"],
            confirmed_incorrect=row["confirmed_incorrect"],
            last_verified_at=last_verified_at,
        )

    def load_into(self, trust_manager) -> None:
        """Hydrate an in-memory TrustManager from the durable agent_trust table."""
        with self._storage._get_connection() as conn:
            rows = conn.execute(
                "SELECT agent_id, domain, confirmed_correct, confirmed_incorrect, "
                "last_verified_at FROM agent_trust"
            ).fetchall()
        for r in rows:
            last = None
            if r["last_verified_at"]:
                try:
                    last = datetime.fromisoformat(r["last_verified_at"])
                    if last.tzinfo is not None:
                        last = last.astimezone(timezone.utc).replace(tzinfo=None)
                except ValueError:
                    last = None
            trust_manager.seed_from_counts(
                agent_id=r["agent_id"],
                domain=r["domain"],
                confirmed_correct=int(r["confirmed_correct"]),
                confirmed_incorrect=int(r["confirmed_incorrect"]),
                last_verified_at=last,
            )

    def outcome(self, outcome_id: str) -> Optional[Dict[str, Any]]:
        with self._storage._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trust_outcomes WHERE outcome_id = ?", (outcome_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def count_outcomes(self) -> int:
        with self._storage._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM trust_outcomes").fetchone()
        return int(row["n"])

    def agent_trust_summary(self, agent: str, domain: str) -> Dict[str, Any]:
        with self._storage._get_connection() as conn:
            row = conn.execute(
                "SELECT confirmed_correct, confirmed_incorrect, last_verified_at "
                "FROM agent_trust WHERE agent_id = ? AND domain = ?",
                (agent, domain),
            ).fetchone()
        if row is None:
            return {"agent_id": agent, "domain": domain,
                    "confirmed_correct": 0, "confirmed_incorrect": 0,
                    "last_verified_at": None}
        return {"agent_id": agent, "domain": domain,
                "confirmed_correct": int(row["confirmed_correct"]),
                "confirmed_incorrect": int(row["confirmed_incorrect"]),
                "last_verified_at": row["last_verified_at"]}
