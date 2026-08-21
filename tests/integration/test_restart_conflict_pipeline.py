"""
Phase A Freeze Repair - Restart to conflict resolution integration test.

Proves that restored durable trust affects the actual corrected conflict pipeline
after service restart, demonstrating the complete flow from outcome recording through
restart to conflict resolution consuming restored history.
"""
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from crt_service.storage import SQLiteStorage
from crt_service.trust_ledger import TrustLedger
from crt_core.trust_manager import TrustManager
from crt_core.pipeline import WritePipeline
from crt_core.conflict import ConflictResolutionEngine
from crt_core.schema import StampedUMF, ProvenanceInfo


class TestRestartToConflictPipeline:
    """Test that restored trust affects conflict resolution after restart."""
    
    def _packet(self, agent, value, ts, confidence=0.8, authority=0.9, domain="test_domain"):
        return StampedUMF(
            agent_id=agent,
            session_id="s",
            timestamp=datetime.fromisoformat(ts),
            confidence_score=confidence,
            assertion_payload={"k": value},
            provenance_id=f"p-{agent}-{value}",
            ingested_at=datetime.fromisoformat(ts),
            provenance_info=ProvenanceInfo(
                verified_confidence=confidence,
                authority_score=authority,
                source_type="tool_output",
                domain=domain,
            ),
        )
    
    @pytest.mark.asyncio
    async def test_restored_trust_affects_conflict_resolution(self, tmp_path):
        """
        Complete integration test: record outcome → restart → conflict resolution uses restored trust.
        
        Flow:
        1. Create file-backed SQLite DB
        2. Record authorized verified history for agent_A in domain_D
        3. Ensure agent_B remains neutral in domain_D
        4. Close first service/storage/pipeline instance
        5. Recreate corrected service/pipeline from same DB
        6. Confirm durable trust is rehydrated
        7. Submit conflict where historical trust is decisive
        8. Confirm conflict result reflects restored trust
        """
        db_path = tmp_path / "restart.db"
        domain = "test_domain"
        
        # Step 1: Create first service instance
        storage1 = SQLiteStorage(str(db_path))
        ledger1 = TrustLedger(storage1)
        trust1 = TrustManager()
        
        # Initialize migrations
        with storage1._get_connection() as conn:
            from crt_service.migrations import run_migrations
            run_migrations(conn)
        
        # Step 2: Record verified history for agent_A (high trust)
        for i in range(10):
            ledger1.record_verified_outcome(
                outcome_id=f"outcome_a_{i}",
                target_agent_id="agent_a",
                domain=domain,
                correct=True,
                verifier_identity="experiment_oracle",
            )
        
        # Step 3: Ensure agent_B remains neutral (no outcomes)
        # agent_b has no recorded outcomes, so trust should be neutral (0.5)
        
        # Hydrate trust manager with durable state
        ledger1.load_into(trust1)
        
        # Verify initial trust state
        trust_a_before = trust1.get_trust("agent_a", domain=domain, strict_domain=True)
        trust_b_before = trust1.get_trust("agent_b", domain=domain, strict_domain=True)
        
        assert trust_a_before > 0.5, f"agent_a should have high trust, got {trust_a_before}"
        assert trust_b_before == 0.5, f"agent_b should be neutral, got {trust_b_before}"
        
        # Step 4: Close first instance
        del storage1, ledger1, trust1
        
        # Step 5: Recreate service from same DB
        storage2 = SQLiteStorage(str(db_path))
        ledger2 = TrustLedger(storage2)
        trust2 = TrustManager()
        
        # Step 6: Confirm durable trust is rehydrated
        ledger2.load_into(trust2)
        
        trust_a_after = trust2.get_trust("agent_a", domain=domain, strict_domain=True)
        trust_b_after = trust2.get_trust("agent_b", domain=domain, strict_domain=True)
        
        assert trust_a_after > 0.5, f"agent_a trust should persist after restart, got {trust_a_after}"
        assert trust_b_after == 0.5, f"agent_b should remain neutral after restart, got {trust_b_after}"
        
        # Step 7: Create pipeline with restored trust
        # Use uncertainty_threshold=0.0 to make decision deterministic
        pipeline = WritePipeline(
            storage=storage2,
            trust_manager=trust2,
            conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.0),
        )
        
        # Step 8: Submit conflict where historical trust is decisive
        # Use identical recency/confidence/provenance so trust decides
        ref_time = datetime(2026, 8, 8, 12, 0, 0)
        
        # First commit agent_a (high trust) as existing
        result_a = await pipeline._locked_write(
            self._packet("agent_a", "value1", "2026-08-08T10:00:00", domain=domain),
            "test.path",
            "constructed",
            ref_time,
        )
        assert result_a.status == "committed"
        
        # Then try to overwrite with agent_b (neutral trust) at same timestamp
        result = await pipeline._locked_write(
            self._packet("agent_b", "value2", "2026-08-08T10:00:00", domain=domain),
            "test.path",
            "constructed",
            ref_time,
        )
        
        # Step 9: Verify conflict result reflects restored trust
        # agent_a should win due to higher historical trust
        assert result.status == "conflict_resolved", f"Expected conflict_resolved, got {result.status}"
        # Verify the incumbent is still agent_a (agent_b's overwrite failed)
        existing = storage2.get_existing("test.path")
        assert existing.agent_id == "agent_a", f"Expected agent_a, got {existing.agent_id}"
    
    @pytest.mark.asyncio
    async def test_unseen_domain_remains_neutral_after_restart(self, tmp_path):
        """Verify an unseen different domain remains neutral after restart."""
        db_path = tmp_path / "domain_isolation.db"
        domain_alpha = "domain_alpha"
        domain_beta = "domain_beta"
        
        # First instance: record history only in domain_alpha
        storage1 = SQLiteStorage(str(db_path))
        ledger1 = TrustLedger(storage1)
        trust1 = TrustManager()
        
        with storage1._get_connection() as conn:
            from crt_service.migrations import run_migrations
            run_migrations(conn)
        
        for i in range(5):
            ledger1.record_verified_outcome(
                outcome_id=f"outcome_{i}",
                target_agent_id="agent_a",
                domain=domain_alpha,
                correct=True,
                verifier_identity="experiment_oracle",
            )
        
        ledger1.load_into(trust1)
        
        # Verify domain_alpha has trust, domain_beta is neutral
        trust_alpha = trust1.get_trust("agent_a", domain=domain_alpha, strict_domain=True)
        trust_beta = trust1.get_trust("agent_a", domain=domain_beta, strict_domain=True)
        
        assert trust_alpha > 0.5
        assert trust_beta == 0.5
        
        # Close and reopen
        del storage1, ledger1, trust1
        
        storage2 = SQLiteStorage(str(db_path))
        ledger2 = TrustLedger(storage2)
        trust2 = TrustManager()
        ledger2.load_into(trust2)
        
        # Verify isolation persists after restart
        trust_alpha_restart = trust2.get_trust("agent_a", domain=domain_alpha, strict_domain=True)
        trust_beta_restart = trust2.get_trust("agent_a", domain=domain_beta, strict_domain=True)
        
        assert trust_alpha_restart > 0.5
        assert trust_beta_restart == 0.5, "Unseen domain should remain neutral after restart"