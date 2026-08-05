"""Preparation-only adapters for officially acquired datasets."""
from pathlib import Path
from typing import Any

def require_official_manifest(manifest: dict[str, Any], dataset: str) -> None:
    required={"official_url","version","license","archive_sha256","retrieved_at"}
    missing=required-set(manifest)
    if missing: raise ValueError(f"{dataset} acquisition manifest missing: {sorted(missing)}")
    if not manifest.get("official_source_validated"): raise ValueError("official source is not validated")

def prepare_longmemeval(raw_path: Path, manifest: dict[str, Any]):
    require_official_manifest(manifest,"LongMemEval")
    if not raw_path.exists(): raise FileNotFoundError(raw_path)
    return {"state":"prepared","dataset":"LongMemEval","raw_path":str(raw_path),"labels_generated":False}

def prepare_locomo(raw_path: Path, manifest: dict[str, Any]):
    require_official_manifest(manifest,"LoCoMo")
    if not raw_path.exists(): raise FileNotFoundError(raw_path)
    return {"state":"prepared","dataset":"LoCoMo","raw_path":str(raw_path),"labels_generated":False}

def preserve_examples(examples: list[dict[str, Any]], id_field: str, label_fields: list[str]):
    """Preparation adapter: preserve original IDs/labels without interpreting them."""
    output=[]
    for example in examples:
        if id_field not in example: raise ValueError(f"missing original ID field {id_field}")
        output.append({"original_example_id":example[id_field],
          "original_labels":{key:example.get(key) for key in label_fields},
          "original_payload":example,"labels_transformed":False})
    return output

def validate_expected_structure(root: Path, expected_paths: list[str]):
    missing=[relative for relative in expected_paths if not (root/relative).exists()]
    if missing: raise ValueError(f"missing expected dataset paths: {missing}")
    return True
