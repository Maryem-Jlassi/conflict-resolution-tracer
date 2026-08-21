"""Generate stochastic mechanism test suite for MSM evaluation."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
OUT_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "msm" / "_sweep_results"


def generate_suite(n_cases: int = 50, seed: int = 20260821):
    dataset = common.load_dataset()
    cases = common.select_cases(dataset, common.N_CASES)
    rng = random.Random(seed)
    chosen = rng.sample(cases, min(n_cases, len(cases)))

    suite = []
    for case in chosen:
        cid = case["annotation_task_id"]
        sources = case.get("sources", [])
        contexts = case.get("contexts", [])
        entry = {
            "case_id": cid,
            "question": case.get("question", ""),
            "gold": common.qacc_gold(case),
            "n_contexts": len(contexts),
            "n_sources": len(sources),
            "source_types": [common.classify_source_type(s.get("source", "")) for s in sources],
            "authorities": [common.source_authority(s.get("source", "")) for s in sources],
        }
        suite.append(entry)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"stochastic_suite_{n_cases}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(suite, fh, indent=2, default=str)
    print(f"wrote {len(suite)} cases -> {out_path}")
    return suite


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    generate_suite(n)


if __name__ == "__main__":
    main()
