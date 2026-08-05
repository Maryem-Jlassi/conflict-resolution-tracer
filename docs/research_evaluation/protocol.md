# Preregistered Research Evaluation Protocol

Status: preregistered before final data collection. Pilot observations are excluded from threshold selection.

The 16+3 set is a frozen pilot, not the final held-out dataset. Corrected Benchmark D scenarios are diagnostic. Synthetic A–E benchmarks provide engineering or sensitivity evidence. Real-agent operational trials do not establish correctness without independently adjudicated ground truth. Equal Ψ weights are a symmetry prior, not an optimized result. Critic/verifier win rate is not accuracy. Repeated deterministic trials are not independent observations.

| RQ | Hypothesis | Independent unit | Primary metric | Dataset and baseline | Inference | Success / blocked |
|---|---|---|---|---|---|---|
| RQ1 | LCM improves conflict correctness. | Adjudicated conflict | Accuracy | Final held-out conflicts; recency-only, majority | McNemar; risk difference; cluster-bootstrap 95% CI; Holm | lower CI > 0 / no adjudication or split |
| RQ2 | Provenance improves adversarial robustness. | Attack scenario | Attack success rate | Held-out attacks; no-provenance ablation | Paired permutation; risk ratio; bootstrap CI; Holm | upper CI < 1 / no attack labels |
| RQ3 | Temporal validity reduces stale selections. | Temporal case | Stale-selection rate | Held-out temporal set; no-decay | McNemar; risk difference; bootstrap CI; Holm | difference upper CI < 0 / absent timestamps or labels |
| RQ4 | Trust history improves resolution after sufficient evidence. | Agent-domain block | Accuracy | Held-out blocks; cold-start, fixed-trust | Cluster permutation; accuracy difference; cluster CI; Holm | lower CI > 0 / repeated units |
| RQ5 | Uncertainty-aware trust reduces small-sample overconfidence. | Agent-domain calibration block | Brier score | Held-out histories; naive trust | Paired permutation; Brier difference; bootstrap CI; Holm | upper CI < 0 / absent outcomes |
| RQ6 | Performance transfers across supported frameworks. | Framework × scenario | Macro accuracy | Held-out cross-framework set; local memory | Cochran Q and paired contrasts; macro difference; cluster CI; Holm | noninferiority margin 0.05 / absent framework evidence |
| RQ7 | Security rejects invalid evidence while accepting valid evidence. | Signed request | Balanced accuracy | Valid/tampered/replay/revoked set; signature-only | Exact paired test; difference; Wilson CI; Holm | lower CI > 0.90 / absent call evidence |
| RQ8 | Runtime overhead is bounded. | Workload instance | Median latency ratio | Fixed held-out workload; direct storage | Paired bootstrap; median ratio and CI; Holm | upper CI < 2.0 / uncontrolled hardware or load |

Secondary metrics are macro-F1, calibration error, abstention/coverage, throughput, p95 latency, and failure rate as applicable. Exclude corrupt/hash-invalid artifacts, unknown splits, missing source provenance, unadjudicated correctness labels, duplicate units, incomplete rows, and non-preregistered configurations. Every exclusion is reported.
