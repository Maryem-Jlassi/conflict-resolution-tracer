"""
No-LCM Baseline System
Naive implementation for comparison: last-write-wins, no validation, no dedup.
"""

import sqlite3
from typing import Dict, Any, List
from datetime import datetime
from contextlib import contextmanager


class NoLCMStorage:
    """Naive storage - last write wins, no conflict resolution."""
    
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._shared_conn = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()
    
    def _init_db(self):
        """Simple table - no provenance, no archiving."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    path TEXT PRIMARY KEY,
                    agent_id TEXT,
                    value TEXT,
                    timestamp TEXT
                )
            """)
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        if self._shared_conn:
            yield self._shared_conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
    
    def write(self, path: str, agent_id: str, value: Any, timestamp: datetime = None):
        """Naive write - just overwrites, no validation."""
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO facts (path, agent_id, value, timestamp) VALUES (?, ?, ?, ?)",
                (path, agent_id, str(value), timestamp.isoformat())
            )
            conn.commit()
    
    def read(self, path: str) -> Dict[str, Any]:
        """Read current value at path."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM facts WHERE path = ?", (path,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    def get_all_context(self) -> str:
        """
        Naive context retrieval - raw concatenation of all facts.
        Simulates unfiltered chat history dump.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM facts ORDER BY timestamp")
            rows = cursor.fetchall()
            
            # Concatenate everything as a simple string
            context_parts = []
            for row in rows:
                context_parts.append(
                    f"{row['agent_id']} ({row['timestamp']}): {row['path']} = {row['value']}"
                )
            
            return "\n".join(context_parts) if context_parts else ""
    
    def get_write_count(self, path: str) -> int:
        """Count how many times a path was written (for lost update detection)."""
        # In naive system, we can't track this - overwrites lose history
        # This limitation is part of what we're benchmarking
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM facts WHERE path = ?", (path,))
            return cursor.fetchone()[0]
    
    def get_all_writes_for_path(self, path: str) -> List[Dict[str, Any]]:
        """
        Get all writes for a path - but naive system only has current value.
        Returns list for API compatibility, but will only have 1 item max.
        """
        result = self.read(path)
        return [result] if result else []
    
    def clear(self):
        """Clear all data."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM facts")
            conn.commit()
