# Research data workspace

This tree contains tooling inputs only. No final labels or frozen-test results are included.

- `raw/`: acquired, hash-verified source material; immutable after registration.
- `annotations/`: blind per-annotator packages.
- `adjudicated/`: human-adjudicated cases.
- `splits/`: split assignments; test writes are locked by default.
- `manifests/`: canonical hashes and provenance.
- `templates/`: non-labelled schemas and annotation guidance.

Use `python -m research_evaluation.dataset_cli --help`. Annotation packages deliberately omit LCM output, Ψ, baselines, hypotheses, other labels, and adjudicated outcomes.

External preparation: LongMemEval and LoCoMo must be obtained only from their official project sources. Record the official URL, version/commit, license, archive SHA-256 and retrieval time in `manifests/` before adapting. Do not use unofficial mirrors. Acquisition and schema adapters are preparation-only until official data is locally available and validated.
