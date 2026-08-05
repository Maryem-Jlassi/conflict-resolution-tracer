from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
FinalLabel = Literal["existing", "incoming", "both_compatible", "both_wrong", "unresolved"]


class Claim(BaseModel):
    text: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    asserted_at: datetime
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnnotatorRecord(BaseModel):
    annotator_id: str
    label: FinalLabel
    rationale: str = ""
    annotated_at: datetime


class ConflictCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    existing_claim: Claim
    incoming_claim: Claim
    evidence_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    event_timestamps: list[datetime] = Field(default_factory=list)
    validity_interval: Optional[dict[str, Optional[datetime]]] = None
    adjudicated_outcome: Optional[FinalLabel] = None
    ambiguity_reason: Optional[str] = None
    annotator_records: list[AnnotatorRecord] = Field(default_factory=list)
    adjudication_status: Literal["unannotated", "in_progress", "agreement", "adjudicated", "escalated"] = "unannotated"
    split: Optional[Literal["pilot", "train", "validation", "test"]] = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_adjudication(self):
        if self.adjudication_status == "adjudicated" and self.adjudicated_outcome is None:
            raise ValueError("adjudicated cases require an adjudicated_outcome")
        return self


class EpisodeClaim(BaseModel):
    claim_id: str = Field(min_length=1)
    value: Any
    semantic_group: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_family: str = Field(min_length=1)
    independence_group: str = Field(min_length=1)
    evidence_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    event_time: datetime
    issuance_time: datetime
    ingestion_time: datetime
    expiration_time: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    derived_from_claim_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TruthInterval(BaseModel):
    valid_from: datetime
    valid_until: Optional[datetime] = None
    status: Literal["established", "all_wrong", "insufficient_evidence", "expected_abstention"]
    correct_claim_ids: list[str] = Field(default_factory=list)
    acceptable_outcome_sets: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def truth_shape(self):
        if self.status == "established" and not (self.correct_claim_ids or self.acceptable_outcome_sets):
            raise ValueError("established truth requires stable correct claim IDs or acceptable sets")
        if self.status != "established" and self.correct_claim_ids:
            raise ValueError("non-established truth cannot name correct claims")
        return self


class ConflictEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0"] = "2.0"
    episode_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    entity_ids: list[str] = Field(min_length=1)
    conflict_family: str = Field(min_length=1)
    evaluation_time: datetime
    claims: list[EpisodeClaim] = Field(min_length=2)
    truth_timeline: list[TruthInterval] = Field(default_factory=list)
    expected_abstention: bool = False
    annotations: list[AnnotatorRecord] = Field(default_factory=list)
    adjudication_status: Literal["unannotated", "in_progress", "agreement", "adjudicated", "escalated"] = "unannotated"
    adjudicator_id: Optional[str] = None
    split: Optional[Literal["pilot", "train", "validation", "test", "cross_domain_test", "source_held_out_test", "temporal_held_out_test"]] = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def stable_references(self):
        ids=[claim.claim_id for claim in self.claims]
        if len(ids)!=len(set(ids)): raise ValueError("claim IDs must be stable and unique")
        known=set(ids)
        for claim in self.claims:
            if not set(claim.derived_from_claim_ids)<=known: raise ValueError("unknown derivation claim ID")
        for interval in self.truth_timeline:
            referenced=set(interval.correct_claim_ids)
            for acceptable in interval.acceptable_outcome_sets: referenced.update(acceptable)
            if not referenced<=known: raise ValueError("truth timeline references unknown claim ID")
        if self.expected_abstention and any(t.status=="established" for t in self.truth_timeline):
            raise ValueError("expected abstention conflicts with established truth")
        return self


def conflict_case_to_episode(case: ConflictCase) -> ConflictEpisode:
    claims=[]
    for stable_id, claim in (("claim-existing",case.existing_claim),("claim-incoming",case.incoming_claim)):
        claims.append(EpisodeClaim(claim_id=stable_id,value=claim.text,semantic_group=claim.text.strip().lower(),
            agent_id=claim.metadata.get("agent_id",claim.source_id),source_id=claim.source_id,
            source_family=claim.source_family,independence_group=claim.metadata.get("independence_group",claim.source_id),
            event_time=claim.asserted_at,issuance_time=claim.asserted_at,ingestion_time=claim.asserted_at,
            expiration_time=claim.valid_until,valid_from=claim.valid_from,valid_until=claim.valid_until,
            evidence_artifacts=case.evidence_artifacts))
    truth=[]
    mapping={"existing":["claim-existing"],"incoming":["claim-incoming"],
             "both_compatible":["claim-existing","claim-incoming"]}
    if case.adjudicated_outcome in mapping:
        truth=[TruthInterval(valid_from=min(c.event_time for c in claims),status="established",correct_claim_ids=mapping[case.adjudicated_outcome])]
    elif case.adjudicated_outcome=="both_wrong": truth=[TruthInterval(valid_from=min(c.event_time for c in claims),status="all_wrong")]
    elif case.adjudicated_outcome=="unresolved": truth=[TruthInterval(valid_from=min(c.event_time for c in claims),status="insufficient_evidence")]
    return ConflictEpisode(episode_id=case.case_id,domain=case.domain,entity_ids=[case.entity_id],
        conflict_family=case.source_family,evaluation_time=max(c.event_time for c in claims),claims=claims,
        truth_timeline=truth,expected_abstention=case.adjudicated_outcome=="unresolved",
        annotations=case.annotator_records,adjudication_status=case.adjudication_status,split=case.split,
        artifact_hashes=case.artifact_hashes)


BLIND_FIELDS = {"lcm_output", "psi", "psi_scores", "baseline_outputs", "hypothesis", "annotator_records", "adjudicated_outcome"}


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_cases(payload: list[dict[str, Any]]) -> list[ConflictCase]:
    cases = [ConflictCase.model_validate(row) for row in payload]
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case_id")
    return cases


def blind_package(cases: list[ConflictCase]) -> list[dict[str, Any]]:
    allowed = {"schema_version", "case_id", "domain", "entity_id", "source_family",
               "existing_claim", "incoming_claim", "evidence_artifacts", "event_timestamps",
               "validity_interval", "artifact_hashes"}
    return [{key: value for key, value in case.model_dump(mode="json").items() if key in allowed}
            for case in cases]


def export_annotation_template(cases: list[ConflictCase]) -> list[dict[str, Any]]:
    return [{**row, "annotation": {"label": None, "rationale": ""}}
            for row in blind_package(cases)]


def import_annotations(cases: list[ConflictCase], package: list[dict[str, Any]],
                       annotator_id: str, annotated_at: datetime) -> list[ConflictCase]:
    by_id = {case.case_id: case.model_copy(deep=True) for case in cases}
    for row in package:
        annotation = row.get("annotation", {})
        if annotation.get("label") is None:
            continue
        case = by_id[row["case_id"]]
        case.annotator_records.append(AnnotatorRecord(
            annotator_id=annotator_id, label=annotation["label"],
            rationale=annotation.get("rationale", ""), annotated_at=annotated_at))
        case.adjudication_status = "in_progress"
    return list(by_id.values())


def adjudicate(case: ConflictCase, outcome: FinalLabel, adjudicator_id: str,
               rationale: str, at: datetime) -> ConflictCase:
    updated = case.model_copy(deep=True)
    updated.annotator_records.append(AnnotatorRecord(
        annotator_id=adjudicator_id, label=outcome, rationale=rationale, annotated_at=at))
    updated.adjudicated_outcome = outcome
    updated.adjudication_status = "adjudicated"
    return updated


def cohens_kappa(a: list[str], b: list[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("equal non-empty rating vectors required")
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb, n = Counter(a), Counter(b), len(a)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if expected == 1.0 else (observed - expected) / (1 - expected)


def krippendorff_alpha_nominal(ratings: list[list[Optional[str]]]) -> float:
    pairs, disagreements, values = 0, 0, []
    for unit in ratings:
        observed = [x for x in unit if x is not None]
        values.extend(observed)
        for i in range(len(observed)):
            for j in range(i + 1, len(observed)):
                pairs += 1; disagreements += observed[i] != observed[j]
    if not pairs or len(values) < 2:
        raise ValueError("insufficient ratings")
    do = disagreements / pairs
    counts, n = Counter(values), len(values)
    de = 1 - sum(count * (count - 1) for count in counts.values()) / (n * (n - 1))
    return 1.0 if de == 0 and do == 0 else 1 - do / de


def duplicate_report(cases: list[ConflictCase], threshold: float = 0.9) -> dict[str, Any]:
    exact, near = [], []
    normalized = {}
    for case in cases:
        text = " ".join((case.existing_claim.text + " " + case.incoming_claim.text).lower().split())
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in normalized:
            exact.append((normalized[digest][0], case.case_id))
        else:
            normalized[digest] = (case.case_id, text)
    items = list(normalized.values())
    for i, (left_id, left) in enumerate(items):
        for right_id, right in items[i + 1:]:
            ratio = SequenceMatcher(None, left, right).ratio()
            if ratio >= threshold and left != right:
                near.append((left_id, right_id, ratio))
    return {"exact": exact, "near": near}


def leakage_report(cases: list[ConflictCase]) -> dict[str, list[tuple[str, list[str]]]]:
    entity, source, temporal = defaultdict(set), defaultdict(set), []
    by_entity = defaultdict(list)
    for case in cases:
        if case.split:
            entity[case.entity_id].add(case.split)
            source[case.existing_claim.source_id].add(case.split)
            source[case.incoming_claim.source_id].add(case.split)
            by_entity[case.entity_id].append(case)
    for entity_id, group in by_entity.items():
        for left in group:
            for right in group:
                if left.split != right.split and left.event_timestamps and right.event_timestamps:
                    if max(left.event_timestamps) >= min(right.event_timestamps):
                        temporal.append((entity_id, sorted({left.split, right.split})))
    return {
        "entity": [(key, sorted(value)) for key, value in entity.items() if len(value) > 1],
        "source": [(key, sorted(value)) for key, value in source.items() if len(value) > 1],
        "temporal": temporal,
    }


def split_manifest(cases: list[ConflictCase]) -> dict[str, Any]:
    assignments = sorted(
        ({"case_id": c.case_id, "split": c.split} for c in cases),
        key=lambda row: row["case_id"])
    return {"schema_version": SCHEMA_VERSION, "assignments": assignments,
            "sha256": canonical_hash(assignments)}


def assert_frozen_test_writable(path: Path, expected_manifest_hash: Optional[str], unlock: bool = False):
    if "test" in {part.lower() for part in path.parts} and not unlock:
        raise PermissionError("frozen test writes require an explicit unlock")
    if path.exists() and expected_manifest_hash is not None:
        current = canonical_hash(json.loads(path.read_text("utf-8")))
        if current != expected_manifest_hash:
            raise PermissionError("frozen artifact hash mismatch")


def dataset_summary(cases: list[ConflictCase]) -> dict[str, Any]:
    return {"case_count": len(cases), "domains": dict(Counter(c.domain for c in cases)),
            "splits": dict(Counter(c.split or "unknown" for c in cases)),
            "statuses": dict(Counter(c.adjudication_status for c in cases)),
            "labels": dict(Counter(c.adjudicated_outcome or "missing" for c in cases)),
            "duplicate_report": duplicate_report(cases), "leakage_report": leakage_report(cases)}
