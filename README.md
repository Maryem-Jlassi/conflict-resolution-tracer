# Living Context Memory (LCM)

LCM is a research prototype for deterministic, provenance-aware memory coherence across multiple agents.

## Status

- Core implementation: complete for the documented prototype scope.
- Engineering validation: strong; see the ordinary test suite.
- Evaluation infrastructure: advanced.
- Pilot annotation: pending human source selection and independent annotation.
- Final empirical validation: incomplete.

Synthetic benchmarks are diagnostic engineering evidence, not proof of generalization. Real-agent activity demonstrates operational execution only; it is not correctness evidence without independently adjudicated ground truth. No final frozen-test results or headline accuracy claims are included.

## Install and test

```bash
python -m venv .venv
# activate the environment
python -m pip install -e .
python -m pip install pytest pytest-asyncio
python -m pytest tests -q
```

See [REPRODUCIBILITY.md](REPRODUCIBILITY.md), [LIMITATIONS.md](LIMITATIONS.md), and [research protocol](docs/research_evaluation/protocol.md).

## License

License selection pending confirmation of university/institutional ownership. See [LICENSE](LICENSE).
