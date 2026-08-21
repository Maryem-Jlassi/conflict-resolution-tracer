# Leakage Controls

This page describes the controls that prevent test-set leakage into training, calibration, or analysis artifacts. CRT uses frozen artifact hashes, authorization checks, and amendment tracking to maintain evaluation integrity.

## Preventing Test-Set Leakage

Test-set leakage can occur through:

1. **Data leakage**: Test episodes appearing in training or validation splits.
2. **Artifact leakage**: Analysis scripts, thresholds, or hyperparameters tuned on test data.
3. **Knowledge leakage**: Researchers remembering test-set labels and unconsciously biasing analysis.

### Split-Level Controls

- `research_evaluation/splits.py::grouped_split()` enforces that no entity or source family appears in more than one split.
- `research_evaluation/dataset.py::leakage_report()` detects entity, source, and temporal leakage.
- Temporal overlap across splits for the same entity is forbidden.

### Artifact-Level Controls

- Frozen split manifests are written immutably using `freeze_split_manifest()`.
- Once frozen, test-set artifact writes are blocked by `assert_frozen_test_writable()`:

```python
def assert_frozen_test_writable(path: Path, expected_manifest_hash: Optional[str], unlock: bool = False):
    if "test" in {part.lower() for part in path.parts} and not unlock:
        raise PermissionError("frozen test writes require an explicit unlock")
    if path.exists() and expected_manifest_hash is not None:
        current = canonical_hash(json.loads(path.read_text("utf-8")))
        if current != expected_manifest_hash:
            raise PermissionError("frozen artifact hash mismatch")
```

## Frozen Artifact Hashes

All final artifacts are fingerprinted with SHA-256:

- **Dataset splits**: `split_manifest()` returns `{"sha256": canonical_hash(assignments)}`.
- **Final results**: `frozen_artifacts.py::immutable_json_write()` publishes JSON artifacts with SHA-256 digests.
- **Files**: `frozen_artifacts.py::sha256_file()` computes file-level hashes.

Hash verification is performed at two points:

1. **Before analysis**: The evaluation harness verifies that input artifact hashes match the frozen manifest.
2. **Before publication**: `validate_final_authorization()` checks that the split-manifest hash in the authorization record matches the current file hash.

## Authorization Checks

Final-run authorization requires a reviewed record with these fields:

```python
required = {"status", "authorized_by", "authorization_id", "authorization_token_sha256", "split_manifest_sha256"}
```

`frozen_artifacts.py::validate_final_authorization()` enforces:

1. The record exists and has `status == "approved"`.
2. The provided authorization token matches `authorization_token_sha256`.
3. The current split-manifest file hash matches `split_manifest_sha256`.

If any check fails, a `PermissionError` is raised and the run is blocked.

## Amendment Tracking

Protocol amendments are tracked under `docs/research_evaluation/protocol_amendments/`:

- Each amendment is a versioned markdown file describing a deviation from the preregistered protocol.
- Amendments must be reviewed and approved before the affected analysis is executed.
- The amendment index is maintained in the supervisor checklist (`docs/research_evaluation/supervisor_conflict_benchmark_checklist.md`).

Amendment categories include:

- Engineering evaluation deviations (v1.2, v1.3, v1.1, v1_run2, etc.).
- Policy separation execution fixtures.
- Real-agent controlled experiment amendments (claim binding, metric disambiguation, smoke portability, structured prompt).

## Integrity Guarantees

| Control | Mechanism | Enforced By |
|---------|-----------|-------------|
| Split immutability | Atomic hard-link write; `FileExistsError` on overwrite | `freeze_split_manifest()` |
| Hash verification | SHA-256 of JSON content | `canonical_hash()`, `sha256_file()` |
| Test-write blocking | `PermissionError` on test-path writes without unlock | `assert_frozen_test_writable()` |
| Authorization | Token + split-manifest hash match | `validate_final_authorization()` |
| Amendment tracking | Versioned markdown in `protocol_amendments/` | Supervisor review process |

## Threat Model Limitations

The frozen-artifact safeguards are **repository-level guards** — they rely on filesystem permissions and researcher discipline. They are not OS-level security isolation. See `docs/research_evaluation/frozen_artifact_threat_model.md` for the full threat model.
