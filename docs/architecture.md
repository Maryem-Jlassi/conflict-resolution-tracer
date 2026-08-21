# CRT Architecture

This document contains the canonical architecture diagrams for the CRT repository. All diagrams are rendered as Mermaid code blocks for native GitHub rendering. No external tooling is required.

## Table of Contents

1. [Four-Stage Pipeline](#1-four-stage-pipeline)
2. [Ψ Calculation Flow](#2-ψ-calculation-flow)
3. [Concurrency / Locking Sequence](#3-concurrency--locking-sequence)
4. [Evaluation Architecture](#4-evaluation-architecture)

---

## 1. Four-Stage Pipeline

Every write from an agent travels through this pipeline in order. The pipeline is the ONLY place that knows the stage order; each module can be swapped independently.

```mermaid
flowchart LR
    A["Agent Claim<br>raw dict"] --> B["Stage 1<br>validate_and_stamp<br>Provenance + Confidence"]
    B --> C["Stage 2<br>LoopDetector<br>Oscillation check"]
    C --> D["Stage 3<br>AsyncLockManager<br>Path-level write lock"]
    D --> E["Stage 4<br>ConflictResolutionEngine<br>Ψ = (R + C + T) / 3"]
    E --> F["Storage<br>Commit winner, archive loser"]
    F --> G["Optional<br>AutoVerifier"]
    G --> H["TrustManager<br>record_outcome"]

```

**Source:** `crt_core/pipeline.py` lines 1–24.

---

## 2. Ψ Calculation Flow

The V1 conflict engine computes Ψ = (R + C + T) / 3. Provenance is a mandatory Stage-1 audit layer; it does NOT contribute a scalar term to Ψ.

```mermaid
flowchart LR
    U["UMF Packet"] --> P["ProvenanceInfo<br>extraction"]
    P --> R["R = e^(-λΔt)<br>Recency decay"]
    P --> C["C = verified_confidence<br>from ProvenanceInfo"]
    P --> T["T = trust_score<br>from TrustManager"]
    R --> W["weighted_total<br>w_r·R + w_c·C + w_t·T"]
    C --> W
    T --> W
    W --> CR["ConflictResult<br>winner / loser / margin"]
```

**Source:** `crt_core/conflict.py` lines 147–199.

**Weights:** Default w_r = w_c = w_t = 1/3. All weights must sum to 1.0 (`ResolutionConfig.validate()`).

**Uncertainty threshold:** |Ψ_A - Ψ_B| < θ → unresolved. Default θ = 0.05.

---

## 3. Concurrency / Locking Sequence

Two concurrent writers attempting to write to the same path. The lock manager uses token-based acquire/release. The owner-token fix ensures only the holder can release.

```mermaid
sequenceDiagram
    participant W1 as Writer A
    participant W2 as Writer B
    participant LM as AsyncLockManager
    participant CRE as ConflictResolutionEngine
    participant ST as Storage

    W1->>LM: acquire_write_lock(path)
    LM-->>W1: LockResult(acquired=True, token="t1")
    W2->>LM: acquire_write_lock(path)
    LM-->>W2: LockResult(acquired=False)

    W1->>CRE: resolve(existing, incoming, trust_scores)
    CRE-->>W1: ConflictResult(winner, loser, psi_margin)
    W1->>ST: commit(winner), archive(loser)
    W1->>LM: release_write_lock(path, token="t1")
```

**Source:** `crt_core/locking.py` lines 81–108; `crt_core/pipeline.py` lines 78–200.

**Note:** Concurrent mode shows spurious `pending_conflict` outcomes on distinct paths due to a middleware race (confirmed in `results/empirical_evaluation/msm/_sweep_results/B2_pending_conflict_evidence.json`). Serial mode is deterministic.

---

## 4. Evaluation Architecture

Three independent evaluation tracks feed into the citable-claims summary. Each track has distinct capabilities and limitations.

```mermaid
flowchart TD
    subgraph MSM[MSM Track — Synthetic Structural]
        M1[Pooled 4-seed DEV<br/>192 personas, 3,456 units]
        M2[Controlled R/C/T variance]
        M3[Identifiability analysis]
        M1 --> M2 --> M3
    end

    subgraph PHEME[PHEME Track — Real-World Generalization]
        P1[9 breaking-news events<br/>6,425 threads, 105k tweets]
        P2[Real temporal structure]
        P3[R/C/T genuinely vary]
        P1 --> P2 --> P3
    end

    subgraph QACC[QACC Track — Real-Agent Multi-Provider]
        Q1[500 cases, 4,824 sources]
        Q2[3 providers: OpenAI, Ollama, Groq]
        Q3[Real conflicting web evidence]
        Q1 --> Q2 --> Q3
    end

    MSM --> CC[Citable Claims Summary]
    PHEME --> CC
    QACC --> CC

    CC --> R[README + Technical Report]
```

**Track capabilities:**

| Track | Can test | Cannot test |
|-------|----------|-------------|
| MSM | Structural identifiability of R, C, T; weight sensitivity; guardrail behavior | Real-world extraction quality; provider behavior |
| PHEME | Generalization to real rumors; R/C/T variance | Trust calibration (single-domain); stance extraction quality |
| QACC | Real conflicting evidence; multi-provider extraction; provider-blind resolver | Recency/trust signal (neutral); groq model quality (rate-limited) |

**Full reports:**
- MSM: `results/empirical_evaluation/msm/_sweep_results/SWEEP_PLAN_RESULTS.md`
- PHEME: `results/empirical_evaluation/pheme_test/PHEME_FINAL_EVALUATION_REPORT.md`
- QACC: `results/empirical_evaluation/component_evaluation/qacc/_frozen_assertions_500_multiprovider/QACC_500_MULTIPROVIDER_RESULTS.md`
