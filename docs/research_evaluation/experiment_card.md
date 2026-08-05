# Experiment Card

Each experiment binds one preregistered RQ to a frozen dataset version, source manifest, environment, model-call evidence, baseline, configuration, seed, and immutable output. States are `completed`, `failed`, `blocked`, or `skipped`; only completed runs enter analysis.

Writers use atomic no-overwrite writes and canonical JSON hashes. Rows identify independent units and ground-truth provenance. Dirty runs preserve HEAD plus tracked, staged, untracked, and complete-source manifests. Operational Ollama trials without adjudicated labels remain publication-ineligible. Deviations require a dated amendment before affected outcomes are inspected.
