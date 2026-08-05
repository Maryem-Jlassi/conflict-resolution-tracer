# Statistical Analysis Plan

Analyses follow `protocol.md`; pilot outcomes do not set thresholds. The named independent unit is the resampling and inference unit. Deterministic repetitions of one case measure stability, not sample size.

Primary analyses use two-sided α=0.05 and 95% confidence intervals, with Holm correction across eight primary hypotheses. Binary paired outcomes use McNemar or exact paired tests; clustered and continuous effects use permutation tests and cluster-bootstrap intervals (10,000 resamples with a recorded seed). Report effects and confidence intervals, not p-values alone. Operational failures count as failures; estimand-specific exclusions retain an explicit reason. Subgroups and sensitivity analyses are exploratory.

Before analysis validate canonical hash, source provenance, split, row count, independent-unit IDs, ground truth/adjudication, model-call evidence, and run state. Any failure makes the artifact headline-ineligible.
