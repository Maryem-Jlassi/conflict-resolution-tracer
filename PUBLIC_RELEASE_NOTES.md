# Public Release Notes

## Included

LCM core/service/client code, adapters, ordinary tests, diagnostic benchmark code, protocol and annotation infrastructure, multi-claim replay, metrics/statistics tooling, and pilot-readiness checks.

## Verification

The public export is validated in an isolated environment. Exact observed counts are recorded in `public_release/validation.json`, outside the repository, to avoid stale README claims.

## Evidence status

Synthetic benchmarks are preliminary diagnostic evidence. No final ground truth, frozen-test results, real-agent correctness claims, or headline figures are included.

## Unsupported claims

This release does not establish generalization, production security, clinical/financial suitability, optimized Ψ weights, or empirical superiority over baselines.

## Reproduce

```bash
python -m pip install -e .
python -m pip install pytest pytest-asyncio
python -m pytest tests -q
python tools/validate_public_release.py
python -m research_evaluation.pilot_readiness
```
