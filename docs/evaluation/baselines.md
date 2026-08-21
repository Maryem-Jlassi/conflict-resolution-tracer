# Baselines

This page describes all implemented baseline policies, how they are replayed against the same frozen input, and the role of oracle analysis.

## Implemented Baseline Policies

All baselines are implemented in `research_evaluation/policies.py` and replayed via `replay()`.

### Recency-Based

| Policy | Logic |
|--------|-------|
| `last_write_wins` | Choose the claim with the later timestamp. |
| `recency_only` | Alias for `last_write_wins`; same recency logic. |

### Incumbent-Based

| Policy | Logic |
|--------|-------|
| `keep_incumbent` | Always choose the existing claim. |

### Random / Abstention

| Policy | Logic |
|--------|-------|
| `case_seeded_random` | Deterministic random choice seeded by the case fingerprint. |
| `always_abstain` | Always return `unresolved`. |

### Majority Vote

| Policy | Logic |
|--------|-------|
| `majority_unique_agent` | One vote per unique `agent_id`. Conflicting duplicates from the same agent invalidate that agent's vote. |
| `majority_independent_source` | One vote per unique `independence_group`. Conflicting duplicates from the same group invalidate that group's vote. |

### Trust-Based

| Policy | Logic |
|--------|-------|
| `fixed_trust` | Compare precomputed `fixed_trust` scores. |
| `global_historical_trust` | Compare global historical trust scores for the claiming agents. |
| `domain_historical_trust` | Compare domain-specific historical trust scores. |

### Evidence-Based

| Policy | Logic |
|--------|-------|
| `evidence_only` | Compare `evidence_score` values. |
| `verified_confidence_only` | Compare `verified_confidence` values. |
| `trust_plus_evidence` | Compare `trust_evidence_score` values (average of trust and evidence). |

### Full CRT

| Policy | Logic |
|--------|-------|
| `full_crt` | Delegate to `ConflictResolutionEngine.classify_scores()` using the canonical Ψ boundary. This is the deployed CRT policy. |

### Aliases

```python
DEPLOYABLE_POLICIES["majority_vote"] = DEPLOYABLE_POLICIES["majority_unique_agent"]
DEPLOYABLE_POLICIES["random_resolver"] = DEPLOYABLE_POLICIES["case_seeded_random"]
DEPLOYABLE_POLICIES["historical_trust"] = DEPLOYABLE_POLICIES["global_historical_trust"]
DEPLOYABLE_POLICIES["domain_specific_trust"] = DEPLOYABLE_POLICIES["domain_historical_trust"]
DEPLOYABLE_POLICIES["full_crt"] = DEPLOYABLE_POLICIES["lcm_full"]
```

## Oracle Analysis

`OracleAnalysisOnly` is a **non-deployable ceiling**:

```python
class OracleAnalysisOnly:
    name = "oracle_analysis_only"
    deployable = False
    def resolve_for_analysis(self, case: PolicyInput, independently_adjudicated_label: Decision) -> PolicyDecision:
        return PolicyDecision(independently_adjudicated_label, 1.0, "analysis ceiling only")
```

Oracle analysis uses independently adjudicated ground truth (`independently_adjudicated_label`) to return the optimal decision. It is **structurally non-deployable** because it requires access to ground truth that is not available at runtime.

Purpose:

- Establishes the theoretical upper bound on accuracy.
- Quantifies the gap between CRT and perfection.
- Never used in production or deployment decisions.

## Replay Against the Same Frozen Input

All baselines and the full CRT policy are replayed against the **exact same frozen input** using `research_evaluation/policies.py::replay()`:

```python
def replay(cases: list[PolicyInput], policy_names: list[str]) -> dict[str, Any]:
    rows = []
    for case in cases:
        fingerprint = case.fingerprint()
        for name in policy_names:
            decision = get_deployable_policy(name).resolve(case)
            rows.append({
                "case_id": case.case_id,
                "case_order": case.case_order,
                "policy": name,
                "decision": decision.decision,
                "confidence": decision.confidence,
                "detail": decision.detail,
                "input_fingerprint": fingerprint,
            })
    return {"schema_version": "2.0", "row_count": len(rows), "rows": rows, "common_input_contract": True}
```

Key properties:

- **Common input contract**: Every policy sees the same `PolicyInput` fields.
- **Deterministic**: `case_seeded_random` uses the case fingerprint as a seed, so results are reproducible.
- **Fingerprint**: Each case has a SHA-256 fingerprint of its full input. Replay asserts that all policies see the same fingerprint for a given case.
- **Non-deployable rejection**: Attempting to replay `oracle_analysis_only` raises `PermissionError`.

## Policy Input Schema

```python
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
    votes: tuple[dict[str, Any], ...] = ()
    validity: tuple[dict[str, Any], ...] = ()
```

## Engineering Validation Policies

`research_evaluation/engineering_evaluation.py` uses this subset for controlled runs:

```python
POLICIES = [
    "last_write_wins", "recency_only", "keep_incumbent", "always_abstain",
    "case_seeded_random", "majority_unique_agent", "majority_independent_source",
    "fixed_trust", "global_historical_trust", "domain_historical_trust",
    "evidence_only", "verified_confidence_only", "trust_plus_evidence", "lcm_full",
]
```
