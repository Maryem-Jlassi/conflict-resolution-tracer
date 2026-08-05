"""Generate the Stage 1A research artifact inventory without touching inputs."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "research_audit" / "artifact_inventory.json"
ROOTS = ("benchmark_results", "experiments/results", "results")
EXTENSIONS = {".json", ".csv", ".png", ".pdf"}


def classify(path):
    p = path.as_posix().lower()
    if "release/" in p:
        return "release_gate", "engineering_validation"
    if "/figures/" in p or path.suffix.lower() in {".png", ".pdf"}:
        return "derived_figures", "diagnostic"
    if "real_agent" in p or "ollama" in p:
        return "real_agent_operational", "preliminary_operational"
    if "benchmark" in p:
        return "synthetic_benchmarks", "synthetic_diagnostic"
    return "historical_experiment", "preliminary"


def main():
    paths = []
    for base in ROOTS:
        folder = ROOT / base
        if folder.exists():
            paths.extend(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS and p != OUT)
    records = []
    for path in sorted(set(paths)):
        raw, stat = path.read_bytes(), path.stat()
        family, classification = classify(path)
        reasons = ["historical or non-held-out artifact", "headline criteria not established"]
        records.append({
            "relative_path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(), "file_size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "experiment_family": family, "research_classification": classification,
            "schema_version": "historical_or_embedded",
            "dataset_provenance_status": "not_independently_verified",
            "ground_truth_status": "absent_or_not_independently_adjudicated",
            "independent_unit_count": None, "git_provenance_status": "dirty_snapshot_or_missing",
            "validation_status": "inventory_hash_verified",
            "headline_reporting_eligible": False, "exclusion_reasons": reasons,
            "superseded_by": None,
        })
    inventory = {
        "schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(records),
        "source_provenance": {
            "git_commit": "16fc83f93c801740956baa0bc23fc938bb5295c2", "worktree_dirty": True,
            "tracked_diff_sha256": "5949dacbf4ee5c58d4dc82d3d1cd369c9ad44af27e5c74417ef956d355096198",
            "staged_diff_sha256": "112835316376e62b700440d977c59ec662604a18d0dc24b45f31f4b4893258e8",
            "untracked_manifest_sha256": "aba0226a6038d6bcc2c8968ade72b917b793576e8b8a37bb2c5ca76519d90347",
            "source_manifest_sha256": "d1d13f2a1b451b93a7d4ecb5cb7366cc6107862dc4dcce2b0dcc2c37d767765b",
            "python_version": "3.10.0", "dependency_lock_sha256": "449bcc0fbf6f962ad7bd9b4946d9d6ad9fd880517081de22ab17cf6347135fe5",
            "platform": "Windows-10-10.0.26200-SP0 AMD64",
        },
        "artifacts": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(inventory, indent=2, ensure_ascii=True) + "\n", "utf-8")


if __name__ == "__main__":
    main()
