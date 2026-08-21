# Split Policy

This page describes how dataset splits are created, how independent units are identified, inclusion/exclusion rules, and how frozen split manifests are produced and verified.

## How Splits Are Created

Splits are created by `research_evaluation/splits.py::grouped_split()`:

```python
def grouped_split(episodes: list[dict[str, Any]], assignment_by_group: dict[str, str]) -> dict[str, Any]:
```

- Each episode is assigned a split based on its group keys.
- Group keys are derived from:
  - `entity_ids` → `entity:{id}`
  - `source_families` → `source:{family}`
  - `domain` → `domain:{domain}`
  - `time_period` → `time:{period}`
  - `conflict_family` → `family:{family}`
- If an episode has multiple conflicting group assignments, a `ValueError` is raised.
- Unassigned episodes default to `train`.

## Independent Unit Identification

The independent unit is the **episode** (`episode_id`):

- An episode contains one or more `EpisodeClaim` objects.
- All claims in an episode share the same `entity_ids`, `domain`, `conflict_family`, and `evaluation_time`.
- Claims within an episode may be correlated (same event, different sources), so they must stay in the same split.

For the legacy `ConflictCase` schema (1.0), the independent unit is the `case_id`.

## Inclusion Rules

Episodes are included in a split only if:

1. They have a valid `schema_version` (1.0 or 2.0).
2. All referenced claim IDs are unique and stable.
3. `derived_from_claim_ids` reference only known claim IDs within the same episode.
4. `truth_timeline` references only known claim IDs.
5. `expected_abstention` is `False` when any `TruthInterval` has `status == "established"`.
6. The episode passes artifact hash validation.

## Exclusion Rules

Episodes are excluded (or cause splits to be rejected) if:

1. **Entity/source history leakage**: An entity or source family appears in more than one split.
2. **Temporal leakage**: Cases for the same entity overlap in time across different splits.
3. **Missing independent unit ID**: Rows without `independent_unit_id` are excluded from metric calculation (counted in `exclusion_reasons`).
4. **Unknown split**: Episodes with splits outside the allowed set are rejected.
5. **Duplicate case IDs**: The `validate_cases()` function rejects duplicate IDs.
6. **Corrupt or hash-invalid artifacts**: `require_accuracy_eligible()` blocks accuracy computation for corrupt artifacts.

## Frozen Split Manifests

Once a split is finalized, it is frozen immutably:

```python
def freeze_split_manifest(manifest: dict[str, Any], path: Path) -> str:
    if path.exists():
        raise FileExistsError("frozen split manifest is immutable")
    frozen = {**manifest, "frozen": True}
    raw = (json.dumps(frozen, sort_keys=True, indent=2) + "\n").encode()
    # Atomic hard-link publication
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".split-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.link(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return hashlib.sha256(raw).hexdigest()
```

Key properties:

- The manifest file is written using an **atomic hard-link** step: data is flushed to a temp file, then hard-linked to the final path. This ensures the file is either fully present or absent; partial writes are impossible.
- Once written, `freeze_split_manifest()` raises `FileExistsError` on subsequent calls.
- The returned SHA-256 digest is the authoritative fingerprint of the frozen manifest.

## Valid Splits

| Split Name | Purpose |
|------------|---------|
| `train` | Development and calibration (not used for final testing). |
| `validation` | Hyperparameter tuning and early stopping. |
| `test` | Final held-out test set. |
| `cross_domain_test` | Held-out domains not seen during training. |
| `source_held_out_test` | Held-out source families not seen during training. |
| `temporal_held_out_test` | Held-out time periods not seen during training. |

## Leakage Detection

`research_evaluation/dataset.py::leakage_report()` checks:

- **Entity leakage**: Same entity in multiple splits.
- **Source leakage**: Same source family in multiple splits.
- **Temporal leakage**: Overlapping event timestamps for the same entity across splits.

```python
report = leakage_report(cases)
# report["entity"] -> list of (entity_id, [split1, split2, ...])
# report["source"] -> list of (source_id, [split1, split2, ...])
# report["temporal"] -> list of (entity_id, [split1, split2, ...])
```
