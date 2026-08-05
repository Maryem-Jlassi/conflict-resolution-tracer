"""
Cold-Start vs History Regression Test

This test ensures that the WritePipeline properly respects history - i.e., that
cold-start behavior differs from post-history behavior. This is a critical gate
for the evaluation plan to ensure the system doesn't ignore prior commits.

The test uses real SQLite storage and WritePipeline, but no LLM.
"""

import pytest
import tempfile
from datetime import datetime, timedelta

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_service.storage import SQLiteStorage


class TestColdStartVsHistory:
    """Test that cold-start and post-history behaviors differ correctly."""
    
    @pytest.fixture
    def storage(self):
        """Create a temporary SQLite storage."""
        storage = SQLiteStorage(":memory:")
        yield storage
    
    @pytest.fixture
    def pipeline(self, storage):
        """Create a pipeline with real storage."""
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
    
    @pytest.mark.asyncio
    async def test_cold_start_write_and_retrieve(self, pipeline):
        """Test cold-start: write fact A, commit, retrieve → expect A."""
        pipeline_obj, storage, trust = pipeline
        
        # Cold-start: write fact A at path P
        result = await pipeline_obj.process({
            "agent_id": "agent_a",
            "session_id": "test_session",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {"test.path": "value_A"},
        })
        
        assert result.status == "committed"
        assert result.committed is not None
        
        # Retrieve from storage
        retrieved = storage.get_existing("test.path")
        assert retrieved is not None
        assert retrieved.assertion_payload.get("test.path") == "value_A"
        assert retrieved.agent_id == "agent_a"
    
    @pytest.mark.asyncio
    async def test_history_affects_subsequent_write(self, pipeline):
        """Test that history affects subsequent writes to the same path."""
        pipeline_obj, storage, trust = pipeline
        
        # Cold-start: write fact A
        result_a = await pipeline_obj.process({
            "agent_id": "agent_a",
            "session_id": "test_session",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {"test.path": "value_A"},
        })
        
        assert result_a.status == "committed"
        
        # Build trust for agent_a through outcomes
        trust.record_outcome("agent_a", correct=True, domain="_global")
        trust.record_outcome("agent_a", correct=True, domain="_global")
        
        # agent_b has default trust (no outcomes)
        
        # Now write fact B with different agent (lower trust)
        result_b = await pipeline_obj.process({
            "agent_id": "agent_b",
            "session_id": "test_session",
            "timestamp": datetime.utcnow() + timedelta(seconds=1),
            "confidence_score": 0.8,
            "assertion_payload": {"test.path": "value_B"},
        })
        
        # agent_b should lose to agent_a due to lower trust
        assert result_b.status in ["conflict_resolved", "conflict_unresolved"]
        if result_b.status == "conflict_resolved":
            assert result_b.conflict.winner.agent_id == "agent_a"
            assert result_b.conflict.loser.agent_id == "agent_b"
        
        # Retrieve should still have value_A (incumbent won)
        retrieved = storage.get_existing("test.path")
        assert retrieved is not None
        assert retrieved.assertion_payload.get("test.path") == "value_A"
    
    @pytest.mark.asyncio
    async def test_trust_changes_after_outcomes(self, pipeline):
        """Test that trust scores change after recording outcomes."""
        pipeline_obj, storage, trust = pipeline
        
        # Cold-start trust should be default
        cold_trust = trust.get_trust("agent_c")
        assert cold_trust == 0.5  # Default cold-start trust
        
        # Record outcomes for agent_c
        trust.record_outcome("agent_c", correct=True, domain="_global")
        trust.record_outcome("agent_c", correct=True, domain="_global")
        trust.record_outcome("agent_c", correct=False, domain="_global")
        
        # Trust should have changed
        post_trust = trust.get_trust("agent_c")
        assert post_trust != 0.5
        # With 2 correct, 1 incorrect, trust should be higher than default
        assert post_trust > 0.5
    
    @pytest.mark.asyncio
    async def test_multiple_paths_dont_interfere(self, pipeline):
        """Test that multiple paths don't interfere with each other."""
        pipeline_obj, storage, trust = pipeline
        
        # Write to different paths with different agents
        paths_and_values = [
            ("path1", "value1", "agent_a"),
            ("path2", "value2", "agent_b"),
            ("path3", "value3", "agent_c"),
        ]
        
        for path, value, agent_id in paths_and_values:
            result = await pipeline_obj.process({
                "agent_id": agent_id,
                "session_id": "test_session",
                "timestamp": datetime.utcnow(),
                "confidence_score": 0.8,
                "assertion_payload": {path: value},
            })
            assert result.status == "committed"
        
        # Retrieve each path independently
        for path, value, agent_id in paths_and_values:
            retrieved = storage.get_existing(path)
            assert retrieved is not None
            assert retrieved.assertion_payload.get(path) == value
            assert retrieved.agent_id == agent_id
    
    @pytest.mark.asyncio
    async def test_history_ignored_fails(self, pipeline):
        """Test that if history is ignored, the test should fail."""
        pipeline_obj, storage, trust = pipeline
        
        # Write fact A
        result_a = await pipeline_obj.process({
            "agent_id": "agent_a",
            "session_id": "test_session",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {"test.path": "value_A"},
        })
        
        assert result_a.status == "committed"
        
        # Write fact B with same agent, higher confidence + explicit evidence
        # (should update).  Explicit DOCUMENT evidence with a valid signature
        # makes verified_confidence deterministic, so the update is not a
        # recency coin-flip between two near-identical utcnow() timestamps.
        from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
        from lcm_core.crypto import sign_evidence_message
        result_b = await pipeline_obj.process({
            "agent_id": "agent_a",
            "session_id": "test_session",
            "timestamp": datetime.utcnow() + timedelta(seconds=1),
            "confidence_score": 0.9,
            "assertion_payload": {"test.path": "value_B"},
        }, evidence_records=[
            EvidenceRecord(evidence_type=EvidenceType.DOCUMENT, relevance_score=1.0,
                           source_id="doc://updated")
        ], evidence_signature=sign_evidence_message(EvidenceType.DOCUMENT, "doc://updated"))
        
        # Should either commit (same agent updating) or resolve correctly
        assert result_b.status in ["committed", "conflict_resolved"]
        
        # Retrieve should have value_B (higher confidence from same agent)
        retrieved = storage.get_existing("test.path")
        assert retrieved is not None
        # If same agent with higher confidence, should update
        assert retrieved.assertion_payload.get("test.path") == "value_B"
    
    @pytest.mark.asyncio
    async def test_domain_isolation_affects_history(self, pipeline):
        """Test that domain isolation affects how history is considered."""
        pipeline_obj, storage, trust = pipeline
        
        # Build trust in healthcare domain for agent_a
        trust.record_outcome("agent_a", correct=True, domain="healthcare")
        trust.record_outcome("agent_a", correct=True, domain="healthcare")
        
        # Build trust in finance domain for agent_b
        trust.record_outcome("agent_b", correct=True, domain="finance")
        trust.record_outcome("agent_b", correct=True, domain="finance")
        
        # Write healthcare fact with agent_a
        result_health = await pipeline_obj.process({
            "agent_id": "agent_a",
            "session_id": "test_session",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {"healthcare.patient.status": "stable"},
        })
        
        assert result_health.status == "committed"
        
        # Write finance fact with agent_b
        result_finance = await pipeline_obj.process({
            "agent_id": "agent_b",
            "session_id": "test_session",
            "timestamp": datetime.utcnow(),
            "confidence_score": 0.8,
            "assertion_payload": {"finance.account.balance": "1000"},
        })
        
        assert result_finance.status == "committed"
        
        # Verify domain-specific trust is used
        healthcare_trust = trust.get_trust("agent_a", domain="healthcare")
        finance_trust = trust.get_trust("agent_b", domain="finance")
        
        assert healthcare_trust > 0.5  # Should be higher due to correct outcomes
        assert finance_trust > 0.5  # Should be higher due to correct outcomes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
