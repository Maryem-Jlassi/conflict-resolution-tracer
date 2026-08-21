# Active evaluation metrics

CRT is evaluated as a deterministic conflict-resolution policy that may abstain. Every rate is accompanied by its numerator and exact denominator.

| Metric | Definition |
|---|---|
| Strict accuracy | Correct decisions / all cases. Unresolved decisions count as incorrect unless unresolved is the gold outcome. |
| Coverage | Resolved decisions / all cases. |
| Selective accuracy | Correct resolved decisions / resolved decisions. |
| Incorrect overwrite rate | Unsupported incoming overwrites / applicable cases. |
| False resolution rate | Forced resolutions when the gold outcome is unresolved / gold-unresolved cases. |
| False abstention rate | Unresolved decisions when a resolvable answer exists / gold-resolvable cases. |
| Unresolved rate | Unresolved decisions / all cases. |

Supporting output includes total, correct, incorrect and unresolved counts; wins, losses and ties against each baseline; per-family/per-track tables; conflict detection where the denominator is valid; component discrimination counts; and ablation decision-change counts. Selective risk is optional and, when present, is only `1 - selective_accuracy`.

The composite CRT score is a deterministic resolution score, not a calibrated probability. The active methodology does not compute confidence intervals, p-values, bootstrap results, Brier score, ECE, log loss, reliability diagrams, calibration curves, or AURC.

For preregistered threshold sensitivity only, a descriptive risk–coverage curve plots coverage against observed error among resolved decisions. It is not summarized, optimized, or interpreted as calibration. Score-margin reports include exact ties, near ties, quantiles, and the proportion below every threshold. Incorrect overwrite is displayed prominently and must state whether it means every incorrect commitment or, as in the sensitivity study, the narrower act of accepting a wrong newest-arriving candidate.
