import pytest
from research_evaluation.policies import EpisodePolicyInput,episode_replay
def c(cid,group,agent,independent,time,**scores): return {"claim_id":cid,"semantic_group":group,"agent_id":agent,"independence_group":independent,"event_time":time,**scores}
def test_multiclaim_intermediate_rows_and_pairwise_warning():
    e=EpisodePolicyInput("e",(c("1","a","x","g1","1"),c("2","b","y","g2","2"),c("3","b","z","g3","3")),"3","d")
    out=episode_replay(e,["last_write_wins","majority_raw_agent"])
    assert out["row_count"]==6 and {r["after_claim_id"] for r in out["rows"]}=={"1","2","3"}
    assert not out["pairwise_majority_warning"]
def test_repeated_agent_vote_suppression():
    e=EpisodePolicyInput("e",(c("1","a","same","g1","1"),c("2","a","same","g1","2"),c("3","b","other","g2","3")),"3","d")
    row=episode_replay(e,["majority_raw_agent"])["rows"][-1]
    assert row["decision_semantic_group"]=="unresolved"
def test_independent_source_majority_and_tie_policy():
    e=EpisodePolicyInput("e",(c("1","a","x","shared","1"),c("2","a","y","shared","2"),c("3","b","z","ind","3")),"3","d")
    assert episode_replay(e,["majority_independent_source"])["rows"][-1]["decision_semantic_group"]=="unresolved"
    assert episode_replay(e,["majority_independent_source"],"most_recent")["rows"][-1]["decision_semantic_group"]=="b"
def test_oracle_rejected():
    e=EpisodePolicyInput("e",(),"","d")
    with pytest.raises(PermissionError): episode_replay(e,["oracle_analysis_only"])
