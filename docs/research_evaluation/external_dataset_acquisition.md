# Official External Dataset Acquisition

Preparation only: no dataset was downloaded and no labels were transformed.

## LongMemEval (ICLR 2025)

- Official repository: `https://github.com/xiaowu0162/LongMemEval`
- Official cleaned release: `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned`
- License: MIT, as identified by the official repository.
- Revision: record an immutable Git commit and Hugging Face dataset revision at acquisition time.
- Expected files: `longmemeval_oracle.json`, `longmemeval_s_cleaned.json`, `longmemeval_m_cleaned.json` (the cleaned release names may differ from original paper names).
- Original IDs/labels: preserve `question_id`, `question_type`, `answer`, `answer_session_ids`, and `has_answer` without reinterpretation.
- Citation: Wu et al., “LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory,” ICLR 2025.

## LoCoMo (ACL 2024)

- Official repository: `https://github.com/snap-research/locomo`
- Official data path: `data/locomo10.json` (the repository may also contain `locomo10.zip`).
- License: use the repository’s `LICENSE.txt`; record its SHA-256 and complete terms at acquisition time rather than inferring an SPDX identifier.
- Revision: record an immutable repository commit.
- Expected structure: ten conversation samples with `sample_id`, `conversation`, annotated `event_summary`, and annotated `qa` records containing question, answer, category, and evidence where available.
- Original IDs/labels: preserve `sample_id`, dialog IDs, QA category/answer/evidence, and event summaries without reinterpretation.
- Citation: Maharana et al., “Evaluating Very Long-Term Conversational Memory of LLM Agents,” ACL 2024 / arXiv:2402.17753.

## Validation

After official acquisition, create a manifest containing official URL, revision, license, archive SHA-256, retrieval time, citation, and expected paths. Validate with the official-manifest and structure helpers in `research_evaluation.external_adapters`. Any conflict-label adaptation requires a separately frozen, tested procedure before transformation.
