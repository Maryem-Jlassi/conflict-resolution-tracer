"""
Publication figure generator tests.

Covers the honest skip path (FigureSkip -> manifest skip_reason) and the
illustrative architecture figure, without depending on live benchmarks.
"""

import json

import pytest

import results.publication_figures as pf


def _v2_cache(tmp_path):
    return {
        "schema_version": 2,
        "generated_at": "2026-08-03T00:00:00",
        "git_commit": "abc123",
        "python_version": "3.10",
        "platform": "test",
        "benchmark_versions": {},
        "config": {"include_real_agent": False},
        "sources": {},
        "benchmark_a": [],
        "benchmark_b": [],
        "benchmark_c": [],
        "benchmark_d": [],
        "benchmark_e": [],
        "experiments": [],
    }


def test_figureskip_carries_reason():
    with pytest.raises(pf.FigureSkip) as ei:
        raise pf.FigureSkip("no validated real-Ollama artifacts")
    assert ei.value.reason == "no validated real-Ollama artifacts"
    assert str(ei.value) == "no validated real-Ollama artifacts"


def test_multi_agent_figure_raises_figureskip_without_data():
    data = _v2_cache(None)
    with pytest.raises(pf.FigureSkip) as ei:
        pf.figure_multi_agent_experiment(data)
    assert "no validated real-Ollama artifacts" in ei.value.reason


def test_architecture_figure_renders(tmp_path, monkeypatch):
    """fig_architecture is data-independent and must render into FIG_DIR."""
    monkeypatch.setattr(pf, "FIG_DIR", tmp_path)
    path = pf.figure_architecture(_v2_cache(None))
    assert path.name == "fig_architecture.png"
    assert path.exists()


def test_main_records_honest_skip_in_manifest(tmp_path, monkeypatch):
    cache = tmp_path / "figures_data.json"
    cache.write_text(json.dumps(_v2_cache(tmp_path)), encoding="utf-8")
    fig_dir = tmp_path / "figures"
    fig_dir.mkdir()
    monkeypatch.setattr(pf, "CACHE", cache)
    monkeypatch.setattr(pf, "FIG_DIR", fig_dir)

    pf.main(["--cache-only", "--figures", "fig_multi_agent_experiment"])

    manifest = json.loads((tmp_path / "figures" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    entry = manifest["figures"][0]
    assert entry["figure"] == "fig_multi_agent_experiment"
    assert entry["status"] == "skipped"
    assert "no validated real-Ollama artifacts" in entry["skip_reason"]
    assert entry["data_classification"] == "skipped"


def test_main_rejects_unknown_figure(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE", tmp_path / "figures_data.json")
    with pytest.raises(SystemExit) as ei:
        pf.main(["--cache-only", "--figures", "fig_nonexistent"])
    assert "Unknown figure" in str(ei.value)
