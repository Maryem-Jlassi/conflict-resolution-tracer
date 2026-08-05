from datetime import datetime,timezone,timedelta
import pytest
from pydantic import ValidationError
from research_evaluation.dataset import ConflictEpisode,ConflictCase,conflict_case_to_episode

NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def claim(cid,value="x"):
    return {"claim_id":cid,"value":value,"semantic_group":value,"agent_id":cid,"source_id":cid,"source_family":"doc","independence_group":cid,"event_time":NOW,"issuance_time":NOW,"ingestion_time":NOW}
def episode(**changes):
    data={"episode_id":"e","domain":"d","entity_ids":["entity"],"conflict_family":"update","evaluation_time":NOW,"claims":[claim("c1"),claim("c2","y")],"truth_timeline":[],"artifact_hashes":{}}
    data.update(changes); return ConflictEpisode.model_validate(data)

def test_stable_claim_id_ground_truth_and_multiple_compatible():
    e=episode(truth_timeline=[{"valid_from":NOW,"status":"established","correct_claim_ids":["c1","c2"],"acceptable_outcome_sets":[["c1","c2"]]}])
    assert e.truth_timeline[0].correct_claim_ids==["c1","c2"]
    with pytest.raises(ValidationError): episode(truth_timeline=[{"valid_from":NOW,"status":"established","correct_claim_ids":["missing"]}])

def test_temporally_changing_truth_timeline():
    e=episode(truth_timeline=[{"valid_from":NOW,"valid_until":NOW+timedelta(days=1),"status":"established","correct_claim_ids":["c1"]},{"valid_from":NOW+timedelta(days=1),"status":"established","correct_claim_ids":["c2"]}])
    assert len(e.truth_timeline)==2

def test_all_wrong_insufficient_and_expected_abstention():
    assert episode(truth_timeline=[{"valid_from":NOW,"status":"all_wrong"}]).truth_timeline[0].status=="all_wrong"
    assert episode(expected_abstention=True,truth_timeline=[{"valid_from":NOW,"status":"expected_abstention"}]).expected_abstention

def test_pairwise_migration_preserves_nonbinary_truth():
    case=ConflictCase.model_validate({"case_id":"p","domain":"d","entity_id":"x","source_family":"docs","existing_claim":{"text":"a","source_id":"s1","source_family":"doc","asserted_at":NOW},"incoming_claim":{"text":"b","source_id":"s2","source_family":"doc","asserted_at":NOW},"adjudicated_outcome":"both_compatible","adjudication_status":"adjudicated","artifact_hashes":{}})
    e=conflict_case_to_episode(case)
    assert [c.claim_id for c in e.claims]==["claim-existing","claim-incoming"]
    assert set(e.truth_timeline[0].correct_claim_ids)=={"claim-existing","claim-incoming"}
