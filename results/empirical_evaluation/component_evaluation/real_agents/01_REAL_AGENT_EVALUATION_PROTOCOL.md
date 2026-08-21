# Real-Agent Evaluation Protocol (CRT V1 Stages 1–2)

**Companion layer** to `STAGE12_EVALUATION_PROTOCOL.md`. Runs real Ollama LLM agents
as heterogeneous writers against the *same* CRT V1 service. This layer does
**not** replace the deterministic controlled evaluation; it adds an
uncontrolled-writer property test. Stage 3 (conflict resolution) and Stage 4
(context optimization) are **not** evaluated here.

## Architecture

```
Ollama API (127.0.0.1:11434)                 CRT service (uvicorn, file SQLite)
   |                                             |
generate phase:                                   |
  agent(model, scenario, rep) -> raw JSON         |
  (real HTTP /api/chat or /api/generate)          |
  store in _corpus/*.jsonl                        |
        |                                         |
submit/replay phase:                              |
  harness reads frozen corpus -> CRT /write       |
  records request/response latency + result       |
  for serial/concurrent arms                      |
```

## Models
Detected at planning time (recorded in `02_AGENT_CONFIGURATION.json`):
- `llama3.2:1b` (Llama, 1.2B params, Q8_0)
- `llama3.2:latest` (Llama, 3.2B, Q4_K_M)
- `llama3.1:8b` (Llama, 8.0B, Q4_K_M)

**Heterogeneity limitation:** all detected models belong to the *Llama* family
(no Qwen/Gemma/Mistral/Phi present). The harness uses all three (they differ in
parameter count, quantization, and context) and reports any
family-specific findings only where directly observed. It does **not** claim
cross-family generality.

## Agent contract
Each agent is given a **scenario** (task + evidence + target `path`), and is told
**NOT** the intended correct answer. The agent responds with a single JSON object:
`{"path": "<memory_key>", "value": "<factual claim>"}`.
- Provenance/`confidence_score`/`evidence`/`provenance_id` are **never** under
  agent control; the harness never injects substantive claims.
- For S1-C/D/E scenarios the agent is *instructed* to produce malformed or
  forged output; its actual (recorded) response is used verbatim → agent
  malformed-output / forgery-attempt rates are measured honestly, including
  refusals.

## Phase separation (latency accounting)
1. **Generation phase** — `agent_generation_latency_ms` = time from first byte
   request to full response (includes model load on first call). Counted as
   `ollama_calls`.
2. **Submission phase** — `middleware_latency_ms` = CRT `/write` round-trip only.
   `end_to_end_latency_ms` = submission only (generation is NOT interleaved into
   the concurrent submission to preserve serial≈concurrent logical-input equality).
3. **Frozen-corpus replay** for the serial-vs-concurrent equivalence: outputs are
   generated **once**, frozen, then replayed in serial mode and in concurrent
   mode against identical CRT configs. This isolates middleware concurrency from
   LLM nondeterminism.
4. A separate **live-agent** arm interleaves generation + submission; it is used
   for heterogeneity/perf only, **never** for the serial≈concurrent equivalence
   claim.

## Scenarios
### Stage 1 (real agents against the ingestion boundary)
- **S1-A valid independent writes**: each agent independently writes a valid
  observation to a distinct path. Measures ingestion success, schema validity,
  provenance attachment, author/source attribution, timestamp integrity, lineage.
- **S1-B conflicting valid writes**: two agents write contradictory values to the
  same path (each only sees its own figure). Both writes valid; measure
  provenance preserved, conflict distinguishable, no author substitution, no
  lineage corruption.
- **S1-C missing required fields**: agents instructed to emit a packet missing
  `path`. Measures rejection rate, rejection reason, false-acceptance rate, and
  the rate at which agents actually comply with the malformed instruction.
- **S1-D malformed packets**: agents instructed to emit malformed structure
  (non-dict payload / empty / non-string). Measures schema rejection +
  false-acceptance.
- **S1-E forged provenance**: agents instructed to attempt to inject fake
  `provenance_id`/`evidence_records`/`verified_confidence`. Measures forged-
  provenance block rate and middleware-owned stamp rate.
- **S1-F duplicate/idempotent**: an agent re-submits its own prior valid claim.
  Measures duplicate handling, state integrity, provenance preservation.

### Stage 2 (real concurrent writers)
Replay frozen corpora through CRT:
- **W1 independent keys** (contention-free baseline): measure concurrency
  overhead vs serial.
- **W2 same-key compatible claims**: agents write compatible info to one path.
- **W3 same-key conflicting claims**: agents write conflicting claims to one
  path → measure coherence, lost updates, conflict resolution.
- **W4 burst contention**: N agents hammer one path; N∈{2,4,8,16} where the
  environment permits (no fabrication of unsupported levels).
- **W5 mixed workload**: independent + compatible + conflicting + duplicates +
  malformed in one burst.

For every deterministic workload: run **serial** (fixed order) and **concurrent**
(same frozen logical operations). Invariant: `F_concurrent == F_serial` (canonical
live-memory projection), zero lost updates, zero coherence corruption,
deterministic across R≥10 repetitions.

## Reps
- S1 scenarios: R=10 per (model × scenario); distinct prompts so outputs differ.
- S2 deterministic workloads: generate-once-freeze, replay R=10 per
  (workload × mode × model-triple). Live-agent concurrent arm R=5.

## Baseline (no-CRT)
A harness-only "naive shared dict" writer (no locks, last-writer-wins) receives
the identical frozen corpus concurrently; compare loss/inconsistency/determinism
to CRT. Implemented entirely in the harness (no production code changes).
