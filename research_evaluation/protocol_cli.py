from __future__ import annotations
import argparse, hashlib, json, os, stat
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BUNDLE_FILES={
 "protocol.md":ROOT/"docs/research_evaluation/protocol.md",
 "hypotheses.yaml":ROOT/"docs/research_evaluation/hypotheses.yaml",
 "statistical_analysis_plan.md":ROOT/"docs/research_evaluation/statistical_analysis_plan.md",
 "dataset_card.md":ROOT/"docs/research_evaluation/dataset_card.md",
 "experiment_card.md":ROOT/"docs/research_evaluation/experiment_card.md",
 "annotation_guide.md":ROOT/"research_data/templates/annotation_guide.md"}

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def freeze_protocol(version:str, approved_by:str, out:Path, approval_reference:str|None=None):
    if out.exists(): raise FileExistsError(f"frozen protocol is immutable: {out}")
    if not approved_by.strip(): raise ValueError("approved_by metadata is required")
    out.mkdir(parents=True)
    files={}
    try:
        for name,source in BUNDLE_FILES.items():
            target=out/name; target.write_bytes(source.read_bytes()); files[name]=sha(target)
        manifest={"schema_version":"1.0","protocol_version":version,
          "frozen_at":datetime.now(timezone.utc).isoformat(),"approved_by":approved_by,
          "approval_reference":approval_reference,"supervisor_approved":bool(approval_reference),
          "files":files}
        manifest["bundle_sha256"]=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        (out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n","utf-8")
        for path in out.iterdir(): path.chmod(stat.S_IREAD)
        return manifest
    except Exception:
        raise

def create_amendment(out:Path, amendment_id:str, reason:str, files_affected:list[str],
                     data_inspected:bool, affects_primary:bool, approver:str,
                     previous_hashes:dict,new_hashes:dict):
    if out.exists(): raise FileExistsError("amendment records are immutable")
    record={"schema_version":"1.0","amendment_id":amendment_id,
      "date":datetime.now(timezone.utc).date().isoformat(),"reason":reason,
      "files_affected":files_affected,"data_already_inspected":data_inspected,
      "affects_primary_analysis":affects_primary,"approver":approver,
      "previous_hashes":previous_hashes,"new_hashes":new_hashes}
    out.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(fd,"w",encoding="utf-8") as handle: json.dump(record,handle,indent=2); handle.write("\n")
    out.chmod(stat.S_IREAD); return record

def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    freeze=sub.add_parser("freeze"); freeze.add_argument("--version",required=True); freeze.add_argument("--approved-by",required=True); freeze.add_argument("--approval-reference"); freeze.add_argument("--out",type=Path,required=True)
    amend=sub.add_parser("amend"); amend.add_argument("--id",required=True); amend.add_argument("--reason",required=True); amend.add_argument("--files",nargs="+",required=True); amend.add_argument("--approver",required=True); amend.add_argument("--previous",type=Path,required=True); amend.add_argument("--new",type=Path,required=True); amend.add_argument("--out",type=Path,required=True); amend.add_argument("--data-inspected",action="store_true"); amend.add_argument("--affects-primary",action="store_true")
    args=parser.parse_args()
    if args.command=="freeze": result=freeze_protocol(args.version,args.approved_by,args.out,args.approval_reference)
    else: result=create_amendment(args.out,args.id,args.reason,args.files,args.data_inspected,args.affects_primary,args.approver,json.loads(args.previous.read_text()),json.loads(args.new.read_text()))
    print(json.dumps(result,indent=2))
if __name__=="__main__": main()
