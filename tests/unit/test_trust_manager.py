"""
Unit tests for TrustManager API methods.

Tests cover:
- Outcome counts per agent and domain
- Domain isolation (global vs domain-specific)
- Cold-start defaults
- get_trust_with_meta functionality
- get_outcome_summary functionality
"""

import math
import pytest
from datetime import datetime, timedelta
from lcm_core.trust_manager import TrustManager, _DEFAULT_PRIOR


class TestTrustManagerAPI:
    """Test TrustManager public API methods."""
    
    def test_cold_start_default_trust(self):
        """Test that new agents return default prior trust."""
        manager = TrustManager()
        
        # Unknown agent should return cold-start prior
        trust = manager.get_trust("unknown_agent")
        assert trust == _DEFAULT_PRIOR
        
        # Unknown agent with specific domain should also return prior
        trust_domain = manager.get_trust("unknown_agent", domain="healthcare")
        assert trust_domain == _DEFAULT_PRIOR
    
    def test_get_outcome_counts_new_agent(self):
        """Test get_outcome_counts for new agent returns zeros."""
        manager = TrustManager()
        
        counts = manager.get_outcome_counts("new_agent")
        assert counts == {"total": 0, "correct": 0, "incorrect": 0}
        
        # Test with domain
        counts_domain = manager.get_outcome_counts("new_agent", domain="finance")
        assert counts_domain == {"total": 0, "correct": 0, "incorrect": 0}
    
    def test_get_outcome_counts_after_records(self):
        """Test get_outcome_counts reflects recorded outcomes."""
        manager = TrustManager()
        
        # Record some outcomes (default domain="_global"; no double-count)
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_a", correct=False)
        
        counts = manager.get_outcome_counts("agent_a")
        # record_outcome(domain="_global") updates _global once (no double-count)
        assert counts["total"] == 3
        assert counts["correct"] == 2
        assert counts["incorrect"] == 1
    
    @pytest.mark.parametrize("domain,correct_count,incorrect_count", [
        ("healthcare", 5, 2),
        ("finance", 3, 1),
        ("_global", 8, 3),  # Global should aggregate all
    ])
    def test_domain_isolation(self, domain, correct_count, incorrect_count):
        """Test that domain-specific outcomes are isolated."""
        manager = TrustManager()
        
        # Record outcomes in different domains
        for _ in range(5):
            manager.record_outcome("agent_a", correct=True, domain="healthcare")
        for _ in range(2):
            manager.record_outcome("agent_a", correct=False, domain="healthcare")
        
        for _ in range(3):
            manager.record_outcome("agent_a", correct=True, domain="finance")
        for _ in range(1):
            manager.record_outcome("agent_a", correct=False, domain="finance")
        
        # Check domain-specific counts
        if domain == "healthcare":
            counts = manager.get_outcome_counts("agent_a", domain="healthcare")
            assert counts["correct"] == 5
            assert counts["incorrect"] == 2
        elif domain == "finance":
            counts = manager.get_outcome_counts("agent_a", domain="finance")
            assert counts["correct"] == 3
            assert counts["incorrect"] == 1
        elif domain == "_global":
            counts = manager.get_outcome_counts("agent_a", domain="_global")
            # Global aggregates all domains (5+3=8 correct, 2+1=3 incorrect)
            assert counts["correct"] == 8
            assert counts["incorrect"] == 3
    
    def test_get_trust_with_meta_new_agent(self):
        """Test get_trust_with_meta for new agent."""
        manager = TrustManager()
        
        meta = manager.get_trust_with_meta("new_agent")
        assert meta["trust_score"] == _DEFAULT_PRIOR
        assert meta["outcome_count"] == 0
        assert meta["correct_count"] == 0
        assert meta["incorrect_count"] == 0
        assert meta["domain"] == "_global"
    
    def test_get_trust_with_meta_after_records(self):
        """Test get_trust_with_meta reflects recorded outcomes."""
        manager = TrustManager()
        
        # Record outcomes (default domain="_global"; no double-count)
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_a", correct=False)
        manager.record_outcome("agent_a", correct=True)
        
        meta = manager.get_trust_with_meta("agent_a")
        # record_outcome(domain="_global") updates _global once (no double-count)
        assert meta["trust_score"] == pytest.approx(2/3)  # 2 correct out of 3 total
        assert meta["outcome_count"] == 3
        assert meta["correct_count"] == 2
        assert meta["incorrect_count"] == 1
        assert meta["domain"] == "_global"
    
    def test_get_trust_with_meta_domain_specific(self):
        """Test get_trust_with_meta with domain parameter."""
        manager = TrustManager()
        
        # Record outcomes in specific domain
        for _ in range(4):
            manager.record_outcome("agent_a", correct=True, domain="healthcare")
        for _ in range(2):
            manager.record_outcome("agent_a", correct=False, domain="healthcare")
        
        meta = manager.get_trust_with_meta("agent_a", domain="healthcare")
        assert meta["trust_score"] == pytest.approx(4/6)
        assert meta["outcome_count"] == 6
        assert meta["correct_count"] == 4
        assert meta["incorrect_count"] == 2
        assert meta["domain"] == "healthcare"
    
    def test_get_outcome_summary_new_agent(self):
        """Test get_outcome_summary for new agent."""
        manager = TrustManager()
        
        summary = manager.get_outcome_summary("new_agent")
        assert summary["total"] == 0
        assert summary["correct"] == 0
        assert summary["trust"] == _DEFAULT_PRIOR
    
    def test_get_outcome_summary_after_records(self):
        """Test get_outcome_summary reflects recorded outcomes."""
        manager = TrustManager()
        
        # Record outcomes (default domain="_global"; no double-count)
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_a", correct=False)
        
        summary = manager.get_outcome_summary("agent_a")
        # record_outcome(domain="_global") updates _global once (no double-count)
        assert summary["total"] == 3
        assert summary["correct"] == 2
        assert summary["trust"] == pytest.approx(2/3)
    
    def test_domain_fallback_to_global(self):
        """Test that domains without history fall back to global."""
        manager = TrustManager()
        
        # Record in a specific domain to test fallback
        for _ in range(3):
            manager.record_outcome("agent_a", correct=True, domain="healthcare")
        
        # Query different domain (should fall back to global which has healthcare data)
        trust = manager.get_trust("agent_a", domain="finance")
        assert trust == pytest.approx(1.0)  # 3/3 correct
        
        counts = manager.get_outcome_counts("agent_a", domain="finance")
        # When domain doesn't exist, falls back to global
        # Global contains the aggregated records (3)
        assert counts["total"] == 3
        assert counts["correct"] == 3
    
    def test_temporal_decay_basic(self):
        """Test basic temporal decay functionality."""
        manager = TrustManager(half_life_days=1.0)  # 1 day half-life for testing
        
        # Record outcome
        manager.record_outcome("agent_a", correct=True)
        
        # Trust should be 1.0 immediately (approx: micro-decay between utcnow calls)
        trust_now = manager.get_trust("agent_a", current_time=datetime.utcnow())
        assert trust_now == pytest.approx(1.0)
        
        # After long time, trust should decay toward 0.5
        future_time = datetime.utcnow() + timedelta(days=10)
        trust_future = manager.get_trust("agent_a", current_time=future_time)
        assert 0.5 < trust_future < 1.0  # Should decay but not reach neutral

    @pytest.mark.parametrize("elapsed", [
        timedelta(0), timedelta(milliseconds=500), timedelta(milliseconds=999),
        timedelta(seconds=1), timedelta(hours=1), timedelta(days=30),
    ])
    def test_explicit_as_of_decay_boundaries(self, elapsed):
        manager = TrustManager(half_life_days=30)
        observed = datetime(2026, 1, 1)
        manager.record_outcome("agent_a", correct=True, observed_at=observed)
        expected = 0.5 + 0.5 * math.exp(
            -math.log(2) * elapsed.total_seconds() / timedelta(days=30).total_seconds())
        assert manager.get_trust("agent_a", as_of=observed + elapsed) == pytest.approx(expected)

    def test_explicit_as_of_is_scheduler_independent(self, monkeypatch):
        manager = TrustManager(immediate_read_grace_seconds=1.0)
        observed = datetime(2026, 1, 1)
        manager.record_outcome("agent_a", correct=True, observed_at=observed)
        first = manager.get_trust("agent_a", as_of=observed + timedelta(milliseconds=500))
        second = manager.get_trust("agent_a", as_of=observed + timedelta(milliseconds=500))
        assert first == second

    def test_timestamp_aliases_are_mutually_exclusive(self):
        manager = TrustManager()
        now = datetime.utcnow()
        with pytest.raises(ValueError):
            manager.record_outcome("a", True, timestamp=now, observed_at=now)
        with pytest.raises(ValueError):
            manager.get_trust("a", current_time=now, as_of=now)
    
    def test_multiple_agents_isolation(self):
        """Test that different agents have independent trust scores."""
        manager = TrustManager()
        
        # Give agent_a good history
        for _ in range(10):
            manager.record_outcome("agent_a", correct=True)
        
        # Give agent_b bad history
        for _ in range(10):
            manager.record_outcome("agent_b", correct=False)
        
        # Check isolation
        trust_a = manager.get_trust("agent_a")
        trust_b = manager.get_trust("agent_b")
        
        # approx: microseconds of wall-clock decay leave scores just off 1.0/0.0
        assert trust_a == pytest.approx(1.0)
        assert trust_b == pytest.approx(0.0, abs=1e-9)
        assert trust_a != trust_b
    
    def test_get_agent_summary(self):
        """Test get_agent_summary returns full agent information."""
        manager = TrustManager()
        
        # Record outcomes in multiple domains
        for _ in range(3):
            manager.record_outcome("agent_a", correct=True, domain="healthcare")
        for _ in range(2):
            manager.record_outcome("agent_a", correct=False, domain="healthcare")
        
        for _ in range(1):
            manager.record_outcome("agent_a", correct=True, domain="finance")
        
        summary = manager.get_agent_summary("agent_a")
        assert summary is not None
        assert summary["agent_id"] == "agent_a"
        assert "domains" in summary
        assert "healthcare" in summary["domains"]
        assert "finance" in summary["domains"]
        assert "_global" in summary["domains"]
    
    def test_all_agents(self):
        """Test all_agents returns list of tracked agent IDs."""
        manager = TrustManager()
        
        # Initially empty
        assert manager.all_agents() == []
        
        # Add some agents
        manager.record_outcome("agent_a", correct=True)
        manager.record_outcome("agent_b", correct=False)
        manager.record_outcome("agent_c", correct=True)
        
        agents = manager.all_agents()
        assert len(agents) == 3
        assert "agent_a" in agents
        assert "agent_b" in agents
        assert "agent_c" in agents
    
    def test_build_trust_table(self):
        """Test build_trust_table creates trust score mapping."""
        manager = TrustManager()
        
        # Setup agents with different trust levels
        for _ in range(5):
            manager.record_outcome("high_trust", correct=True)
        
        for _ in range(5):
            manager.record_outcome("low_trust", correct=False)
        
        for _ in range(3):
            manager.record_outcome("medium_trust", correct=True)
        for _ in range(2):
            manager.record_outcome("medium_trust", correct=False)
        
        table = manager.build_trust_table(["high_trust", "low_trust", "medium_trust"])
        
        assert table["high_trust"] == pytest.approx(1.0)
        assert table["low_trust"] == pytest.approx(0.0, abs=1e-9)
        assert table["medium_trust"] == pytest.approx(0.6)
    
    def test_get_outcome_counts_fallback_behavior(self):
        """Test that domain-specific counts fall back to global when domain doesn't exist."""
        manager = TrustManager()
        
        # Record in a specific domain
        for _ in range(4):
            manager.record_outcome("agent_a", correct=True, domain="healthcare")
        
        # Query non-existent domain
        counts = manager.get_outcome_counts("agent_a", domain="nonexistent")
        # Should fall back to global
        # Global contains the aggregated records (4)
        assert counts["total"] == 4
        assert counts["correct"] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
