from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

Decision = Literal["existing", "incoming", "both_compatible", "both_wrong", "unresolved"]


@dataclass(frozen=True)
class PolicyInput:
    case_id: str
    existing_claim: dict[str, Any]
    incoming_claim: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    timestamps: tuple[str, ...]
    trust_history: tuple[dict[str, Any], ...]
    source_metadata: dict[str, Any]
    case_order: int
    inclusion_rules: tuple[str, ...] = ()
    exclusion_rules: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    confidence: Optional[float] = None
    detail: str = ""


class ReplayPolicy:
    name: str
    deployable: bool = True
    def resolve(self, case: PolicyInput) -> PolicyDecision: raise NotImplementedError


class FunctionPolicy(ReplayPolicy):
    def __init__(self, name: str, fn: Callable[[PolicyInput], PolicyDecision]): self.name, self.fn = name, fn
    def resolve(self, case): return self.fn(case)


def _recency(case):
    left = case.existing_claim.get("asserted_at", "")
    right = case.incoming_claim.get("asserted_at", "")
    return PolicyDecision("incoming" if right > left else "existing" if left > right else "unresolved")


def _score(case, key):
    left = float(case.existing_claim.get(key, 0.0)); right = float(case.incoming_claim.get(key, 0.0))
    return PolicyDecision("incoming" if right > left else "existing" if left > right else "unresolved")


def _trust(case, domain_only=False):
    domain = case.source_metadata.get("domain")
    relevant = [row for row in case.trust_history if not domain_only or row.get("domain") == domain]
    scores = {row.get("source_id"): row.get("trust", 0.5) for row in relevant}
    left = scores.get(case.existing_claim.get("source_id"), 0.5)
    right = scores.get(case.incoming_claim.get("source_id"), 0.5)
    return PolicyDecision("incoming" if right > left else "existing" if left > right else "unresolved")


DEPLOYABLE_POLICIES: dict[str, ReplayPolicy] = {
    "last_write_wins": FunctionPolicy("last_write_wins", _recency),
    "recency_only": FunctionPolicy("recency_only", _recency),
    "majority_vote": FunctionPolicy("majority_vote", lambda c: PolicyDecision("unresolved", detail="two-claim tie without independent votes")),
    "random_resolver": FunctionPolicy("random_resolver", lambda c: PolicyDecision(random.Random(c.case_id).choice(["existing", "incoming"]))),
    "keep_incumbent": FunctionPolicy("keep_incumbent", lambda c: PolicyDecision("existing")),
    "always_abstain": FunctionPolicy("always_abstain", lambda c: PolicyDecision("unresolved")),
    "fixed_trust": FunctionPolicy("fixed_trust", lambda c: _score(c, "fixed_trust")),
    "global_historical_trust": FunctionPolicy("global_historical_trust", lambda c: _trust(c, False)),
    "domain_historical_trust": FunctionPolicy("domain_historical_trust", lambda c: _trust(c, True)),
    "evidence_only": FunctionPolicy("evidence_only", lambda c: _score(c, "evidence_score")),
    "verified_confidence_only": FunctionPolicy("verified_confidence_only", lambda c: _score(c, "verified_confidence")),
    "trust_plus_evidence": FunctionPolicy("trust_plus_evidence", lambda c: _score(c, "trust_evidence_score")),
    "lcm_full": FunctionPolicy("lcm_full", lambda c: _score(c, "lcm_score")),
}


class OracleAnalysisOnly:
    name = "oracle_analysis_only"
    deployable = False
    def resolve_for_analysis(self, case: PolicyInput, independently_adjudicated_label: Decision) -> PolicyDecision:
        return PolicyDecision(independently_adjudicated_label, 1.0, "analysis ceiling only")


ORACLE_ANALYSIS_ONLY = OracleAnalysisOnly()


@dataclass(frozen=True)
class EpisodePolicyInput:
    episode_id: str
    claims: tuple[dict[str, Any], ...]
    evaluation_time: str
    domain: str
    inclusion_rules: tuple[str, ...] = ()
    exclusion_rules: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__,sort_keys=True,default=str).encode()).hexdigest()


def _winner(scores: dict[str,float], tie_policy: str, claims: tuple[dict[str,Any], ...]):
    if not scores: return "unresolved"
    best=max(scores.values()); groups=[group for group,value in scores.items() if value==best]
    if len(groups)==1: return groups[0]
    if tie_policy=="most_recent":
        for claim in reversed(claims):
            if claim["semantic_group"] in groups: return claim["semantic_group"]
    return "unresolved"


def episode_replay(episode: EpisodePolicyInput, policy_names: list[str], tie_policy="abstain"):
    allowed={"last_write_wins","recency_only","majority_raw_agent","majority_independent_source",
             "evidence_only","trust_only","lcm_full","always_abstain"}
    unknown=set(policy_names)-allowed
    if unknown or "oracle_analysis_only" in policy_names: raise PermissionError(f"non-deployable or unknown policy: {sorted(unknown)}")
    rows=[]
    for name in policy_names:
        seen=[]
        for index,claim in enumerate(episode.claims):
            seen.append(claim); subset=tuple(seen)
            if name in {"last_write_wins","recency_only"}: decision=max(subset,key=lambda c:c["event_time"])["semantic_group"]
            elif name=="always_abstain": decision="unresolved"
            elif name in {"majority_raw_agent","majority_independent_source"}:
                identity="agent_id" if name=="majority_raw_agent" else "independence_group"
                votes={c[identity]:c["semantic_group"] for c in subset}
                counts={group:sum(value==group for value in votes.values()) for group in set(votes.values())}
                decision=_winner(counts,tie_policy,subset)
            else:
                key={"evidence_only":"evidence_score","trust_only":"trust_score","lcm_full":"lcm_score"}[name]
                scores={}
                for c in subset: scores[c["semantic_group"]]=max(scores.get(c["semantic_group"],float("-inf")),float(c.get(key,0)))
                decision=_winner(scores,tie_policy,subset)
            rows.append({"episode_id":episode.episode_id,"policy":name,"after_claim_id":claim["claim_id"],
                         "claim_index":index,"decision_semantic_group":decision,
                         "input_fingerprint":episode.fingerprint(),"tie_policy":tie_policy})
    return {"schema_version":"2.0","row_count":len(rows),"rows":rows,
            "pairwise_majority_warning":len({c["semantic_group"] for c in episode.claims})==2 and len(episode.claims)==2}


def get_deployable_policy(name: str) -> ReplayPolicy:
    if name == "oracle_analysis_only":
        raise PermissionError("oracle_analysis_only is structurally non-deployable")
    return DEPLOYABLE_POLICIES[name]


def replay(cases: list[PolicyInput], policy_names: list[str]) -> dict[str, Any]:
    rows = []
    for case in cases:
        fingerprint = case.fingerprint()
        for name in policy_names:
            decision = get_deployable_policy(name).resolve(case)
            rows.append({"case_id": case.case_id, "case_order": case.case_order,
                         "policy": name, "decision": decision.decision,
                         "confidence": decision.confidence, "detail": decision.detail,
                         "input_fingerprint": fingerprint})
    return {"schema_version": "1.0", "row_count": len(rows), "rows": rows}
