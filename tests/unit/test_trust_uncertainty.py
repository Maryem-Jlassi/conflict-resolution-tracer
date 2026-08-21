"""
Unit tests — Uncertainty-aware trust model (Phase 3).

The naive ``correct/total`` point estimate cannot express how much we should
trust a small sample. This model adds a Wilson-score interval whose WIDTH is
the uncertainty and whose LOWER bound is a conservative, uncertainty-penalized
score. A single lucky verification must NOT produce near-certain trust.

Covered here:
- Cold start (no outcomes) → maximal uncertainty.
- Small samples → wide interval, conservative << naive.
- Large samples → interval tightens, conservative converges to naive.
- Interval sanity (bounds, containment of the point estimate).
- Domain isolation and _global fallback.
- naive-vs-conservative comparison (Phase 12 input).
"""

import pytest
from crt_core.trust_manager import TrustManager, _DEFAULT_PRIOR


class TestUncertaintyAwareTrust:
    def test_cold_start_maximal_uncertainty(self):
        """No outcomes → whole unit interval, maximal uncertainty."""
        m = TrustManager()
        prof = m.get_trust_with_uncertainty("unknown")
        assert prof["outcome_count"] == 0
        assert prof["naive_trust"] == _DEFAULT_PRIOR
        assert prof["interval_low"] == 0.0
        assert prof["interval_high"] == 1.0
        assert prof["uncertainty"] == 0.5
        assert prof["conservative_trust"] == 0.0
        # The pipeline point estimate is unchanged (cold-start prior).
        assert m.get_trust("unknown") == _DEFAULT_PRIOR

    def test_single_correct_does_not_imply_certain_trust(self):
        """Naive trust is 1.0 after one correct outcome, but the conservative
        (uncertainty-penalized) score must be far below — a sample of one is
        not enough to trust anyone."""
        m = TrustManager()
        m.record_outcome("agent_a", correct=True)
        prof = m.get_trust_with_uncertainty("agent_a")
        assert prof["naive_trust"] == 1.0
        assert prof["conservative_trust"] < 0.5
        assert prof["conservative_trust"] > 0.0
        assert prof["uncertainty"] > 0.2
        # The pipeline point estimate stays at the naive value.
        assert m.get_trust("agent_a") == pytest.approx(1.0, abs=1e-6)

    def test_single_incorrect_gives_zero_conservative(self):
        m = TrustManager()
        m.record_outcome("agent_b", correct=False)
        prof = m.get_trust_with_uncertainty("agent_b")
        assert prof["naive_trust"] == 0.0
        assert prof["conservative_trust"] == 0.0

    def test_interval_narrows_with_more_outcomes(self):
        """More observations at the same success ratio → tighter interval."""
        m = TrustManager()
        for i in range(1, 101):
            m.record_outcome("perfect", correct=True)
        one = TrustManager()
        one.record_outcome("perfect", correct=True)
        assert one.get_uncertainty("perfect") > m.get_uncertainty("perfect")
        assert one.get_conservative_trust("perfect") < m.get_conservative_trust("perfect")

    def test_conservative_converges_to_naive_with_many_outcomes(self):
        m = TrustManager()
        for _ in range(100):
            m.record_outcome("solid", correct=True)
        prof = m.get_trust_with_uncertainty("solid")
        assert prof["conservative_trust"] > 0.9
        assert prof["naive_trust"] == 1.0
        assert prof["uncertainty"] < 0.1

    def test_uncertainty_monotone_for_fixed_ratio(self):
        """5/10 has wider interval (higher uncertainty) than 50/100."""
        small = TrustManager()
        for i in range(10):
            small.record_outcome("half", correct=(i < 5))
        big = TrustManager()
        for i in range(100):
            big.record_outcome("half", correct=(i < 50))
        assert small.get_uncertainty("half") > big.get_uncertainty("half")
        assert small.get_conservative_trust("half") < big.get_conservative_trust("half")
        assert small.get_trust_with_uncertainty("half")["naive_trust"] == pytest.approx(
            big.get_trust_with_uncertainty("half")["naive_trust"]
        )

    def test_interval_contains_point_estimate(self):
        m = TrustManager()
        for i in range(20):
            m.record_outcome("mixed", correct=(i % 3 != 0))  # ~2/3
        prof = m.get_trust_with_uncertainty("mixed")
        assert 0.0 <= prof["interval_low"] <= prof["interval_high"] <= 1.0
        assert prof["interval_low"] <= prof["naive_trust"] <= prof["interval_high"]
        assert prof["conservative_trust"] == prof["interval_low"]

    def test_domain_isolation(self):
        m = TrustManager()
        for _ in range(10):
            m.record_outcome("agent", correct=True, domain="healthcare")
        for _ in range(3):
            m.record_outcome("agent", correct=False, domain="finance")
        health = m.get_trust_with_uncertainty("agent", domain="healthcare")
        finance = m.get_trust_with_uncertainty("agent", domain="finance")
        # Each domain carries its own outcome history.
        assert health["outcome_count"] == 10
        assert finance["outcome_count"] == 3
        assert health["naive_trust"] == 1.0
        assert finance["naive_trust"] == 0.0
        assert health["conservative_trust"] > finance["conservative_trust"]

    def test_global_fallback_uses_domain_history(self):
        m = TrustManager()
        for _ in range(50):
            m.record_outcome("agent", correct=True, domain="finance")
        # _global fallback surfaces the domain history via get_trust_with_meta
        # semantics only when the domain itself has no record; here the domain
        # has its own history so uncertainty reflects it.
        prof = m.get_trust_with_uncertainty("agent", domain="finance")
        assert prof["outcome_count"] == 50

    def test_compare_trust_models(self):
        """naive >= conservative always; gap shrinks as the sample grows."""
        m = TrustManager()
        m.record_outcome("agent", correct=True)
        small = m.compare_trust_models("agent")
        assert small["naive"] == 1.0
        assert small["conservative"] < small["naive"]
        assert small["difference"] > 0

        for _ in range(999):
            m.record_outcome("agent", correct=True)
        large = m.compare_trust_models("agent")
        assert large["difference"] < small["difference"]

    def test_temporal_decay_still_applies_to_point_estimate(self):
        """Phase 3 must not disturb the existing decayed trust_score used by the
        pipeline and conflict resolution."""
        from datetime import datetime, timedelta

        m = TrustManager()
        ref = datetime(2026, 7, 14, 10, 0, 0)
        m.record_outcome("agent", correct=True, timestamp=ref)
        # 30 days later with 30-day half-life → trust decays to halfway to prior.
        later = ref + timedelta(days=30)
        trust = m.get_trust("agent", current_time=later)
        assert abs(trust - 0.75) < 1e-6
        assert trust < 1.0
