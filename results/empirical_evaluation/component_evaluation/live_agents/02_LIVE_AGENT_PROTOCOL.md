# Live Heterogeneous-Agent Evaluation Protocol

**Purpose:** Protocol for real LLM agent evaluation using multiple providers  
**Version:** 1.0  
**Date:** 2026-08-20  
**Status:** Ready for Execution

## 1. Overview

This protocol defines the experimental methodology for evaluating CRT V1 with real heterogeneous LLM agents from multiple providers. This is separate from the controlled integration evaluation and represents actual agent behavior rather than scripted clients.

## 2. Objectives

### Primary Objectives
1. Demonstrate CRT's ability to handle real LLM-generated memory operations
2. Evaluate ingestion and provenance enforcement with authentic agent outputs
3. Test concurrency control under real multi-agent scenarios
4. Compare performance against naive baselines
5. Establish heterogeneous-agent robustness across providers

### Secondary Objectives
1. Measure variability across different LLM providers
2. Evaluate real-world parsing success rates
3. Test forgery resistance against actual LLM behavior
4. Assess scalability under concurrent agent load

## 3. Providers and Models

### Required Providers
- **Ollama** (local, always available):
  - llama3.2:1b
  - llama3.2:latest
  - llama3.1:8b

### Optional Providers (when available)
- **OpenAI**:
  - gpt-4o-mini (configured in .env)
  
- **Groq**:
  - qwen/qwen3.6-27b (configured in .env)

### Provider Priority
1. Ollama (baseline, always tested)
2. OpenAI (when API key available)
3. Groq (when API key available)

### Fallback Behavior
- If a provider is unavailable, continue with available providers
- Record unavailability in results
- Do not fabricate results for unavailable providers
- Minimum requirement: at least 2 different Ollama models

## 4. Agent Roles

### Agent A: Research/Investigation
**Role:** Factual evidence analysis and careful investigation
**System Prompt:** Focus on evidence quality, multiple perspectives, factual claims
**Task:** Analyze information and reach evidence-based conclusions

### Agent B: Summarization/Interpretation
**Role:** Information synthesis and key theme identification
**System Prompt:** Integrate information, identify patterns, provide clear summaries
**Task:** Synthesize information and provide interpretive conclusions

### Agent C: Retrieval/Evidence-Oriented
**Role:** Evidence search and logical reasoning
**System Prompt:** Evaluate evidence quality, build evidence-based arguments
**Task:** Evaluate evidence and provide evidence-based conclusions

### Agent D: Adversarial/Challenging
**Role:** Critical analysis and assumption questioning
**System Prompt:** Question assumptions, identify biases, challenge conventional wisdom
**Task:** Provide critical perspective and alternative views

### Agent Instructions
All agents receive the same task context but must:
- Work independently without coordinating answers
- Report their own conclusions based on their role
- Submit claims to specified memory paths
- NOT include provenance metadata (middleware handles this)
- Accept that other agents may disagree

## 5. Stage 1 Experiments

### S1-LIVE-A: Valid Independent Claims
**Purpose:** Basic ingestion with real agent outputs
**Setup:** Multiple agents generate claims to different paths
**Metrics:**
- Generation success rate
- Parse success rate
- Submission success rate
- HTTP 201 rate
- Middleware stamp coverage
- Latency statistics

### S1-LIVE-B: Natural Conflicting Claims
**Purpose:** Real conflict detection with genuine disagreements
**Setup:** Multiple agents receive same question, submit to same path
**Key Requirement:** Do NOT force disagreement, record natural conflicts
**Metrics:**
- Conflict detection rate
- Coexistence correctness
- Resolution behavior
- Pending conflict handling

### S1-LIVE-C: Malformed Output
**Purpose:** Robustness testing with adversarial prompts
**Setup:** Agents instructed to generate malformed JSON
**Metrics:**
- Malformed rejection rate
- Schema rejection rate
- Parser failure handling
- Middleware safety

### S1-LIVE-D: Provenance Forgery Attempt
**Purpose:** Security testing with actual LLM behavior
**Setup:** Agents instructed to include forbidden fields
**Metrics:**
- Forbidden-field acceptance rate
- Middleware overwrite rate
- Forgery block rate
- Security violation handling

### S1-LIVE-E: Duplicate/Idempotency
**Purpose:** Duplicate handling with real agent behavior
**Setup:** Multiple agents generate similar claims
**Metrics:**
- Duplicate detection rate
- Active memory preservation
- Duplicate amplification control

## 6. Stage 2 Experiments

### LIVE-W1: Independent Paths
**Purpose:** Basic concurrency with independent operations
**Setup:** Multiple agents write to different paths simultaneously
**Metrics:**
- Throughput (requests/sec)
- Latency statistics
- Error rate
- Success rate

### LIVE-W2: Same-Path Compatible Claims
**Purpose:** Duplicate handling under concurrency
**Setup:** Multiple agents write compatible info to same path
**Metrics:**
- False conflict rate
- Retained state correctness
- Duplicate handling

### LIVE-W3: Same-Path Natural Conflicts
**Purpose:** Real conflict resolution under concurrency
**Setup:** Multiple agents independently answer same question
**Key Requirement:** Do NOT predetermine winner
**Metrics:**
- Conflict detection rate
- Resolution behavior
- Final-state consistency
- Pending conflict handling

### LIVE-W4: Burst Contention
**Purpose:** Scaling under increasing concurrency
**Setup:** 2/4/8/16/32 concurrent agents to same path
**Metrics:**
- Throughput at each scale
- Latency p50/p95/p99
- Lock contention
- Timeout rate
- Error rate

### LIVE-W5: Mixed Heterogeneous Workload
**Purpose:** Real-world mixed scenario
**Setup:** Mix of independent, duplicate, conflicting, malformed, forgery attempts
**Metrics:**
- Overall success rate
- Per-operation-type success rate
- Composite behavior correctness

### LIVE-W6: Serial vs Concurrent
**Purpose:** Separates agent variability from middleware behavior
**Setup:** Same frozen operations run serially and concurrently
**Metrics:**
- Serial-concurrent equivalence rate
- Final-state divergence rate
- Determinism across repetitions
- Performance comparison

## 7. Frozen Replay System

### Phase A: Generation
- Real LLM agents generate operations
- Full metadata captured
- Results frozen to corpus

### Phase B: Replay
- Same frozen operations replayed
- Different execution modes tested
- Deterministic reproducibility verified

### Replay Execution Modes
1. **Serial Baseline:** Operations executed one at a time
2. **Concurrent Burst:** All operations executed simultaneously
3. **Scaling Tests:** Different concurrency levels tested
4. **Determinism Tests:** Same workload repeated for consistency

## 8. Metrics Specification

### Stage 1 Metrics (per scenario)
- ingestion_accept_rate = committed+conflict_resolved+unresolved / total submissions
- schema_rejection_rate = HTTP 422 / schema-hostile attempts
- malformed_rejection_rate = HTTP 400 for malformed / malformed attempts
- forbidden_field_rejection_rate = forbidden-field blocks / attempts
- forged_provenance_block_rate = forgery blocks / attempts
- middleware_stamp_coverage = stamped packets / accepted packets
- provenance_completeness = complete provenance / accepted packets
- provenance_integrity = correct provenance fields / stamped packets
- lineage_integrity = correct lineage / stored packets
- parse_success_rate = successfully parsed / generated
- generation_success_rate = successful generation / attempts
- end_to_end_success = successful submissions / operations
- latency_p50_ms = 50th percentile of submission latency
- latency_p95_ms = 95th percentile of submission latency
- latency_p99_ms = 99th percentile of submission latency

### Stage 2 Metrics (per scenario)
- lost_update_rate = lost writes / total writes
- duplicate_corruption_rate = corrupted duplicates / duplicates
- stale_read_rate = stale reads / total reads
- invalid_final_state_rate = invalid states / total paths
- active_memory_violation_rate = path violations / total paths
- lock_failure_rate = lock failures / total attempts
- timeout_rate = timeouts / total attempts
- deadlock_rate = deadlocks / total attempts
- serial_concurrent_equivalence = identical states / paired runs
- state_divergence_rate = divergent states / total runs
- determinism_rate = identical final states / repetitions
- throughput_writes_per_sec = successful writes / total time
- latency_p50_ms = 50th percentile of write latency
- latency_p95_ms = 95th percentile of write latency
- latency_p99_ms = 99th percentile of write latency
- scaling_speedup = serial_time / concurrent_time

### All Metrics Must Include
- Numerator
- Denominator
- Percentage
- Confidence interval (when sample size permits)

## 9. Statistical Analysis

### Small Sample Handling
- Report exact binomial confidence intervals for proportions
- Use bootstrap CIs for latency comparisons
- Mann-Whitney U or appropriate nonparametric tests for comparisons
- Report per-model results before pooling

### Significance Thresholds
- 95% confidence intervals
- No claim of significance without statistical support
- Report limitations when sample sizes are insufficient

## 10. Baseline Comparison

### Baseline Implementations
1. **Naive LWW Dictionary:** Last-writer-wins without conflict detection
2. **Thread-Safe Dictionary:** Basic mutex without conflict resolution
3. **CRT Production:** Full middleware with provenance and conflict resolution

### Comparison Metrics
- Lost update rate (CRT vs baselines)
- Final-state coherence
- Latency overhead
- Throughput advantage
- Conflict detection capability
- Provenance tracking advantage

## 11. Reproducibility Requirements

### LLM Generation
- Stochastic (expected variability)
- Report variability statistics
- Document provider/model versions
- Record prompt hashes

### Frozen Replay
- Deterministic (exact equality required)
- Same frozen operations replayed
- Hash-based state verification
- Exact final-state equality required

### Repetition Counts
- Basic experiments: 3 repetitions
- Where feasible: 5 repetitions
- Determinism tests: 10 repetitions

## 12. Artifacts and Outputs

### Required Artifacts
- 00_README.md
- 01_IMPLEMENTATION_AUDIT.md
- 01_IMPLEMENTATION_AUDIT.json
- 02_LIVE_AGENT_PROTOCOL.md (this file)
- 03_PROVIDER_CONFIGURATION.json
- 04_AGENT_GENERATIONS.jsonl
- 05_AGENT_OPERATIONS.jsonl
- 06_STAGE1_LIVE_RESULTS.json
- 07_STAGE2_LIVE_RESULTS.json
- 08_STAGE2_SCALING_RESULTS.json
- 09_BASELINE_RESULTS.json
- 10_STATISTICAL_ANALYSIS.json
- 11_LATENCY_ANALYSIS.json
- 12_MODEL_HETEROGENEITY.json
- 13_SERIAL_CONCURRENT_COMPARISON.json
- 14_DETERMINISM_RESULTS.json
- 15_REAL_AGENT_REPRODUCIBILITY.json
- 16_REAL_AGENT_VALIDATION_REPORT.json
- 17_REAL_AGENT_EVALUATION_REPORT.md

### Directory Structure
```
live_agents/
├── 00_README.md
├── 01_IMPLEMENTATION_AUDIT.md
├── 01_IMPLEMENTATION_AUDIT.json
├── 02_LIVE_AGENT_PROTOCOL.md
├── 03_PROVIDER_CONFIGURATION.json
├── _harness/
│   ├── provider_adapter.py
│   ├── agent_roles.py
│   ├── live_agent_harness.py
│   ├── stage2_concurrency.py
│   ├── frozen_replay.py
│   └── baseline_comparison.py
├── _corpus/
│   ├── scenario1_corpus.jsonl
│   └── scenario2_corpus.jsonl
├── _replay/
│   ├── replay_manifest.json
│   └── replay_results.json
├── S1_live_experiments/
│   └── scenario_results/
└── S2_live_experiments/
    └── concurrency_results/
```

## 13. Validation Requirements

### Mechanical Validation
- All required artifacts exist
- Hashes match stored values
- Raw JSONL is valid
- Numerator/denominator calculations are correct
- Percentages are reproducible
- Latency statistics match raw observations
- Serial/concurrent runs use identical frozen operations
- No API keys appear in artifacts
- Provider/model labels are valid
- No previous frozen evaluation artifacts were modified
- Implementation hashes are recorded
- Lock implementation identified as production
- Real-agent generations distinguishable from scripted workloads
- Confidence intervals reproducible
- Baseline results reproducible
- All claims map to raw evidence
- No fabricated or manual measurements

### Validation Result
- **PASS:** All validation checks passed
- **PARTIAL:** Some validation checks failed
- **FAIL:** Critical validation checks failed

## 14. Execution Plan

### Phase 1: Setup
1. Verify provider availability
2. Check CRT service status
3. Validate agent role definitions
4. Test provider connectivity

### Phase 2: Stage 1 Execution
1. Run S1-LIVE-A through S1-LIVE-E
2. Generate and save frozen corpus
3. Calculate Stage 1 metrics
4. Save Stage 1 results

### Phase 3: Stage 2 Execution
1. Load frozen corpus from Stage 1
2. Run LIVE-W1 through LIVE-W6
3. Execute scaling tests
4. Run serial vs concurrent comparison
5. Calculate Stage 2 metrics

### Phase 4: Baseline Comparison
1. Run naive LWW dictionary baseline
2. Run thread-safe dictionary baseline
3. Compare with CRT results
4. Calculate comparison metrics

### Phase 5: Analysis
1. Perform statistical analysis
2. Calculate model heterogeneity metrics
3. Generate latency analysis
4. Calculate determinism metrics

### Phase 6: Validation
1. Run mechanical validation
2. Verify all artifacts
3. Check data integrity
4. Generate validation report

### Phase 7: Reporting
1. Generate comprehensive evaluation report
2. Create paper-ready summary
3. Document limitations
4. Archive all artifacts

## 15. Final Claims

### Evidence-Bounded Wording
- "Under the evaluated heterogeneous-agent workloads, CRT maintained..."
- "Real LLM agents from X providers generated and concurrently submitted memory operations..."
- "No general claim without evidence support"

### Prohibited Claims
- "CRT guarantees coherence" (too absolute)
- "Real agents prove the system works" (overstates results)
- Any claim not supported by actual experimental data

### Required Distinguishment
- Explicitly separate controlled evaluation from live agent evaluation
- Distinguish between generation variability and replay determinism
- Clearly state which providers/models were actually used
- Report limitations without minimization

## 16. Limitations

### Known Limitations
- Single-process asyncio architecture (not distributed)
- Lock failures rare under normal workloads
- Provider availability varies
- LLM generation is stochastic
- Sample sizes may be limited
- Ollama models limited to local testing

### Mitigation Strategies
- Document which providers were available
- Report actual sample sizes
- Use confidence intervals for small samples
- Report variability in LLM generation
- Provide both generation and replay metrics