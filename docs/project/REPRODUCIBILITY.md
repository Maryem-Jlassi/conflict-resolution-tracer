# Reproducibility

```bash
python -m venv .venv
python -m pip install -e .
python -m pip install pytest pytest-asyncio
python -m pytest tests -q
python tools/validate_public_release.py
```

Ordinary tests exclude `real_ollama`. External datasets, final labels, private annotations, and machine-specific reports are intentionally absent. Public diagnostic artifacts are described in `results/public/README.md`.
