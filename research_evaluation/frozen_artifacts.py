import hashlib
import json
from pathlib import Path


def immutable_json_write(path: Path, data: dict) -> None:
    """Write JSON to path, refusing to overwrite an existing file."""
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def validate_final_authorization(
    record: dict,
    token: str,
    split_manifest: Path,
) -> None:
    """Validate that the authorization record matches the token and split manifest."""
    expected_token_sha = hashlib.sha256(token.encode()).hexdigest()
    if record.get("authorization_token_sha256") != expected_token_sha:
        raise PermissionError("Authorization token mismatch")

    current_manifest_sha = hashlib.sha256(split_manifest.read_bytes()).hexdigest()
    if record.get("split_manifest_sha256") != current_manifest_sha:
        raise PermissionError("Split manifest has changed since authorization")
