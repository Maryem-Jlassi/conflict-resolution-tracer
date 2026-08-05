import json
from pathlib import Path

from tools.update_readme_test_status import render_fragment, select_report, update_readme


def _report(path: Path, verdict: str, detail: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verdict": verdict, "categories": {
        "pytest_suite": {"checks": [{"detail": detail}]}}}), "utf-8")


def test_newer_fail_is_not_hidden_by_older_pass(tmp_path):
    old = tmp_path / "verify_release_1.json"
    new = tmp_path / "verify_release_2.json"
    _report(old, "PASS", "10 passed, 0 skipped, 0 failed")
    _report(new, "FAIL", "9 passed, 0 skipped, 1 failed")
    old.touch()
    new.touch()
    assert select_report(tmp_path) == new
    assert "**FAIL**" in render_fragment(new, tmp_path)


def test_explicit_report_wins(tmp_path):
    chosen = tmp_path / "chosen.json"
    newer = tmp_path / "newer.json"
    _report(chosen, "PASS", "1 passed")
    _report(newer, "FAIL", "1 failed")
    assert select_report(tmp_path, chosen) == chosen.resolve()


def test_update_preserves_surrounding_readme(tmp_path):
    report = tmp_path / "report.json"
    _report(report, "FAIL", "2 passed, 1 failed")
    readme = tmp_path / "README.md"
    readme.write_text("before\n<!-- release-test-status:start -->old<!-- release-test-status:end -->\nafter", "utf-8")
    update_readme(report, readme, tmp_path)
    text = readme.read_text("utf-8")
    assert "before" in text and "after" in text and "**FAIL**" in text
