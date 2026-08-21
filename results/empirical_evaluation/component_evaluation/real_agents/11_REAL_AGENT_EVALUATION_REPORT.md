# Real-Agent (Ollama LLM) Evaluation Report - CRT V1

**Generated:** 2026-08-20T02:37:25.337563+00:00
**Models:** llama3.2:1b, llama3.2:latest, llama3.1:8b
**Overall:** PASS (PASS=12, PARTIAL=0, FAIL=0)

## Heterogeneity limitation
All models are llama-family (1.2B/3.2B/8.0B + Q8_0/Q4_K_M). Cross-family generality NOT claimed.

## Phase separation
Generation = Ollama /api/chat. Submission = CRT /write. Frozen corpora replayed serially vs concurrently against fresh DBs.

## Stage 1 results
- **S1-A_valid_ingestion**: PASS ({"total": 30, "parse_errors": 0, "parseable_accepted": 30})
- **S1-B_conflicting_coexist**: PASS ({"total": 20, "parse_errors": 0, "accepted": 5})
- **S1-C_missing_path_rejected**: PASS ({"total": 30, "parse_errors": 0, "accepted": 0})
- **S1-D_malformed_rejected**: PASS ({"total": 30, "parse_errors": 0, "accepted": 0})
- **S1-E_forgery_resistance**: PASS ({"total": 30, "forged_degraded": 25, "middleware_stamp": 5})
- **S1-F_duplicate_handling**: PASS ({"total": 12, "active_value_preserved": 12})

## Stage 2 results
- **W1**: serial_identical=True, concurrent_identical=False, serial_vs_concurrent_equal=False, no_lost_updates=30==30 (ops=30, reps=10)
- **W3**: serial_identical=True, concurrent_identical=False, serial_vs_concurrent_equal=False, no_lost_updates=20==20 (ops=20, reps=10)
- **W2**: serial_identical=True, concurrent_identical=False, serial_vs_concurrent_equal=False, no_lost_updates=12==12 (ops=12, reps=10)
- **W4 burst**: single_active_per_path=True
- **W5 mixed**: no_lost_updates=True

## Key findings
- S1-B: equal-authority real-agent claims coexist as unresolved (accepted=5/20). Expected with 0.05 uncertainty threshold.
- W1/W2/W3/W5: Serial mode is fully deterministic across reps. Concurrent mode is NOT (for equal-authority real-agent claims): middleware conflict-detection race produces spurious pending_conflict on some paths. No writes lost in either mode.
- W4: burst contention resolves to single active per contested path (PASS).
- No-CRT baseline: naive last-writer-wins dict loses writes under concurrency, demonstrating CRT coherence advantage.

## Reproducibility
- Frozen module hashes: 22 modules.
- Re-run: python _harness/run_real_eval.py