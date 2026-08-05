"""
Benchmark F — Uncertainty-aware trust vs naive point-estimate baseline (Phase 3)

Deterministic model-property demonstration, NOT an empirical measurement.

The naive trust model ``correct/total`` treats one correct verification as
trust == 1.0 (maximal) and 99/100 as trust == 0.99 — a 1% difference that hides
enormously different levels of evidence. This benchmark tabulates the
uncertainty-aware profile (Wilson interval, uncertainty, conservative score)
for a sweep of outcome counts so the Phase 12 metrics can quantify how much a
small sample can overstate reliability.

Output rows are tagged ``diagnostic`` — they are computed properties of the
model, not observations of real agents.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.trust_manager import TrustManager


def trust_profile_sweep(max_outcomes: int = 50) -> List[Dict[str, Any]]:
    """
    For each outcome count ``n`` (all correct), compare the naive point
    estimate against the uncertainty-aware conservative score.

    Rows are tagged ``diagnostic``: pure model properties, not observations.
    """
    rows: List[Dict[str, Any]] = []
    for n in range(1, max_outcomes + 1):
        m = TrustManager()
        for _ in range(n):
            m.record_outcome("agent", correct=True)
        prof = m.get_trust_with_uncertainty("agent")
        rows.append({
            "tag": "diagnostic",
            "outcome_count": n,
            "naive_trust": prof["naive_trust"],
            "uncertainty": prof["uncertainty"],
            "interval_low": prof["interval_low"],
            "interval_high": prof["interval_high"],
            "conservative_trust": prof["conservative_trust"],
            "overstatement_gap": prof["naive_trust"] - prof["conservative_trust"],
        })
    return rows


def trust_model_comparison_table() -> List[Dict[str, Any]]:
    """
    Headline numbers for the Phase 12 metrics: how a small sample inflates
    naive trust vs the uncertainty-penalized conservative score.

    Rows are tagged ``diagnostic``.
    """
    cases = [
        (1, 1),       # one correct outcome
        (1, 0),       # one incorrect outcome
        (5, 5),       # five correct
        (10, 9),      # 9/10
        (50, 45),     # 45/50
        (100, 95),    # 95/100
    ]
    rows: List[Dict[str, Any]] = []
    for total, correct in cases:
        m = TrustManager()
        for i in range(total):
            m.record_outcome("agent", correct=(i < correct))
        cmp = m.compare_trust_models("agent")
        prof = m.get_trust_with_uncertainty("agent")
        rows.append({
            "tag": "diagnostic",
            "outcome_count": total,
            "correct_count": correct,
            "naive_trust": cmp["naive"],
            "conservative_trust": cmp["conservative"],
            "difference": cmp["difference"],
            "uncertainty": prof["uncertainty"],
        })
    return rows


if __name__ == "__main__":
    table = trust_model_comparison_table()
    print(f"{'n':>4} {'k':>4} {'naive':>7} {'conservative':>13} {'diff':>7} {'uncertainty':>11}")
    for r in table:
        print(
            f"{r['outcome_count']:>4} {r['correct_count']:>4} "
            f"{r['naive_trust']:>7.4f} {r['conservative_trust']:>13.4f} "
            f"{r['difference']:>7.4f} {r['uncertainty']:>11.4f}"
        )
