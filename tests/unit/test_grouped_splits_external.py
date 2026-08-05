import json
import pytest
from research_evaluation.external_adapters import preserve_examples,require_official_manifest,validate_expected_structure
from research_evaluation.splits import freeze_split_manifest,grouped_split

def ep(eid,entity,source): return {"episode_id":eid,"entity_ids":[entity],"source_families":[source],"domain":"d","time_period":"2026-Q1","conflict_family":"update"}
def test_grouped_split_and_frozen_immutability(tmp_path):
    manifest=grouped_split([ep("a","e1","s1"),ep("b","e2","s2")],{"entity:e1":"train","entity:e2":"test"})
    out=tmp_path/"frozen.json"; digest=freeze_split_manifest(manifest,out)
    assert digest and json.loads(out.read_text())["frozen"]
    with pytest.raises(FileExistsError): freeze_split_manifest(manifest,out)
def test_forbidden_entity_source_leakage_fails():
    with pytest.raises(ValueError): grouped_split([ep("a","same","s1"),ep("b","same","s2")],{"source:s1":"train","source:s2":"test"})
def test_official_manifest_and_structure_validation(tmp_path):
    valid={"official_url":"https://official","version":"commit","license":"MIT","archive_sha256":"a"*64,"retrieved_at":"2026-01-01","official_source_validated":True}
    require_official_manifest(valid,"dataset")
    with pytest.raises(ValueError): require_official_manifest({},"dataset")
    (tmp_path/"data").mkdir(); (tmp_path/"data/file.json").write_text("[]")
    assert validate_expected_structure(tmp_path,["data/file.json"])
def test_external_adapter_preserves_ids_and_labels():
    row={"question_id":"q1","answer":"original","payload":1}
    adapted=preserve_examples([row],"question_id",["answer"])[0]
    assert adapted["original_example_id"]=="q1" and adapted["original_labels"]=={"answer":"original"}
    assert not adapted["labels_transformed"] and adapted["original_payload"]==row
