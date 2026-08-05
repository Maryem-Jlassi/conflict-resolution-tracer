"""Versioned, immutable research-run artifacts with eligibility validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = "1.0"
RUN_STATES = {"completed", "failed", "blocked", "skipped"}
CLASSIFICATIONS = {"research", "preliminary", "diagnostic", "synthetic", "operational"}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def headline_eligibility(manifest: Dict[str, Any], rows: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    gt = manifest.get("ground_truth", {})
    source = manifest.get("source_provenance", {})
    if not gt.get("available"):
        reasons.append("ground_truth_absent")
    if not gt.get("independently_adjudicated"):
        reasons.append("ground_truth_not_independently_adjudicated")
    required_source = ("git_commit", "worktree_dirty", "source_manifest_sha256")
    if any(key not in source for key in required_source):
        reasons.append("source_provenance_missing")
    if not manifest.get("dataset", {}).get("split"):
        reasons.append("dataset_split_unknown")
    if manifest.get("research_classification") in {"diagnostic", "synthetic"}:
        reasons.append("diagnostic_or_synthetic")
    unit_ids = [row.get("independent_unit_id") for row in rows]
    if None in unit_ids or len(unit_ids) != len(set(unit_ids)):
        reasons.append("independent_units_missing_or_repeated")
    if manifest.get("row_count") != len(rows):
        reasons.append("row_count_mismatch")
    if manifest.get("real_agent_execution") and not manifest.get("model_call_evidence"):
        reasons.append("real_agent_without_model_call_evidence")
    return not reasons, reasons


def build_artifact(manifest: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    body = {"schema_version": SCHEMA_VERSION, "manifest": dict(manifest), "rows": rows}
    body["manifest"].setdefault("schema_version", SCHEMA_VERSION)
    eligible, reasons = headline_eligibility(body["manifest"], rows)
    body["manifest"]["headline_eligible"] = eligible
    body["manifest"]["headline_exclusion_reasons"] = reasons
    body["artifact_sha256"] = canonical_sha256(body)
    return body


def validate_artifact(artifact: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    manifest, rows = artifact.get("manifest", {}), artifact.get("rows", [])
    if manifest.get("state") not in RUN_STATES:
        errors.append("invalid_run_state")
    if manifest.get("research_classification") not in CLASSIFICATIONS:
        errors.append("invalid_research_classification")
    expected = dict(artifact)
    claimed = expected.pop("artifact_sha256", None)
    if claimed != canonical_sha256(expected):
        errors.append("artifact_hash_failed")
    eligible, reasons = headline_eligibility(manifest, rows)
    if manifest.get("headline_eligible") != eligible or manifest.get("headline_exclusion_reasons") != reasons:
        errors.append("headline_eligibility_inconsistent")
    return errors


def write_immutable(path: os.PathLike[str] | str, artifact: Dict[str, Any]) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(target)
    errors = validate_artifact(artifact)
    if errors:
        raise ValueError(", ".join(errors))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_json_bytes(artifact))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_name, target)  # atomic and refuses overwrite
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target


def adapt_historical(payload: Any, relative_path: str) -> Dict[str, Any]:
    """Wrap a legacy payload without modifying it or inventing provenance."""
    rows = payload if isinstance(payload, list) else [payload]
    manifest = {
        "state": "completed", "research_classification": "preliminary",
        "row_count": len(rows), "dataset": {"split": None},
        "ground_truth": {"available": False, "independently_adjudicated": False},
        "source_provenance": {}, "historical_source_path": relative_path,
    }
    return build_artifact(manifest, [
        {"independent_unit_id": f"legacy-{i}", "legacy_payload": row}
        for i, row in enumerate(rows)
    ])
