from __future__ import annotations
import hashlib, json, random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from .dataset import ConflictEpisode, cohens_kappa, krippendorff_alpha_nominal

FORBIDDEN_BLIND_KEYS={"method","method_name","lcm_output","psi","psi_scores","trust_score","trust_values",
 "baseline_outputs","hypothesis","hypothesis_direction","annotations","adjudication","adjudicator_id","split",
 "expected_difficulty","truth_timeline","correct_claim_ids"}

def import_source(path:Path,url:str,retrieved_at:datetime):
    raw=path.read_bytes()
    return {"filename":path.name,"source_url":url,"retrieved_at":retrieved_at.isoformat(),
            "size_bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"metadata":{}}

def blank_episode(episode_id,domain,entity_ids,evaluation_time,conflict_family):
    return {"schema_version":"2.0","episode_id":episode_id,"domain":domain,"entity_ids":entity_ids,
      "evaluation_time":evaluation_time.isoformat(),"conflict_family":conflict_family,"claims":[],
      "truth_timeline":[],"expected_abstention":False,"annotations":[],"adjudication_status":"unannotated",
      "split":None,"artifact_hashes":{}}

def blind_episode_package(episodes:list[ConflictEpisode],annotator_id:str,seed:int):
    rng=random.Random(f"{seed}:{annotator_id}"); order=list(range(len(episodes))); rng.shuffle(order)
    presented=[]; mapping=[]
    for position,index in enumerate(order):
        episode=episodes[index]; claims=list(episode.claims); rng.shuffle(claims)
        public_id=f"episode-{position+1:04d}"
        claim_mapping={f"claim-{i+1:03d}":claim.claim_id for i,claim in enumerate(claims)}
        reverse={stable:public for public,stable in claim_mapping.items()}
        presented.append({"presentation_id":public_id,"domain":episode.domain,"entity_ids":episode.entity_ids,
          "evaluation_time":episode.evaluation_time.isoformat(),"claims":[{"presentation_claim_id":reverse[c.claim_id],
          "value":c.value,"source_family":c.source_family,"evidence_artifacts":c.evidence_artifacts,
          "event_time":c.event_time.isoformat(),"issuance_time":c.issuance_time.isoformat(),
          "expiration_time":c.expiration_time.isoformat() if c.expiration_time else None,
          "valid_from":c.valid_from.isoformat() if c.valid_from else None,
          "valid_until":c.valid_until.isoformat() if c.valid_until else None} for c in claims]})
        mapping.append({"presentation_id":public_id,"episode_id":episode.episode_id,"claim_mapping":claim_mapping})
    return {"annotator_id":annotator_id,"episodes":presented},{"annotator_id":annotator_id,"mapping":mapping,"seed":seed}

def verify_blind_package(package:dict[str,Any]):
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower() in FORBIDDEN_BLIND_KEYS: raise ValueError(f"blindness violation: {key}")
                walk(item)
        elif isinstance(value,list):
            for item in value: walk(item)
    walk(package); return True

def annotation_quality(records:list[dict[str,Any]]):
    by_episode=defaultdict(list)
    for row in records: by_episode[row["episode_id"]].append(row)
    complete=[rows for rows in by_episode.values() if len({r["annotator_id"] for r in rows})>=2]
    pairs=[]; pair_rows=[]
    for rows in complete:
        ordered=sorted(rows,key=lambda r:r["annotator_id"]); pair=(ordered[0]["label"],ordered[1]["label"])
        pairs.append(pair); pair_rows.append((ordered[0].get("domain","unknown"),*pair))
    raw=sum(a==b for a,b in pairs)/len(pairs) if pairs else None
    kappa=cohens_kappa([a for a,_ in pairs],[b for _,b in pairs]) if pairs else None
    alpha=krippendorff_alpha_nominal([[r["label"] for r in rows] for rows in complete]) if complete else None
    by_domain={domain:sum(a==b for d,a,b in pair_rows if d==domain)/sum(d==domain for d,_,_ in pair_rows)
               for domain in {d for d,_,_ in pair_rows}}
    by_label={label:sum(a==b for a,b in pairs if label in (a,b))/sum(label in (a,b) for a,b in pairs)
              for label in {x for pair in pairs for x in pair}}
    confusion=Counter()
    for a,b in pairs: confusion[f"{a}|{b}"]+=1
    times=[r["annotation_seconds"] for r in records if r.get("annotation_seconds") is not None]
    total_episodes=len(by_episode); adjudicated=sum(any(r.get("adjudicated") for r in rows) for rows in by_episode.values())
    missing=sum(not r.get("label") for r in records)
    return {"raw_agreement":raw,"cohens_kappa":kappa,"krippendorff_alpha":alpha,
      "agreement_by_domain":by_domain,"agreement_by_label":by_label,"confusion_matrix":dict(confusion),
      "disagreement_reasons":dict(Counter(r.get("disagreement_reason") for r in records if r.get("disagreement_reason"))),
      "adjudication_rate":adjudicated/total_episodes if total_episodes else None,
      "missing_label_rate":missing/len(records) if records else None,
      "annotation_time":{"count":len(times),"mean_seconds":sum(times)/len(times) if times else None},
      "singly_annotated_excluded_from_correctness":sum(len({r["annotator_id"] for r in rows})<2 for rows in by_episode.values())}
