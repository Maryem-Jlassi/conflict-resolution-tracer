from __future__ import annotations
import hashlib,json,os,tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

SPLITS=("train","validation","test","cross_domain_test","source_held_out_test","temporal_held_out_test")
GROUP_FIELDS=("entity_ids","source_families","domain","time_period","conflict_family")

def grouped_split(episodes:list[dict[str,Any]],assignment_by_group:dict[str,str]):
    assignments={}
    histories=defaultdict(set)
    for episode in episodes:
        keys=[]
        for entity in episode["entity_ids"]: keys.append(f"entity:{entity}")
        for source in episode["source_families"]: keys.append(f"source:{source}")
        keys += [f"domain:{episode['domain']}",f"time:{episode['time_period']}",f"family:{episode['conflict_family']}"]
        requested={assignment_by_group[k] for k in keys if k in assignment_by_group}
        if len(requested)>1: raise ValueError(f"conflicting grouped split for {episode['episode_id']}: {sorted(requested)}")
        split=next(iter(requested),"train")
        if split not in SPLITS: raise ValueError(f"unknown split {split}")
        assignments[episode["episode_id"]]=split
        for key in keys: histories[key].add(split)
    forbidden={key:sorted(value) for key,value in histories.items() if len(value)>1 and (key.startswith("entity:") or key.startswith("source:"))}
    if forbidden: raise ValueError(f"entity/source history leakage: {forbidden}")
    rows=[{"episode_id":key,"split":value} for key,value in sorted(assignments.items())]
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return {"schema_version":"1.0","assignments":rows,"content_sha256":digest,"frozen":False}

def freeze_split_manifest(manifest:dict[str,Any],path:Path):
    if path.exists(): raise FileExistsError("frozen split manifest is immutable")
    frozen={**manifest,"frozen":True}; raw=(json.dumps(frozen,sort_keys=True,indent=2)+"\n").encode()
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix=".split-",suffix=".tmp")
    try:
        with os.fdopen(fd,"wb") as handle: handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.link(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return hashlib.sha256(raw).hexdigest()
