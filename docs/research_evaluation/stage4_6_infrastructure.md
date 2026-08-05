# Stages 4–6 Infrastructure

This infrastructure does not contain final annotations, run the frozen test, tune Ψ, or publish empirical results.

## Annotation workflow

Validate cases, export blind packages, distribute packages independently, import each annotator’s records, calculate agreement, and escalate disagreement to a named human adjudicator. Only then create split manifests and freeze test assignments. Blind packages omit LCM decisions, Ψ, baseline outputs, hypotheses, other labels, and adjudicated labels. Dataset writes are no-overwrite; paths under a `test` directory are locked unless an explicit programmatic unlock is supplied.

Leakage checks flag exact and near-text duplicates, entities spanning splits, sources spanning splits, and temporally overlapping entity histories across splits. Split manifests use canonical SHA-256 assignments.

## Replay policies

All deployable policies consume the same immutable `PolicyInput` and record its fingerprint per case/policy row. Last-write/recency choose the newest timestamp; majority abstains on the two-claim tie unless independent votes are supplied; random is case-seeded; incumbent and abstention are controls; fixed/global/domain trust, evidence, verified confidence, combined trust/evidence, and full LCM compare only their named precomputed policy inputs. Ground truth is absent from `PolicyInput`. The oracle exists only as a separate non-deployable analysis object and cannot be retrieved from the deployment registry.

## Metrics and inference

Strict accuracy counts exact label matches over independent cases. Coverage is resolved cases/all cases; selective accuracy and risk condition on resolution. Incorrect overwrite counts unsupported incoming selections. False resolution resolves an adjudicated unresolved case; false abstention abstains on a resolvable case. Unresolved precision, recall, and F1 use unresolved as the positive class. Risk–coverage orders confidence descending; AURC integrates its empirical curve. Brier, ECE, log loss, and reliability bins evaluate probabilistic calibration.

Paired comparisons provide exact McNemar, seeded paired permutation, Wilcoxon signed-rank, cluster bootstrap intervals, paired effect sizes, and Holm correction. Repetitions sharing an independent-unit ID are reported separately and never increase the independent-case denominator. Accuracy gates reject missing/unadjudicated truth, diagnostics, unknown splits, and invalid hashes.

Power analysis consumes explicit independently annotated pilot error rates and cluster assumptions. With no qualifying pilot it returns `BLOCKED — no independently annotated pilot results available.`
