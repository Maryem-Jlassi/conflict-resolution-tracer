"""
Figure-data cache (results/figures_data.json, schema v2) tests.

Covers provenance envelope construction, stale-schema rejection, the strict
cache-only path, and loading from a valid cache.
"""

import json

import pytest

import results.publication_figures as pf


def _write_cache(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _v2_cache(tmp_path, **overrides):
    payload = {
        "schema_version": 2,
        "generated_at": "2026-08-03T00:00:00",
        "git_commit": "abc123",
        "config": {"include_real_agent": False},
        "sources": {},
        "benchmark_a": [1, 2],
        "benchmark_b": [],
        "benchmark_c": [],
        "benchmark_d": [],
        "benchmark_e": [],
        "experiments": [],
    }
    payload.update(overrides)
    return _write_cache(tmp_path / "figures_data.json", payload)


def test_build_cache_payload_provenance_envelope():
    payload = pf.build_cache_payload({"benchmark_a": [{"x": 1}]}, include_real_agent=False)
    assert payload["schema_version"] == 2
    assert "benchmark_versions" in payload
    assert "sources" in payload
    for key in ["benchmark_a", "benchmark_b", "benchmark_c", "benchmark_d", "benchmark_e"]:
        assert key in payload["benchmark_versions"]
        assert key in payload
    assert payload["config"]["include_real_agent"] is False
    assert payload["benchmark_a"] == [{"x": 1}]


def test_read_cache_missing_returns_none(tmp_path):
    assert pf._read_cache(tmp_path / "nope.json") is None


def test_read_cache_stale_v1_rejected(tmp_path):
    p = _write_cache(tmp_path / "old.json", {"schema_version": 1, "benchmark_a": []})
    assert pf._read_cache(p) is None


def test_read_cache_v2_accepted(tmp_path):
    p = _v2_cache(tmp_path)
    data = pf._read_cache(p)
    assert data is not None
    assert data["schema_version"] == 2


def test_load_data_strict_raises_when_cache_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE", tmp_path / "missing.json")
    with pytest.raises(SystemExit):
        pf.load_data(use_cache=True, force=False, include_real_agent=False, strict=True)


def test_load_data_strict_raises_when_cache_stale(tmp_path, monkeypatch):
    p = _write_cache(tmp_path / "old.json", {"schema_version": 1, "benchmark_a": []})
    monkeypatch.setattr(pf, "CACHE", p)
    with pytest.raises(SystemExit):
        pf.load_data(use_cache=True, force=False, include_real_agent=False, strict=True)


def test_load_data_from_valid_cache(tmp_path, monkeypatch):
    p = _v2_cache(tmp_path)
    monkeypatch.setattr(pf, "CACHE", p)
    data = pf.load_data(use_cache=True, force=False, include_real_agent=False, strict=True)
    assert data["benchmark_a"] == [1, 2]


def test_load_data_cache_without_real_agent_warns_on_request(tmp_path, monkeypatch, capsys):
    p = _v2_cache(tmp_path)
    monkeypatch.setattr(pf, "CACHE", p)
    data = pf.load_data(use_cache=True, force=False, include_real_agent=True, strict=True)
    out = capsys.readouterr().out
    assert "real-agent figures will be skipped" in out
    assert data["config"]["include_real_agent"] is False
