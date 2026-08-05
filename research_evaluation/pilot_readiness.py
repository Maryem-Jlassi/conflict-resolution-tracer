from __future__ import annotations
import argparse,json
from pathlib import Path
from typing import Any
from .pilot import verify_blind_package

ROOT=Path(__file__).resolve().parents[1]
def assess(config:dict[str,Any]|None=None,root:Path=ROOT):
    config=config or {}; blockers=[]; checks={}
    protocol=Path(config.get("protocol_bundle","")) if config.get("protocol_bundle") else None
    manifest=protocol/"manifest.json" if protocol else None
    protocol_data=json.loads(manifest.read_text("utf-8")) if manifest and manifest.exists() else {}
    checks["protocol_frozen"]=bool(protocol_data.get("bundle_sha256")); checks["supervisor_approval_recorded"]=bool(protocol_data.get("supervisor_approved") and protocol_data.get("approval_reference"))
    checks["annotation_guide_present"]=(root/"research_data/templates/annotation_guide.md").exists()
    try:
        from .dataset import ConflictEpisode
        checks["episode_schema_valid"]=ConflictEpisode.model_json_schema().get("title")=="ConflictEpisode"
    except Exception: checks["episode_schema_valid"]=False
    packages=list((root/"research_data/pilot/annotation_packages").glob("*.json"))
    blind=True
    for path in packages:
        try: verify_blind_package(json.loads(path.read_text("utf-8")))
        except Exception: blind=False
    checks["blind_packaging_verified"]=bool(packages) and blind
    checks["two_annotators_configured"]=len(set(config.get("annotators",[])))>=2
    checks["adjudicator_configured"]=bool(config.get("adjudicator"))
    source_manifests=list((root/"research_data/pilot/manifests").glob("*.json"))
    checks["source_artifacts_hashed"]=bool(source_manifests) and all(json.loads(p.read_text()).get("sha256") for p in source_manifests)
    checks["duplicate_checks_passed"]=config.get("duplicate_checks_passed") is True
    checks["leakage_checks_passed"]=config.get("leakage_checks_passed") is True
    checks["lcm_outputs_absent"]=blind
    checks["pilot_data_available"]=any(not p.name.startswith(".") for p in (root/"research_data/pilot/raw").iterdir())
    for key,value in checks.items():
        if not value: blockers.append(key)
    return {"verdict":"READY" if not blockers else "BLOCKED","checks":checks,"blockers":blockers}
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--config",type=Path); args=parser.parse_args()
    config=json.loads(args.config.read_text("utf-8")) if args.config else None
    print(json.dumps(assess(config),indent=2))
if __name__=="__main__": main()
