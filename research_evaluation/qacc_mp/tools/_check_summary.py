"""Print summary of QACC evaluation results."""
from __future__ import annotations

import json
from pathlib import Path

from research_evaluation.qacc_mp import common


REPO_ROOT = Path(__file__).resolve().parent
RUN_DIR = REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc" / "_frozen_assertions_500_multiprovider" / "RUN"


def main():
    if not (RUN_DIR / "analysis.json").exists():
        print("analysis.json not found. Run qacc_frozen_eval.py first.")
        return

    data = json.loads((RUN_DIR / "analysis.json").read_text(encoding="utf-8"))
    manifest = data["manifest"]
    print("=== QACC Summary ===\n")
    print(f"Cases        : {manifest['n_cases']}")
    print(f"Sources      : {manifest['total_sources']}")
    print(f"Providers    : {manifest['provider_counts']}")

    ro = data["4_2_resolution_outcomes"]
    clean = ro["clean_subset_openai_ollama"]
    print(f"Clean subset : {ro['clean_subset_definition']['n_cases_clean']} cases")
    print(f"Resolved     : {clean['resolved_cases_full_lcm']}")
    print(f"OpenAI wins  : {clean['winners_by_provider']['openai']}")
    print(f"Ollama wins  : {clean['winners_by_provider']['ollama']}")

    pb = data["4_3_provider_blindness"]
    print(f"Provider blind: {pb['provider_blind_verification']} (mismatches={pb['n_authority_mismatches']})")

    agree = data.get("4_4_agreement_reclassified", {})
    if agree:
        print(f"Agreement rate: {agree.get('genuine_agreement', 0)}/{agree.get('measurable_total', 0)} = {agree.get('agreement_rate_b', 'N/A')}")


if __name__ == "__main__":
    main()
