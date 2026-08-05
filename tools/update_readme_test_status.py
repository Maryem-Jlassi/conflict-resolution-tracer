"""Update README from an explicit or newest completed authoritative report."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def select_report(release_root: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted(release_root.rglob("verify_release_*.json"),
                        key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    for path in candidates:
        try:
            report = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("verdict") and report.get("categories"):
            return path
    raise FileNotFoundError("no completed authoritative release report found")


def render_fragment(report_path: Path, root: Path = ROOT) -> str:
    report = json.loads(report_path.read_text("utf-8"))
    verdict = report["verdict"]
    pytest_category = report.get("categories", {}).get("pytest_suite", {})
    checks = pytest_category.get("checks", [])
    detail = checks[0].get("detail", "pytest result unavailable") if checks else "pytest not run"
    source = report_path.resolve().relative_to(root.resolve()).as_posix()
    return ("<!-- release-test-status:start -->\n"
            f"Authoritative release verdict: **{verdict}** — {detail} "
            f"([generated source]({source})).\n"
            "<!-- release-test-status:end -->")


def update_readme(report_path: Path, readme: Path, root: Path = ROOT) -> None:
    fragment = render_fragment(report_path, root)
    text = readme.read_text("utf-8")
    updated, count = re.subn(
        r"<!-- release-test-status:start -->.*?<!-- release-test-status:end -->",
        fragment, text, flags=re.DOTALL)
    if count != 1:
        raise ValueError("README must contain exactly one release-test-status block")
    readme.write_text(updated, "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--readme", type=Path, default=ROOT / "README.md")
    args = parser.parse_args()
    report = select_report(ROOT / "results" / "release", args.report)
    update_readme(report, args.readme)


if __name__ == "__main__":
    main()
