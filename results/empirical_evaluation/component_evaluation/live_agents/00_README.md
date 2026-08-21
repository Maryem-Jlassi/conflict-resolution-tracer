# Live Heterogeneous-Agent Evaluation

**Purpose:** Upgrade CRT V1 evaluation with real LLM agents from multiple providers  
**Status:** In Development  
**Created:** 2026-08-20

## Overview

This directory contains the live heterogeneous-agent evaluation for CRT V1 Stages 1 and 2. This is a **separate evaluation tier** from the existing controlled integration evaluation.

## Directory Structure

```
live_agents/
├── 00_README.md                    # This file
├── 01_IMPLEMENTATION_AUDIT.md      # Lock implementation audit
├── 01_IMPLEMENTATION_AUDIT.json    # Audit results (JSON)
├── 02_LIVE_AGENT_PROTOCOL.md       # Experimental protocol
├── 03_PROVIDER_CONFIGURATION.json  # Provider/model configuration
├── _harness/                       # Agent harness and execution scripts
├── _corpus/                        # Frozen live-agent generations
├── S1_live_experiments/            # Stage 1 experiments
├── S2_live_experiments/            # Stage 2 experiments
├── 04_AGENT_GENERATIONS.jsonl      # Raw LLM generations
├── 05_AGENT_OPERATIONS.jsonl       # Agent operations submitted to CRT
├── 06_STAGE1_LIVE_RESULTS.json     # Stage 1 results
├── 07_STAGE2_LIVE_RESULTS.json     # Stage 2 results
├── 08_STAGE2_SCALING_RESULTS.json  # Scaling results
├── 09_BASELINE_RESULTS.json        # Baseline comparisons
├── 10_STATISTICAL_ANALYSIS.json    # Statistical analysis
├── 11_LATENCY_ANALYSIS.json        # Latency analysis
├── 12_MODEL_HETEROGENEITY.json    # Model comparison
├── 13_SERIAL_CONCURRENT_COMPARISON.json  # Serial vs concurrent
├── 14_DETERMINISM_RESULTS.json     # Determinism analysis
├── 15_REAL_AGENT_REPRODUCIBILITY.json # Reproducibility artifacts
├── 16_REAL_AGENT_VALIDATION_REPORT.json # Validation results
└── 17_REAL_AGENT_EVALUATION_REPORT.md # Final report
```

## Controlled vs Live Evaluation

### Controlled Integration Evaluation (Preserved)
- **Location:** `../_harness/` and `../real_agents/`
- **Agents:** Scripted deterministic clients
- **Purpose:** Validate specific CRT behaviors
- **Status:** Complete and preserved

### Live Heterogeneous-Agent Evaluation (New)
- **Location:** This directory
- **Agents:** Real LLM inference from multiple providers
- **Purpose:** Demonstrate real multi-agent interaction
- **Status:** In development

## Provider Configuration

### Current Providers (from .env)
- **Ollama:** llama3.2:1b, llama3.2:latest, llama3.1:8b
- **OpenAI:** gpt-4o-mini (API key configured)
- **Groq:** qwen/qwen3.6-27b (API key configured)

### Provider Priority
1. Ollama (local, always available)
2. OpenAI (when API key works)
3. Groq (when API key works)

## Agent Roles

Real LLM-based agents with role prompts:
- **Agent A:** Research/investigation
- **Agent B:** Summarization/interpretation  
- **Agent C:** Retrieval/evidence-oriented reasoning
- **Agent D:** Adversarial/challenging agent

## Experimental Design

### Phase A: Generation
- Real LLM agents generate claims
- Captured in frozen corpus
- Full metadata preserved

### Phase B: Replay
- Same frozen operations replayed
- Serial vs concurrent comparison
- Deterministic reproducibility

## Scenarios

### Stage 1 (Ingestion & Provenance)
- S1-LIVE-A: Valid independent claims
- S1-LIVE-B: Natural conflicting claims
- S1-LIVE-C: Malformed output
- S1-LIVE-D: Provenance forgery attempt
- S1-LIVE-E: Duplicate/idempotency

### Stage 2 (Concurrency & Coherence)
- LIVE-W1: Independent paths
- LIVE-W2: Same-path compatible claims
- LIVE-W3: Same-path natural conflicts
- LIVE-W4: Burst contention (2/4/8/16/32 agents)
- LIVE-W5: Mixed heterogeneous workload
- LIVE-W6: Serial vs concurrent comparison

## Metrics

### Stage 1 Metrics
- Ingestion acceptance rate
- Schema rejection rate
- Malformed rejection rate
- Forbidden-field rejection rate
- Forged-provenance block rate
- Middleware-stamp coverage
- Provenance completeness
- Lineage integrity
- Parser success rate
- Generation success rate
- End-to-end submission success
- Latency statistics

### Stage 2 Metrics
- Lost update rate
- Serial-concurrent equivalence rate
- Determinism rate
- Throughput (requests/sec)
- Latency statistics
- Lock contention metrics
- Scaling efficiency

## Status

- [x] Lock implementation audit completed
- [x] Provider configuration verified
- [ ] Provider adapters implemented
- [ ] Agent roles and prompts created
- [ ] Stage 1 experiments implemented
- [ ] Stage 2 experiments implemented
- [ ] Frozen replay system implemented
- [ ] Scaling experiments implemented
- [ ] Baseline experiments implemented
- [ ] Statistical analysis completed
- [ ] Validation completed
- [ ] Final report completed

## Notes

- All API keys must remain in .env (never committed)
- Generation is stochastic (expected variability)
- Replay is deterministic (exact equality required)
- Results must distinguish between live and controlled evaluation
- No modification of existing frozen artifacts