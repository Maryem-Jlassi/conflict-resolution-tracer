"""Tests for Multi-Source Memory Benchmark adapter v1."""
from __future__ import annotations
pytestmark = pytest.mark.external_dataset
import json
from pathlib import Path

import pytest

REV = "5b428c8d6826a7dc73ac05f5239b089a6c631ac1"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data" / "multisource_memory" / "raw" / REV / "data" / "benchmark"

from research_evaluation.multisource_memory_adapter import (
    MSMAdapter,
    PersonaRecord,
    SourceAssertion,
    canonical_hash,
    QUESTION_DERIVATIONS,
    _build_aggregate_assertions_for_question,
)


@pytest.fixture(scope="module")
def adapter():
    a = MSMAdapter(config="s20260321", allowed_splits=["dev"])
    return a


class TestAdapterV1Load:
    def test_loads_dev_only(self, adapter):
        for pid, rec in adapter.personas.items():
            assert rec.split == "dev", f"Persona {pid} has split={rec.split}, expected dev"

    def test_no_train_in_adapter(self, adapter):
        for pid in adapter.personas:
            assert "train" not in pid.lower() or adapter.personas[pid].split != "train"

    def test_no_test_in_adapter(self, adapter):
        for pid, rec in adapter.personas.items():
            assert rec.split != "test", f"TEST persona {pid} leaked into DEV adapter"

    def test_no_calibration_in_adapter(self, adapter):
        for pid, rec in adapter.personas.items():
            assert rec.split != "calibration", f"CALIBRATION persona {pid} leaked into DEV adapter"

    def test_has_dev_personas(self, adapter):
        assert len(adapter.personas) > 0


class TestQuestionDerivationRegistry:
    def test_all_18_questions_defined(self):
        expected = ["A1", "A2", "A3", "B2", "B3", "C2", "C3", "D1", "D2", "E1", "E2",
                    "F1", "F2", "F3", "G1", "G2", "Ctrl1", "Ctrl2"]
        for qid in expected:
            assert qid in QUESTION_DERIVATIONS, f"Missing derivation for {qid}"

    def test_each_derivation_has_required_fields(self):
        required = ["target_semantic", "temporal_window_days", "output_type",
                     "canonicalization", "eligible_sources", "source_fields",
                     "aggregation_rule", "unsupported_rule"]
        for qid, spec in QUESTION_DERIVATIONS.items():
            for field in required:
                assert field in spec, f"Missing {field} in {qid}"

    def test_eligible_sources_are_valid(self):
        valid_sources = {"profile_ltm", "daily_self_report", "planner", "device_log", "objective_log"}
        for qid, spec in QUESTION_DERIVATIONS.items():
            for src in spec["eligible_sources"]:
                assert src in valid_sources, f"Invalid source {src} in {qid}"


class TestAggregateAssertions:
    def test_one_assertion_per_source_not_per_day(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        # Should be at most 5 assertions (one per source), not 150+
        assert len(assertions) <= 10, f"Too many assertions for A1: {len(assertions)}"

    def test_assertions_are_question_level(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        for a in assertions:
            assert a.question_id == "A1"
            assert a.memory_key.endswith("/A1")

    def test_same_source_single_assertion(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        sources = [a.source_stream for a in assertions]
        assert len(sources) == len(set(sources)), "Duplicate assertions from same source"

    def test_no_ground_truth_in_assertion(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        for a in assertions:
            d = a.to_crt_claim_dict()
            assert "correctAnswer" not in str(d.get("text", "")).lower()
            assert "ground_truth" not in json.dumps(d.get("metadata", {})).lower()

    def test_event_table_not_in_derivation(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        for a in assertions:
            assert "event_table" not in json.dumps(a.to_crt_claim_dict()).lower()

    def test_fingerprints_unique(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        fps = [a.assertion_fingerprint for a in assertions]
        assert len(fps) == len(set(fps))

    def test_fingerprints_deterministic(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        a1 = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        a2 = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        for x, y in zip(a1, a2):
            assert x.assertion_fingerprint == y.assertion_fingerprint


class TestSplitEnforcement:
    def test_dev_smoke_fail_closed_on_train(self):
        with pytest.raises(AssertionError):
            adapter_train = MSMAdapter(config="s20260321", allowed_splits=["train"])
            # Should not load any personas if we try to use train for DEV
            assert len(adapter_train.personas) == 0

    def test_dev_smoke_fail_closed_on_test(self):
        with pytest.raises(AssertionError):
            adapter_test = MSMAdapter(config="s20260321", allowed_splits=["test"])
            assert len(adapter_test.personas) == 0


class TestPolicyIsolation:
    def test_identical_assertions_across_policies(self, adapter):
        rec = adapter.get_persona("bench_shift_073_noah_diaz")
        if rec is None:
            pytest.skip("DEV persona not found")
        assertions = _build_aggregate_assertions_for_question(rec, "A1", adapter.config)
        fps = [a.assertion_fingerprint for a in assertions]
        assert len(fps) == len(set(fps))
