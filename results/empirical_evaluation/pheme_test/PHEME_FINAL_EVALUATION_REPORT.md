# PHEME TEST Final Evaluation Report (Phase 8)

## Scientific Objective
"Does the full CRT V1 mechanism provide useful conflict-resolution behavior when Recency (R), Confidence (C), and Trust (T) genuinely vary, using a real-world, human-adjudicated dataset without current-case gold leakage?"

## Dataset
- **PHEME** (Zubiaga et al. 2016, figshare 6392078)
- 9 breaking-news events, 6425 threads, 105354 tweets
- TEST split: ['charliehebdo', 'putinmissing', 'germanwings-crash']
- TRAIN split (for trust): ['ferguson', 'ebola-essien', 'ottawashooting', 'prince-toronto', 'gurlitt', 'sydneysiege']

## Stance Extraction
- Model: pheme_stance_v1.0_deterministic (frozen, deterministic, gold-blind)
- Total tweets extracted: 105354 (all tweets in archive)
- Gold boundary: current gold → stance = FALSE

## TEST Results (n=1950 episodes)

### Main Methods
| Method | Strict Accuracy | Coverage | Abstention | Mean Margin |
|---|---|---|---|---|
| crt_v1 | 0.130 | 0.737 | 0.263 | 0.10934 |
| last_write_wins | 0.160 | 1.000 | 0.000 | 1.0 |
| recency_only | 0.160 | 1.000 | 0.000 | 1.0 |
| highest_confidence | 0.136 | 1.000 | 0.000 | 1.0 |
| highest_trust | 0.155 | 1.000 | 0.000 | 1.0 |
| majority_independent_source | 0.157 | 1.000 | 0.000 | 1.0 |
| fixed_trust | 0.136 | 1.000 | 0.000 | 1.0 |
| evidence_only | 0.136 | 1.000 | 0.000 | 1.0 |
| trust_plus_evidence | 0.140 | 1.000 | 0.000 | 1.0 |
| c_only | 0.136 | 1.000 | 0.000 | 1.0 |

### Ablations
| Ablation | Strict Accuracy | Coverage |
|---|---|---|
| Full_CRT | 0.154 | 1.000 |
| R_only | 0.160 | 1.000 |
| C_only | 0.136 | 1.000 |
| T_only | 0.155 | 1.000 |
| R_plus_C | 0.144 | 1.000 |
| R_plus_T | 0.165 | 1.000 |
| C_plus_T | 0.147 | 1.000 |

## Component Discrimination
- Removing R changes decision in 145/1950 episodes (7.44%)
- Removing C changes decision in 593/1950 episodes (30.41%)
- Removing T changes decision in 202/1950 episodes (10.36%)

## CRT V1 vs C-only
- CRT V1 accuracy: 0.130
- C-only accuracy: 0.136
- Delta: -0.006

## MSM Reference
On MSM (Multi-Source DEV), Full CRT V1 was exactly equivalent to C-only (1440/1440) because R and T were invariant in that controlled setting. PHEME is the independent mechanism-evaluation track where R, C, and T vary.

## Limitations
- Stance extraction is deterministic lexicon-based; not state-of-the-art.
- Thread-level gold mapped to tweet-level predictions.
- T is conservative (0.7 prior for users with any train history); no per-user correctness tracking without stance gold.
- PHEME is a single-domain (Twitter rumours) dataset.

## Conclusion
PHEME TEST provides mechanistic evidence for whether R, C, and T contribute to CRT V1 conflict resolution under real-world temporal structure. The MSM and PHEME tracks are complementary: MSM showed V1 collapses to C-only when R and T are invariant; PHEME tests whether V1 can leverage R, C, and T when they genuinely vary.
