"""
End-to-end provenance lineage tests (Phase 8).

Verifies the WritePipeline records first-class lineage nodes for:
- direct commits
- derived writes (parent_memory_ids supplied by the caller)
- conflict resolution (winner bound to incumbent + challenger, loser retained)
- unresolved conflicts (pending alternative bound to incumbent)
"""

import pytest
import asyncio
import os
from datetime import datetime, timedelta
import tempfile

from crt_core.pipeline import WritePipeline
from crt_core.conflict import ConflictResolutionEngine
from crt_core.trust_manager import TrustManager
from crt_core.locking import AsyncLockManager
from crt_core.loop_detection import LoopDetector
from crt_core.lineage import walk_chain
from crt_service.storage import SQLiteLineageStore, SQLiteStorage


class TestLineagePipeline:
    @pytest.fixture
    def temp_db_path(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def pipeline(self, temp_db_path):
        storage = SQLiteStorage(temp_db_path)
        trust = TrustManager()
        conflict_engine = ConflictResolutionEngine(uncertainty_threshold=0.0)
        lock_manager = AsyncLockManager()
        loop_detector = LoopDetector(rate_threshold=1000)
        pipeline = WritePipeline(
            storage=storage,
            trust_manager=trust,
            conflict_engine=conflict_engine,
            lock_manager=lock_manager,
            loop_detector=loop_detector,
        )
        yield pipeline, storage, trust

    def _write(self, pipeline_obj, agent, payload, *, ts=None, parents=None):
        raw = {
            "agent_id": agent,
            "session_id": "test_session",
            "timestamp": ts or datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": payload,
        }
        return pipeline_obj.process(raw, parent_memory_ids=parents)

    @pytest.mark.asyncio
    async def test_direct_commit_records_lineage(self, pipeline):
        pipeline_obj, storage, _ = pipeline
        result = await self._write(pipeline_obj, "agent_a", {"a.path": "v1"})
        assert result.status == "committed"
        assert storage.lineage_node_count() == 1
        node = storage.get_lineage_node(result.committed.provenance_id)
        assert node is not None
        assert node.content_hash == result.committed.content_hash
        walk = walk_chain(SQLiteLineageStore(storage), result.committed.provenance_id)
        assert walk.ok

    @pytest.mark.asyncio
    async def test_derived_write_chains_to_parent(self, pipeline):
        pipeline_obj, storage, _ = pipeline
        r1 = await self._write(pipeline_obj, "agent_a", {"base": "v1"})
        r2 = await self._write(
            pipeline_obj, "agent_b", {"derived": "v1"},
            parents=[r1.committed.provenance_id],
        )
        assert r2.status == "committed"
        walk = walk_chain(SQLiteLineageStore(storage), r2.committed.provenance_id)
        assert walk.ok
        assert walk.node_count == 2
        assert walk.edge_count == 1

    @pytest.mark.asyncio
    async def test_conflict_winner_chain_binds_both_participants(self, pipeline):
        pipeline_obj, storage, trust = pipeline
        for _ in range(10):
            trust.record_outcome("agent_a", correct=True)

        r1 = await self._write(pipeline_obj, "agent_a", {"c.path": "va"})
        r2 = await self._write(pipeline_obj, "agent_b", {"c.path": "vb"})
        assert r2.status == "conflict_resolved"

        winner = r2.committed
        loser = r2.conflict.loser
        # Both participants recorded.
        assert storage.lineage_node_count() >= 2

        walk = walk_chain(SQLiteLineageStore(storage), winner.provenance_id)
        assert walk.ok
        assert walk.node_count == 2
        assert walk.edge_count == 1
        assert loser.provenance_id in walk.visited

    @pytest.mark.asyncio
    async def test_unresolved_pending_bound_to_incumbent(self, pipeline):
        storage = SQLiteStorage(":memory:")
        trust = TrustManager()
        pipeline_obj = WritePipeline(
            storage=storage,
            trust_manager=trust,
            conflict_engine=ConflictResolutionEngine(uncertainty_threshold=1.0),
            lock_manager=AsyncLockManager(),
            loop_detector=LoopDetector(rate_threshold=1000),
        )

        r1 = await self._write(pipeline_obj, "agent_a", {"u.path": "va"})
        r2 = await self._write(pipeline_obj, "agent_b", {"u.path": "vb"})
        assert r2.status == "unresolved"

        # The pending alternative (not the incumbent) is bound to the incumbent.
        pending = storage.get_pending_conflicts("u.path")
        assert len(pending) == 1
        store = SQLiteLineageStore(storage)
        walk = walk_chain(store, pending[0].provenance_id)
        assert walk.ok
        assert walk.node_count == 2
        assert r1.committed.provenance_id in walk.visited
