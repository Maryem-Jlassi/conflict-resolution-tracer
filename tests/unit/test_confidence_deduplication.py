"""
Phase A Freeze Repair 2 - Confidence boundary and invariance tests.

Verifies that caller-controlled agreement fields do not influence trusted confidence,
and that confidence calculation relies only on middleware-owned evidence.
"""
from datetime import datetime

import pytest

from crt_core.confidence_engine import ConfidenceEngine, EvidenceRecord, EvidenceType


def _evidence(source_type="database", source_id="db://test", verified=True):
    return EvidenceRecord(
        evidence_type=EvidenceType(source_type),
        source_id=source_id,
        relevance_score=1.0,
        verified=verified,
    )


class TestConfidenceCallerInvariance:
    """Test that caller-controlled fields do not influence trusted confidence."""
    
    def test_caller_agreement_fields_do_not_affect_confidence(self):
        """Caller-reported agreeing_agents and total_independent_agents must not affect confidence.
        
        This ensures agreement counts remain audit-only and cannot be forged by agents.
        """
        engine = ConfidenceEngine()
        
        # Baseline with neutral agreement counts
        baseline = engine.calculate([_evidence()], agreeing_agents=0, total_independent_agents=0)
        
        # Attempt to inflate agreement via caller fields
        inflated = engine.calculate([_evidence()], agreeing_agents=999, total_independent_agents=1000)
        
        # Confidence must be identical - agreement fields are audit-only
        assert inflated == baseline, "Caller agreement fields must not affect trusted confidence"
    
    def test_caller_verified_memories_consistent_does_not_affect_confidence(self):
        """Caller-reported verified_memories_consistent must not affect confidence.
        
        This ensures memory consistency cannot be forged by agents.
        """
        engine = ConfidenceEngine()
        
        baseline = engine.calculate([_evidence()], verified_memories_consistent=None)
        false_claim = engine.calculate([_evidence()], verified_memories_consistent=False)
        true_claim = engine.calculate([_evidence()], verified_memories_consistent=True)
        
        # All must be identical - verification field is audit-only
        assert baseline == false_claim == true_claim, "Verified memories field must not affect trusted confidence"
    
    def test_trusted_evidence_drives_confidence_only(self):
        """Only middleware-owned evidence should drive trusted confidence calculation.
        
        Different evidence types should produce different confidence scores,
        but caller agreement/memory fields should have no effect regardless.
        """
        engine = ConfidenceEngine()
        
        high_authority = _evidence(source_type="database")
        low_authority = _evidence(source_type="agent_claim")
        
        # High authority evidence produces higher confidence
        high_conf = engine.calculate([high_authority], agreeing_agents=0, total_independent_agents=0)
        low_conf = engine.calculate([low_authority], agreeing_agents=999, total_independent_agents=1000)
        
        assert high_conf > low_conf, "Evidence type should affect confidence"
        
        # But inflated agreement should not inflate confidence for low authority
        low_conf_inflated = engine.calculate([low_authority], agreeing_agents=999, total_independent_agents=1000)
        assert low_conf_inflated == low_conf, "Agreement inflation must not rescue low authority evidence"


class TestEvidenceDeduplication:
    """Test that duplicate/correlated evidence does not inflate confidence via max() selection."""
    
    def test_exact_duplicate_evidence_no_confidence_increase(self):
        """Same evidence record twice should not increase confidence (max() selection)."""
        engine = ConfidenceEngine()
        
        # Single evidence baseline
        single_confidence = engine.calculate([_evidence()])
        
        # Exact duplicate
        duplicate_confidence = engine.calculate([_evidence(), _evidence()])
        
        assert duplicate_confidence == single_confidence
    
    def test_distinct_objects_same_canonical_evidence_no_increase(self):
        """Different record objects with same canonical evidence should not increase confidence."""
        engine = ConfidenceEngine()
        
        # Single evidence baseline
        single_confidence = engine.calculate([_evidence()])
        
        # Two distinct objects, same canonical source
        duplicate_confidence = engine.calculate([
            EvidenceRecord(
                evidence_type=EvidenceType.DATABASE,
                source_id="db://test",
                relevance_score=1.0,
                verified=True,
            ),
            EvidenceRecord(
                evidence_type=EvidenceType.DATABASE,
                source_id="db://test",
                relevance_score=1.0,
                verified=True,
            ),
        ])
        
        assert duplicate_confidence == single_confidence
    
    def test_max_authority_prevents_evidence_stacking(self):
        """Verify that max() selection prevents evidence stacking."""
        engine = ConfidenceEngine()
        
        # High authority evidence
        high_authority = _evidence()
        
        # Lower authority evidence
        low_authority = EvidenceRecord(
            evidence_type=EvidenceType.AGENT_CLAIM,
            source_id="agent",
            relevance_score=1.0,
            verified=False,
        )
        
        baseline = engine.calculate([high_authority])
        with_extra_low = engine.calculate([high_authority, low_authority])
        
        # Max authority should dominate, extra low authority shouldn't inflate
        assert with_extra_low == baseline
    
    def test_evidence_max_unchanged_by_quantity(self):
        """Adding more evidence of same or lower authority shouldn't increase max-based score."""
        engine = ConfidenceEngine()
        
        baseline = engine.calculate([_evidence()])
        with_multiple = engine.calculate([_evidence(), _evidence(), _evidence()])
        
        # Should be equal due to max() selection
        assert with_multiple == baseline


class TestIndependenceGroupSemantics:
    """Test independence group field exists and can be used for future deduplication."""
    
    def test_independence_group_field_exists(self):
        """Verify independence_group field exists in EvidenceRecord."""
        record = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test",
            relevance_score=1.0,
            verified=True,
            independence_group="group_a",
        )
        assert record.independence_group == "group_a"
    
    def test_independence_group_does_not_affect_max_authority(self):
        """Current implementation uses max() so independence groups don't affect evidence score."""
        engine = ConfidenceEngine()
        
        # Same evidence type, different independence groups
        group1 = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test1",
            relevance_score=1.0,
            verified=True,
            independence_group="group_a",
        )
        group2 = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test2",
            relevance_score=1.0,
            verified=True,
            independence_group="group_b",
        )
        
        # Both have same authority (DATABASE = 0.9), so max should give same result
        single = engine.calculate([group1])
        with_multiple = engine.calculate([group1, group2])
        
        # Should be equal since both have same authority
        assert single == with_multiple


class TestIndependenceGroupSemantics:
    """Test independence group field exists and can be used for deduplication."""
    
    def test_independence_group_field_exists(self):
        """Verify independence_group field exists in EvidenceRecord."""
        record = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test",
            relevance_score=1.0,
            verified=True,
            independence_group="group_a",
        )
        assert record.independence_group == "group_a"
    
    def test_independence_group_does_not_affect_max_authority(self):
        """Current implementation uses max() so independence groups don't affect evidence score."""
        engine = ConfidenceEngine()
        
        # Same evidence type, different independence groups
        group1 = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test1",
            relevance_score=1.0,
            verified=True,
            independence_group="group_a",
        )
        group2 = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            source_id="db://test2",
            relevance_score=1.0,
            verified=True,
            independence_group="group_b",
        )
        
        # Both have same authority (DATABASE = 0.9), so max should give same result
        single = engine.calculate([group1])
        with_multiple = engine.calculate([group1, group2])
        
        # Should be equal since both have same authority
        assert single == with_multiple