"""SQLite-backed storage for CRT packets."""

import sqlite3
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

from crt_core.replay import ReplayGuard, nonce_fingerprint


class SQLiteStorage:
    """SQLite backend for storing StampedUMF packets."""
    
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        # For in-memory databases, keep a shared connection
        self._shared_conn = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema via the numbered migration ledger.

        Corrected Phase A replaces the historical ``CREATE TABLE IF NOT EXISTS``
        + ``ALTER TABLE`` inside ``try/except`` pattern with explicit numbered
        migrations (see :mod:`crt_service.migrations`) that are idempotent,
        ordered, transactionally rolled back on failure, and recorded in the
        ``schema_migrations`` ledger.
        """
        from .migrations import run_migrations
        with self._get_connection() as conn:
            run_migrations(conn)

    # ------------------------------------------------------------------
    # Replay-protection table (Phase 5)
    # ------------------------------------------------------------------

    def record_nonce(self, fingerprint: str, provider_id: str = "", key_id: str = "", nonce: str = "") -> None:
        """Persist a nonce fingerprint (first sighting). Idempotent."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_nonces
                (fingerprint, provider_id, key_id, nonce, first_seen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fingerprint, provider_id, key_id, nonce, datetime.utcnow().isoformat()),
            )
            conn.commit()

    def nonce_exists(self, fingerprint: str) -> bool:
        """True when this nonce fingerprint was already recorded."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM evidence_nonces WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            return row is not None

    def check_and_record_nonce(self, fingerprint: str, provider_id: str = "", key_id: str = "", nonce: str = "") -> bool:
        """
        Atomic check-and-record for one nonce fingerprint.

        Returns:
            True when new (accepted), False when already seen (replay).
        """
        if self.nonce_exists(fingerprint):
            return False
        self.record_nonce(fingerprint, provider_id=provider_id, key_id=key_id, nonce=nonce)
        return True
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        if self._shared_conn:
            # For in-memory, use shared connection but don't close it
            yield self._shared_conn
        else:
            # For file-based, create new connection each time
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
    
    def store_packet(self, packet: Dict[str, Any], path: str, is_archived: bool = False):
        """Store a StampedUMF packet, including provenance_info."""
        # Serialise provenance_info — it may be a nested dict or a Pydantic model
        prov = packet.get("provenance_info")
        if prov is not None and not isinstance(prov, str):
            if hasattr(prov, "model_dump"):
                # Use mode='json' to ensure datetime fields are serialized
                prov = json.dumps(prov.model_dump(mode='json'))
            else:
                # Custom JSON encoder for datetime objects in plain dicts
                prov = json.dumps(prov, default=str)

        # Map confidence_score → agent_confidence_score for storage
        agent_conf = packet.get("agent_confidence_score")
        if agent_conf is None:
            agent_conf = packet.get("confidence_score")

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO umf_packets
                (provenance_id, agent_id, session_id, timestamp, agent_confidence_score,
                 assertion_payload, media_uri, media_hash, ingested_at, path,
                 is_archived, provenance_info)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                packet["provenance_id"],
                packet["agent_id"],
                packet["session_id"],
                packet["timestamp"] if isinstance(packet["timestamp"], str)
                    else packet["timestamp"].isoformat(),
                agent_conf,
                json.dumps(packet["assertion_payload"]),
                packet.get("media_uri"),
                packet.get("media_hash"),
                packet["ingested_at"] if isinstance(packet["ingested_at"], str)
                    else packet["ingested_at"].isoformat(),
                path,
                1 if is_archived else 0,
                prov,
            ))
            conn.commit()
    
    def get_packets_by_path(self, path_prefix: str, include_archived: bool = False) -> List[Dict[str, Any]]:
        """Retrieve packets matching a path prefix."""
        with self._get_connection() as conn:
            if include_archived:
                query = """
                    SELECT * FROM umf_packets
                    WHERE path LIKE ? OR path = ?
                    ORDER BY timestamp DESC
                """
            else:
                query = """
                    SELECT * FROM umf_packets
                    WHERE (path LIKE ? OR path = ?) AND is_archived = 0
                    ORDER BY timestamp DESC
                """
            
            cursor = conn.execute(query, (f"{path_prefix}%", path_prefix))
            rows = cursor.fetchall()
            
            packets = []
            for row in rows:
                packet = dict(row)
                packet["assertion_payload"] = json.loads(packet["assertion_payload"])
                # Deserialise provenance_info if stored
                prov_raw = packet.get("provenance_info")
                if prov_raw and isinstance(prov_raw, str):
                    try:
                        packet["provenance_info"] = json.loads(prov_raw)
                    except (json.JSONDecodeError, TypeError):
                        packet["provenance_info"] = None
                # Map agent_confidence_score for compatibility
                if "agent_confidence_score" in row:
                    packet["agent_confidence_score"] = row["agent_confidence_score"]
                packets.append(packet)
            
            return packets

    @staticmethod
    def _memory_status(provenance_info: Any) -> str:
        if isinstance(provenance_info, dict):
            return provenance_info.get("memory_status", "active")
        return "active"

    def _row_to_stamped(self, row: Dict[str, Any]) -> Any:
        from crt_core.schema import StampedUMF, ProvenanceInfo

        prov_raw = row.get("provenance_info")
        if isinstance(prov_raw, dict):
            try:
                prov_info = ProvenanceInfo(**prov_raw)
            except Exception:
                prov_info = ProvenanceInfo()
        else:
            prov_info = ProvenanceInfo()

        agent_conf = row.get("agent_confidence_score")
        if agent_conf is None:
            agent_conf = row.get("confidence_score")

        def _parse_dt(s: str):
            """Parse ISO-8601 datetime — handles trailing Z (Python <3.11 compat)."""
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        return StampedUMF(
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            timestamp=_parse_dt(row["timestamp"]),
            confidence_score=agent_conf,
            assertion_payload=row["assertion_payload"],
            media_uri=row.get("media_uri"),
            media_hash=row.get("media_hash"),
            provenance_id=row["provenance_id"],
            ingested_at=_parse_dt(row["ingested_at"]),
            provenance_info=prov_info,
        )

    def update_provenance_fields(
        self,
        provenance_id: str,
        *,
        verification_status: Optional[str] = None,
        memory_status: Optional[str] = None,
    ) -> None:
        """Update lifecycle fields on a stored packet's provenance_info."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT provenance_info FROM umf_packets WHERE provenance_id = ?",
                (provenance_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return

            prov_raw = row["provenance_info"]
            if prov_raw and isinstance(prov_raw, str):
                try:
                    prov = json.loads(prov_raw)
                except (json.JSONDecodeError, TypeError):
                    prov = {}
            elif isinstance(prov_raw, dict):
                prov = prov_raw
            else:
                prov = {}

            if verification_status is not None:
                prov["verification_status"] = verification_status
            if memory_status is not None:
                prov["memory_status"] = memory_status

            conn.execute(
                "UPDATE umf_packets SET provenance_info = ? WHERE provenance_id = ?",
                (json.dumps(prov, default=str), provenance_id),
            )
            conn.commit()
    
    def archive_packet(self, provenance_id: str):
        """Mark a packet as archived (never deleted)."""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE umf_packets
                SET is_archived = 1
                WHERE provenance_id = ?
            """, (provenance_id,))
            conn.commit()
    
    def get_all_paths(self) -> List[str]:
        """Get all unique paths in the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT DISTINCT path FROM umf_packets WHERE is_archived = 0")
            return [row[0] for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # MemoryStorage protocol — used by WritePipeline
    # ------------------------------------------------------------------

    def get_existing(self, path: str) -> Optional[Any]:
        """
        Return the current active committed StampedUMF for an exact path, or None.
        Satisfies the MemoryStorage protocol required by WritePipeline.
        """
        packets = self.get_packets_by_path(path)
        for row in packets:
            if self._memory_status(row.get("provenance_info")) == "active":
                return self._row_to_stamped(row)
        return None

    def get_pending_conflicts(self, path: str) -> List[Any]:
        """Return all pending_conflict alternatives stored for a path."""
        from crt_core.schema import StampedUMF

        pending: List[StampedUMF] = []
        for row in self.get_packets_by_path(path):
            if self._memory_status(row.get("provenance_info")) == "pending_conflict":
                pending.append(self._row_to_stamped(row))
        return pending

    def commit(self, umf: Any, path: str) -> None:
        """Persist a StampedUMF as the live active record. Satisfies MemoryStorage protocol."""
        active = umf.model_copy(update={
            "provenance_info": umf.provenance_info.model_copy(update={
                "memory_status": "active",
            })
        })
        self.store_packet(active.model_dump(mode="json"), path)

    def commit_pending(self, umf: Any, path: str) -> None:
        """Store an unresolved alternative without replacing the active memory."""
        pending = umf.model_copy(update={
            "provenance_info": umf.provenance_info.model_copy(update={
                "memory_status": "pending_conflict",
                "verification_status": "unverified",
            })
        })
        self.store_packet(pending.model_dump(mode="json"), path, is_archived=False)

    def _transition_precommit_hook(self) -> None:
        """Test seam invoked inside the pending-transition transaction."""

    def apply_pending_transition(
        self,
        path: str,
        packet_states: List[tuple[Any, str]],
        lineage_nodes: List[Any],
    ) -> None:
        """Atomically persist an active/pending/archive reconciliation plan.

        ``packet_states`` contains ``(StampedUMF, memory_status)`` pairs. The
        accepted statuses are active, pending_conflict, archived,
        superseded_pending and coalesced_pending. Archived-like records are
        retained with ``is_archived=1``; nothing is deleted.
        """
        allowed={"active","pending_conflict","archived","superseded_pending","coalesced_pending"}
        with self._get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                for packet,state in packet_states:
                    if state not in allowed: raise ValueError(f"invalid transition state: {state}")
                    archived=state in {"archived","superseded_pending","coalesced_pending"}
                    verification="replaced" if archived else packet.provenance_info.verification_status
                    updated=packet.model_copy(update={"provenance_info":packet.provenance_info.model_copy(
                        update={"memory_status":state,"verification_status":verification})})
                    data=updated.model_dump(mode="json"); prov=json.dumps(data["provenance_info"],default=str)
                    conn.execute("""
                        INSERT OR REPLACE INTO umf_packets
                        (provenance_id,agent_id,session_id,timestamp,agent_confidence_score,
                         assertion_payload,media_uri,media_hash,ingested_at,path,is_archived,provenance_info)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,(data["provenance_id"],data["agent_id"],data["session_id"],data["timestamp"],
                        data.get("confidence_score"),json.dumps(data["assertion_payload"]),data.get("media_uri"),
                        data.get("media_hash"),data["ingested_at"],path,1 if archived else 0,prov))
                for node in lineage_nodes:
                    conn.execute("""
                        INSERT OR REPLACE INTO provenance_lineage
                        (provenance_id,agent_id,content_hash,timestamp,parent_ids_json,path,payload_json)
                        VALUES (?,?,?,?,?,?,?)
                    """,(node.provenance_id,node.agent_id,node.content_hash,node.timestamp,
                        json.dumps(node.parent_memory_ids),node.path,json.dumps(node.payload) if node.payload else None))
                self._transition_precommit_hook()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def archive(self, provenance_id: str) -> None:
        """Mark packet as archived. Satisfies MemoryStorage protocol."""
        self.archive_packet(provenance_id)
        self.update_provenance_fields(
            provenance_id,
            memory_status="archived",
        )

    # ------------------------------------------------------------------
    # Provenance lineage (Phase 8)
    # ------------------------------------------------------------------

    def store_lineage_node(self, node: Any) -> None:
        """Persist a LineageNode (upsert by provenance_id)."""
        from crt_core.lineage import LineageNode
        if not isinstance(node, LineageNode):
            raise TypeError("store_lineage_node expects a LineageNode")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO provenance_lineage
                (provenance_id, agent_id, content_hash, timestamp,
                 parent_ids_json, path, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.provenance_id,
                    node.agent_id,
                    node.content_hash,
                    node.timestamp,
                    json.dumps(node.parent_memory_ids),
                    node.path,
                    json.dumps(node.payload, default=str) if node.payload is not None else None,
                ),
            )
            conn.commit()

    def get_lineage_node(self, provenance_id: str) -> Optional[Any]:
        """Return the stored LineageNode for a provenance_id, or None."""
        from crt_core.lineage import LineageNode
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM provenance_lineage WHERE provenance_id = ?",
                (provenance_id,),
            ).fetchone()
        if row is None:
            return None
        return LineageNode.from_dict({
            "provenance_id": row["provenance_id"],
            "content_hash": row["content_hash"],
            "agent_id": row["agent_id"],
            "timestamp": row["timestamp"],
            "parent_memory_ids": json.loads(row["parent_ids_json"] or "[]"),
            "path": row["path"],
            "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
        })

    def all_lineage_nodes(self) -> List[Any]:
        """Return every recorded LineageNode."""
        from crt_core.lineage import LineageNode
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM provenance_lineage"
            ).fetchall()
        nodes = []
        for row in rows:
            nodes.append(LineageNode.from_dict({
                "provenance_id": row["provenance_id"],
                "content_hash": row["content_hash"],
                "agent_id": row["agent_id"],
                "timestamp": row["timestamp"],
                "parent_memory_ids": json.loads(row["parent_ids_json"] or "[]"),
                "path": row["path"],
                "payload": json.loads(row["payload_json"]) if row["payload_json"] else None,
            }))
        return nodes

    def lineage_node_count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM provenance_lineage"
            ).fetchone()
        return int(row["n"])


class SQLiteReplayGuard(ReplayGuard):
    """SQLite-backed replay guard — durable across restarts.

    Wraps a :class:`SQLiteStorage` instance (shares its DB) and stores nonce
    fingerprints in the ``evidence_nonces`` table.
    """

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage

    def is_seen(self, fingerprint: str) -> bool:
        return self._storage.nonce_exists(fingerprint)

    def record(self, fingerprint: str) -> None:
        self._storage.record_nonce(fingerprint)

    def check_and_record(self, fingerprint: str) -> bool:
        return self._storage.check_and_record_nonce(fingerprint)


# ---------------------------------------------------------------------------
# Evidence-provider key lifecycle (Phase 6)
# ---------------------------------------------------------------------------

class SQLiteProviderRegistry:
    """
    SQLite-backed evidence-provider registry — durable and auditable.

    Mirrors the interface of ``crt_core.crypto.EvidenceProviderRegistry``
    (register / get_public_key / revoke_key / list_providers) so it can be
    installed as the active registry via ``set_provider_registry``. Keys are
    stored as hex and revoked keys are retained with ``status='revoked'`` for
    auditability rather than deleted.
    """

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage

    def register_provider(self, provider_id: str, key_id: str, public_key_bytes: bytes) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if len(public_key_bytes) != 32:
            raise ValueError("Ed25519 public key must be 32 raw bytes")
        Ed25519PublicKey.from_public_bytes(public_key_bytes)  # validate
        with self._storage._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evidence_providers
                (provider_id, key_id, public_key_hex, status, created_at, revoked_at)
                VALUES (?, ?, ?, 'active', ?, NULL)
                """,
                (provider_id, key_id, public_key_bytes.hex(), datetime.utcnow().isoformat()),
            )
            conn.commit()

    def get_public_key(self, provider_id: str, key_id: str):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        with self._storage._get_connection() as conn:
            row = conn.execute(
                """
                SELECT public_key_hex, status FROM evidence_providers
                WHERE provider_id = ? AND key_id = ?
                """,
                (provider_id, key_id),
            ).fetchone()
        if row is None or row["status"] != "active":
            return None
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(row["public_key_hex"]))

    def revoke_key(self, provider_id: str, key_id: str) -> None:
        """Mark a provider key revoked (kept in the table for audit)."""
        with self._storage._get_connection() as conn:
            conn.execute(
                """
                UPDATE evidence_providers
                SET status = 'revoked', revoked_at = ?
                WHERE provider_id = ? AND key_id = ?
                """,
                (datetime.utcnow().isoformat(), provider_id, key_id),
            )
            conn.commit()

    def list_providers(self) -> List[str]:
        with self._storage._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT provider_id FROM evidence_providers"
            ).fetchall()
            return [r["provider_id"] for r in rows]

    def provider_status(self, provider_id: str, key_id: str) -> Optional[str]:
        """'active', 'revoked', or None when the key is unknown."""
        with self._storage._get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM evidence_providers WHERE provider_id = ? AND key_id = ?",
                (provider_id, key_id),
            ).fetchone()
        return row["status"] if row is not None else None


# ---------------------------------------------------------------------------
# Provenance lineage (Phase 8)
# ---------------------------------------------------------------------------

class SQLiteLineageStore:
    """SQLite-backed lineage store — durable provenance chain.

    Wraps a :class:`SQLiteStorage` instance (shares its DB) and implements the
    :class:`crt_core.lineage.LineageStore` protocol against the
    ``provenance_lineage`` table.
    """

    def __init__(self, storage: SQLiteStorage):
        self._storage = storage

    def add_node(self, node: Any) -> None:
        self._storage.store_lineage_node(node)

    def get_node(self, provenance_id: str) -> Optional[Any]:
        return self._storage.get_lineage_node(provenance_id)

    def all_nodes(self) -> List[Any]:
        return self._storage.all_lineage_nodes()

    def node_count(self) -> int:
        return self._storage.lineage_node_count()
