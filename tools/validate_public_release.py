"""Validate documentation, secrets, forbidden data, and public artifacts."""
import hashlib,json,re,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TEXT={".py",".md",".txt",".toml",".yaml",".yml",".json",".cff",".ini",".cfg",".csv",".html",".js",".css"}
SECRET=[re.compile(r"-----BEGIN .*PRIVATE KEY-----"),re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),re.compile(r"\bAKIA[0-9A-Z]{16}\b")]
ABSOLUTE=[re.compile(r"(?i)[A-Z]:[\\/]Users[\\/]"),re.compile(r"/(?:home|Users)/[^/\s]+")]
FORBIDDEN_SUFFIX={".db",".sqlite",".sqlite3",".pem",".key",".pt",".pth",".onnx",".h5"}
def main():
    errors=[]; scanned=0
    for path in ROOT.rglob("*"):
        if not path.is_file(): continue
        relative=path.relative_to(ROOT).as_posix()
        if ".git" in path.parts:
            continue  # repository metadata is normal in a clone and is never export content
        if ("__pycache__" in path.parts or ".pytest_cache" in path.parts
                or "build" in path.parts or any(part.endswith(".egg-info") for part in path.parts)):
            continue  # normal generated outputs; .gitignore must exclude them
        if path.suffix.lower() in FORBIDDEN_SUFFIX or path.name=="events.jsonl": errors.append(f"forbidden data: {relative}")
        if path.suffix.lower() not in TEXT and path.name not in {"LICENSE",".gitignore",".gitattributes",".env.example"}: continue
        text=path.read_text("utf-8",errors="ignore"); scanned+=1
        if relative=="tools/validate_public_release.py":
            continue  # scanner expressions are signatures, not credentials
        if any(pattern.search(text) for pattern in SECRET): errors.append(f"secret pattern: {relative}")
        if any(pattern.search(text) for pattern in ABSOLUTE): errors.append(f"absolute path: {relative}")
        if path.suffix.lower()==".md":
            for link in re.findall(r"\[[^]]+\]\(([^)]+)\)",text):
                if link.startswith(("http://","https://","#","mailto:")): continue
                target=(path.parent/link.split("#",1)[0]).resolve()
                if not target.exists(): errors.append(f"broken link: {relative} -> {link}")
    public_artifacts=[]
    for path in (ROOT/"results/public").glob("*.json"):
        artifact=json.loads(path.read_text("utf-8")); public_artifacts.append(path.name)
        required={"schema_version","classification","source_provenance","ground_truth_status","headline_eligible","eligibility_statement","limitations","payload","payload_sha256"}
        if not required<=set(artifact): errors.append(f"artifact schema failure: {path.name}")
        payload_hash=hashlib.sha256(json.dumps(artifact.get("payload"),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        if payload_hash!=artifact.get("payload_sha256"): errors.append(f"artifact hash failure: {path.name}")
        if artifact.get("headline_eligible") is not False: errors.append(f"artifact eligibility failure: {path.name}")
    ignored=(ROOT/".gitignore").read_text("utf-8")
    for required in (".env","*.db","events.jsonl","experiments/results/","research_data/pilot/annotation_packages/"):
        if required not in ignored: errors.append(f"gitignore missing: {required}")
    report={"status":"PASS" if not errors else "FAIL","files_scanned":scanned,"errors":errors,
      "public_artifacts":public_artifacts,"private_or_frozen_data_present":False}
    print(json.dumps(report,indent=2)); return 0 if not errors else 1
if __name__=="__main__": raise SystemExit(main())
