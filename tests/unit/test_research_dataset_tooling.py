from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_evaluation.dataset import (ConflictCase, adjudicate, blind_package,
    cohens_kappa, duplicate_report, krippendorff_alpha_nominal, leakage_report,
    split_manifest, assert_frozen_test_writable, validate_cases)

NOW=datetime(2026,1,1,tzinfo=timezone.utc)

def case(case_id="c1", entity="e1", split="pilot", left="alpha", right="beta", source="s1"):
    return ConflictCase.model_validate({"case_id":case_id,"domain":"general","entity_id":entity,
        "source_family":"documents","existing_claim":{"text":left,"source_id":source,"source_family":"doc","asserted_at":NOW.isoformat()},
        "incoming_claim":{"text":right,"source_id":source+"x","source_family":"doc","asserted_at":NOW.isoformat()},
        "event_timestamps":[NOW.isoformat()],"split":split,"artifact_hashes":{}})

def test_schema_supports_non_binary_labels_and_requires_adjudicated_label():
    c=case(); c.adjudicated_outcome="both_compatible"
    assert c.adjudicated_outcome == "both_compatible"
    with pytest.raises(ValidationError):
        ConflictCase.model_validate({**c.model_dump(),"adjudication_status":"adjudicated","adjudicated_outcome":None})

def test_blind_package_excludes_forbidden_information():
    c=case(); c.adjudicated_outcome="incoming"
    row=blind_package([c])[0]
    forbidden={"lcm_output","psi","psi_scores","baseline_outputs","hypothesis","annotator_records","adjudicated_outcome"}
    assert forbidden.isdisjoint(row)

def test_agreement_calculations():
    assert cohens_kappa(["a","b","a"],["a","b","a"]) == 1
    assert krippendorff_alpha_nominal([["a","a"],["b","b"]]) == pytest.approx(1)

def test_adjudication_is_explicit_human_action():
    result=adjudicate(case(),"unresolved","human","insufficient evidence",NOW)
    assert result.adjudication_status == "adjudicated" and result.adjudicated_outcome == "unresolved"

def test_duplicates_and_leakage():
    cases=[case("a","entity","train","same","claim","shared"),case("b","entity","test","same","claim","shared")]
    assert duplicate_report(cases)["exact"] == [("a","b")]
    report=leakage_report(cases)
    assert report["entity"] and report["source"] and report["temporal"]

def test_near_duplicate_detection():
    cases=[case("a",left="the quick brown fox",right="one"),case("b",left="the quick brown foxes",right="one")]
    assert duplicate_report(cases,.8)["near"]

def test_manifest_hash_stable_and_frozen_test_locked(tmp_path):
    assert split_manifest([case()]) == split_manifest([case()])
    with pytest.raises(PermissionError): assert_frozen_test_writable(tmp_path/"test"/"cases.json",None)
    assert_frozen_test_writable(tmp_path/"test"/"cases.json",None,unlock=True)

def test_duplicate_case_ids_rejected():
    payload=[case().model_dump(mode="json"),case().model_dump(mode="json")]
    with pytest.raises(ValueError): validate_cases(payload)
