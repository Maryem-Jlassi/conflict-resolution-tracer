"""
Corrected Phase A — explicit numbered SQLite migrations.

Replaces the historical ``CREATE TABLE IF NOT EXISTS`` + ``ALTER TABLE``
inside ``try/except`` pattern with a versioned, idempotent migration ledger
(``schema_migrations``) and a ``PRAGMA table_info`` inspection helper.

Design constraints (supervisor corrected protocol):

* inspect schema before applying;
* apply migrations in ascending numeric order;
* each migration is idempotent;
* failures roll back safely (each migration runs in its own transaction);
* previously published rows remain readable;
* repeated startup does not re-apply already-applied migrations.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Tuple


MIGRATION_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def _column_names(conn, table: str) -> List[str]:
    """Return the ordered column names of a table via PRAGMA table_info."""
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Add ``column`` to ``table`` only if it is not already present.

    Using PRAGMA table_info inspection avoids the historical
    ``ALTER TABLE ... ADD COLUMN`` inside a bare try/except pattern.
    """
    if column in _column_names(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ---------------------------------------------------------------------------
# Migration bodies (version, name, callable)
# ---------------------------------------------------------------------------

def _migration_1_base(conn) -> None:
    """Version 1 — the current pre-Phase-A schema, made additive + idempotent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS umf_packets (
            provenance_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            assertion_payload TEXT NOT NULL,
            media_uri TEXT,
            media_hash TEXT,
            ingested_at TEXT NOT NULL,
            path TEXT NOT NULL,
            is_archived INTEGER DEFAULT 0
        )
    """)
    # Backward-compatible columns that historically were added later.
    _ensure_column(conn, "umf_packets", "agent_confidence_score", "REAL")
    _ensure_column(conn, "umf_packets", "provenance_info", "TEXT")
    conn.execute("DROP INDEX IF EXISTS idx_confidence")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_path ON umf_packets(path, is_archived)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_session ON umf_packets(agent_id, session_id)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidence_nonces (
            fingerprint TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL DEFAULT '',
            key_id TEXT NOT NULL DEFAULT '',
            nonce TEXT NOT NULL,
            first_seen TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidence_providers (
            provider_id TEXT NOT NULL,
            key_id TEXT NOT NULL,
            public_key_hex TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            PRIMARY KEY (provider_id, key_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provenance_lineage (
            provenance_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            parent_ids_json TEXT NOT NULL,
            path TEXT,
            payload_json TEXT
        )
    """)


def _migration_2_trust_ledger(conn) -> None:
    """Version 2 — durable verification outcome ledger and agent trust summary.

    ``trust_outcomes`` is the durable, unique-outcome receipt ledger used by
    the corrected-protocol /verify boundary (exact-replay idempotency and
    same-outcome-ID/different-payload collision rejection).

    ``agent_trust`` is the materialized per (agent, domain) summary that makes
    historical trust durable across service restarts and concurrently safe.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trust_outcomes (
            outcome_id            TEXT PRIMARY KEY,
            target_agent_id       TEXT NOT NULL,
            domain                TEXT NOT NULL,
            correct               INTEGER NOT NULL,
            verifier_identity     TEXT NOT NULL,
            target_provenance_id  TEXT,
            observed_at           TEXT NOT NULL,
            created_at            TEXT NOT NULL,
            semantic_fingerprint  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_trust (
            agent_id            TEXT NOT NULL,
            domain              TEXT NOT NULL,
            confirmed_correct   INTEGER NOT NULL DEFAULT 0,
            confirmed_incorrect INTEGER NOT NULL DEFAULT 0,
            last_verified_at    TEXT,
            PRIMARY KEY (agent_id, domain)
        )
    """)


# Ordered migration catalog (version, name, body). Append new entries only.
MIGRATIONS: List[Tuple[int, str, Callable]] = [
    (1, "base_schema", _migration_1_base),
    (2, "trust_ledger", _migration_2_trust_ledger),
]

MIGRATION_LATEST_VERSION = MIGRATIONS[-1][0]


def _applied_versions(conn) -> List[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return [r["version"] for r in rows]
    except Exception:
        return []


def _ensure_migration_ledger(conn) -> None:
    conn.execute(MIGRATION_LEDGER_DDL)


def run_migrations(conn) -> List[int]:
    """Apply any not-yet-applied migrations in ascending numeric order.

    Each migration runs inside its own transaction; a failure rolls back that
    migration and re-raises, leaving the ledger untouched for that version.

    Returns the list of versions applied during this call.
    """
    _ensure_migration_ledger(conn)
    applied = set(_applied_versions(conn))
    newly_applied: List[int] = []
    prev_isolation = getattr(conn, "isolation_level", None)
    try:
        # Disable implicit transaction wrapping so we control BEGIN/COMMIT.
        conn.isolation_level = None
        for version, name, body in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version in applied:
                continue
            try:
                conn.execute("BEGIN")
                body(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.utcnow().isoformat()),
                )
                conn.commit()
                newly_applied.append(version)
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.isolation_level = prev_isolation
    return newly_applied


def applied_migration_versions(conn) -> List[int]:
    """Return the migration versions currently applied in the ledger."""
    _ensure_migration_ledger(conn)
    return sorted(_applied_versions(conn))


def current_schema_version(conn) -> int:
    applied = applied_migration_versions(conn)
    return max(applied) if applied else 0
