from __future__ import annotations
import hashlib,json,platform,sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={".git","venv",".venv","__pycache__",".pytest_cache","results","benchmark_results","Microsoft"}
SECRET_MARKERS=(".env","secret","private_key","credentials","token")
GENERATED_SUFFIXES={".png",".pdf",".db",".sqlite",".pyc",".log"}
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def source_files():
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and not any(part in EXCLUDED for part in p.parts)
                  and "experiments/results" not in p.relative_to(ROOT).as_posix()
                  and p.suffix not in GENERATED_SUFFIXES and not p.name.startswith("events.jsonl"))
def generate():
    files=source_files(); intended=[]; secrets=[]; ignored=[]
    for path in files:
        relative=path.relative_to(ROOT).as_posix(); lower=relative.lower()
        if any(marker in lower for marker in SECRET_MARKERS): secrets.append(relative)
        else: intended.append(relative)
    ignored=["venv/",".venv/","**/__pycache__/",".pytest_cache/","events.jsonl","*.db","*.sqlite","*.pyc","temporary model/cache outputs"]
    manifest=[{"path":p.relative_to(ROOT).as_posix(),"sha256":sha(p),"size":p.stat().st_size} for p in files]
    source_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    hashes={"source_manifest_sha256":source_hash,
      "protocol_sha256":sha(ROOT/"docs/research_evaluation/protocol.md"),
      "hypotheses_sha256":sha(ROOT/"docs/research_evaluation/hypotheses.yaml"),
      "statistical_plan_sha256":sha(ROOT/"docs/research_evaluation/statistical_analysis_plan.md"),
      "dataset_schema_sha256":sha(ROOT/"research_data/templates/conflict_episode.schema.json"),
      "pairwise_dataset_schema_sha256":sha(ROOT/"research_data/templates/conflict_case.schema.json"),
      "dependency_lock_sha256":sha(ROOT/"requirements.txt")}
    return {"schema_version":"1.0","generated_at":datetime.now(timezone.utc).isoformat(),
      "recommended_tag":"lcm-evaluation-infrastructure-v1","automatic_commit_or_tag":False,
      "files_intended_for_source_control":intended,
      "files_that_should_remain_generated":["results/release/","results/research_checkpoint/","results/research_protocol/","experiments/results/","benchmark_results/","annotation randomization mappings","dataset summaries"],
      "files_that_may_contain_secrets":secrets,
      "files_that_should_be_ignored":ignored,"hashes":hashes,
      "environment":{"python":sys.version.split()[0],"platform":platform.platform()}}
def write_report(out:Path):
    if out.exists(): raise FileExistsError(out)
    out.parent.mkdir(parents=True,exist_ok=True); report=generate()
    out.write_text(json.dumps(report,indent=2)+"\n","utf-8"); return report
