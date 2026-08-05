"""
Benchmark suite runner tests (Phase 11).

Verifies the unified benchmark suite runs deterministically, tags every
benchmark honestly, and produces a valid aggregated summary JSON.
"""

import json

import pytest

from benchmarks.run_all import run_benchmark_suite

BENCHMARKS = [
    "benchmark_a", "benchmark_b", "benchmark_c",
    "benchmark_d", "benchmark_e", "benchmark_f",
]

EXPECTED_TAGS = {
    "benchmark_a": "verification",
    "benchmark_b": "experiment",
    "benchmark_c": "verification",
    "benchmark_d": "verification",
    "benchmark_e": "experiment",
    "benchmark_f": "diagnostic",
}


class TestBenchmarkSuite:
    def test_suite_runs_all_benchmarks(self, tmp_path):
        summary = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        assert set(summary["benchmarks"].keys()) == set(BENCHMARKS)
        assert summary["mode"] == "quick"
        assert summary["reproducible"] is True
        assert summary["seed"] == 20260714
        assert summary["saved_to"] is not None

    def test_every_benchmark_tagged_and_populated(self, tmp_path):
        summary = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        for name in BENCHMARKS:
            payload = summary["benchmarks"][name]
            assert EXPECTED_TAGS[name] in payload.get("tags", [])
            assert payload["rows"] > 0

    def test_suite_is_deterministic_given_seed(self, tmp_path):
        a = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        b = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        for name in BENCHMARKS:
            assert a["benchmarks"][name] == b["benchmarks"][name]

    def test_summary_json_valid_and_loadable(self, tmp_path):
        summary = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        with open(summary["saved_to"], encoding="utf-8") as fh:
            loaded = json.load(fh)
        assert loaded["benchmarks"]["benchmark_f"]["tags"] == ["diagnostic"]
        assert loaded["benchmarks"]["benchmark_a"]["rows"] > 0

    def test_headline_metrics_present(self, tmp_path):
        summary = run_benchmark_suite(quick=True, output_dir=str(tmp_path))
        d = summary["benchmarks"]["benchmark_d"]
        assert "frozen_held_out_full_strict_accuracy" in d
        assert "corrected_diagnostic_v2_full_strict_accuracy" in d
        c = summary["benchmarks"]["benchmark_c"]
        assert "cold_start_LCM_accuracy" in c
        assert "graded_ambiguous_LCM_accuracy" in c
