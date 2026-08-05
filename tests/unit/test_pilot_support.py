from datetime import datetime,timezone
import json
import pytest
from research_evaluation.dataset import ConflictEpisode
from research_evaluation.pilot import annotation_quality,blind_episode_package,verify_blind_package
from research_evaluation.pilot_readiness import assess

NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def episode(eid):
    claims=[]
    for i in range(3): claims.append({"claim_id":f"{eid}-c{i}","value":str(i),"semantic_group":str(i),"agent_id":str(i),"source_id":str(i),"source_family":"doc","independence_group":str(i),"event_time":NOW,"issuance_time":NOW,"ingestion_time":NOW,"metadata":{"trust_score":.9}})
    return ConflictEpisode(episode_id=eid,domain="d",entity_ids=[eid],conflict_family="f",evaluation_time=NOW,claims=claims)

def test_randomization_is_independent_and_stable_ids_hidden():
    episodes=[episode(str(i)) for i in range(5)]
    p1,m1=blind_episode_package(episodes,"ann-a",10); p2,m2=blind_episode_package(episodes,"ann-b",10)
    assert m1 != m2 and p1 != p2
    assert all("claim_id" not in claim for row in p1["episodes"] for claim in row["claims"])
    assert all(item["claim_mapping"] for item in m1["mapping"])
    assert verify_blind_package(p1)

def test_blindness_rejects_forbidden_outputs():
    with pytest.raises(ValueError): verify_blind_package({"episodes":[],"lcm_output":"hidden"})
    with pytest.raises(ValueError): verify_blind_package({"claims":[{"trust_score":.8}]})

def test_quality_report_by_domain_and_label():
    records=[{"episode_id":"e1","annotator_id":"a","label":"x","domain":"d","annotation_seconds":10},
             {"episode_id":"e1","annotator_id":"b","label":"x","domain":"d","annotation_seconds":20},
             {"episode_id":"e2","annotator_id":"a","label":"x","domain":"d","disagreement_reason":"evidence"},
             {"episode_id":"e2","annotator_id":"b","label":"y","domain":"d","adjudicated":True}]
    report=annotation_quality(records)
    assert report["raw_agreement"]==.5 and report["agreement_by_domain"]["d"]==.5
    assert "x" in report["agreement_by_label"] and report["confusion_matrix"]["x|y"]==1
    assert report["adjudication_rate"]==.5 and report["annotation_time"]["mean_seconds"]==15

def test_readiness_current_state_blocked(tmp_path):
    for relative in ("research_data/templates","research_data/pilot/annotation_packages","research_data/pilot/manifests","research_data/pilot/raw"):
        (tmp_path/relative).mkdir(parents=True,exist_ok=True)
    (tmp_path/"research_data/templates/annotation_guide.md").write_text("guide")
    result=assess({},tmp_path)
    assert result["verdict"]=="BLOCKED"
    assert "protocol_frozen" in result["blockers"] and "pilot_data_available" in result["blockers"]
