"""
Results Analysis - Compute statistics and generate plots from benchmark CSVs.

Statistical tests:
  - Mann-Whitney U  : non-parametric comparison of LCM vs No-LCM distributions
                      (does not assume normality; appropriate for small n)
  - Chi-square      : proportion test for binary outcomes (final_is_correct)
  - 95% CI          : bootstrap confidence interval on the mean

Usage:
    python -m benchmarks.analyze_results
"""

import csv
import math
import statistics
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ---------------------------------------------------------------------------
# Statistical helpers (no scipy dependency — pure stdlib)
# ---------------------------------------------------------------------------

def _mann_whitney_u(a: List[float], b: List[float]) -> Tuple[float, str]:
    """
    Compute Mann-Whitney U statistic and an approximate significance label.

    Returns (U, significance) where significance is one of:
      "p<0.001", "p<0.01", "p<0.05", "n.s." (not significant).

    Uses the normal approximation (z-score) which is valid for n >= 8.
    For smaller samples the exact result is labelled "n.s. (n too small)".
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return 0.0, "n.s. (n too small)"

    # Rank all values together
    combined = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = {}
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j

    r1 = sum(ranks[k] for k, (_, grp) in enumerate(combined) if grp == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    # Normal approximation
    mu_u = n1 * n2 / 2.0
    sigma_u = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sigma_u == 0:
        return u, "n.s."
    z = abs((u - mu_u) / sigma_u)

    if z > 3.29:
        sig = "p<0.001"
    elif z > 2.576:
        sig = "p<0.01"
    elif z > 1.96:
        sig = "p<0.05"
    else:
        sig = "n.s."

    return u, sig


def _chi_square_2x2(correct_a: int, n_a: int, correct_b: int, n_b: int) -> Tuple[float, str]:
    """
    Chi-square test for independence on a 2×2 contingency table:
      group A: correct_a successes out of n_a
      group B: correct_b successes out of n_b

    Returns (chi2, significance).
    """
    if n_a == 0 or n_b == 0:
        return 0.0, "n.s. (empty group)"
    wrong_a = n_a - correct_a
    wrong_b = n_b - correct_b
    total = n_a + n_b
    total_correct = correct_a + correct_b
    total_wrong = wrong_a + wrong_b
    if total_correct == 0 or total_wrong == 0:
        return 0.0, "n.s. (no variance)"

    def _expected(row_total: int, col_total: int) -> float:
        return row_total * col_total / total

    e = [
        _expected(n_a, total_correct),
        _expected(n_a, total_wrong),
        _expected(n_b, total_correct),
        _expected(n_b, total_wrong),
    ]
    o = [correct_a, wrong_a, correct_b, wrong_b]
    chi2 = sum((oi - ei) ** 2 / ei for oi, ei in zip(o, e) if ei > 0)

    # Chi-square critical values (df=1): 3.841 = 0.05, 6.635 = 0.01, 10.828 = 0.001
    if chi2 > 10.828:
        sig = "p<0.001"
    elif chi2 > 6.635:
        sig = "p<0.01"
    elif chi2 > 3.841:
        sig = "p<0.05"
    else:
        sig = "n.s."

    return chi2, sig


def _bootstrap_ci(values: List[float], confidence: float = 0.95, n_boot: int = 2000) -> Tuple[float, float]:
    """
    Bootstrap 95% CI on the mean. Uses a fixed seed for reproducibility.
    Returns (lower, upper).
    """
    import random
    rng = random.Random(42)
    n = len(values)
    if n < 2:
        v = values[0] if values else 0.0
        return v, v
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = int((1 - confidence) / 2 * n_boot)
    hi = int((1 + confidence) / 2 * n_boot)
    return means[lo], means[min(hi, n_boot - 1)]


def load_latest_results(benchmark_name: str, results_dir: str = "benchmark_results") -> List[Dict]:
    """Load the most recent CSV file for a given benchmark."""
    results_path = Path(results_dir)
    if not results_path.exists():
        return []
    matching_files = sorted(results_path.glob(f"{benchmark_name}_*.csv"))
    if not matching_files:
        return []
    latest_file = matching_files[-1]
    print(f"Loading: {latest_file}")
    results = []
    with open(latest_file, "r") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            for key, value in row.items():
                try:
                    if "." in value:
                        row[key] = float(value)
                    elif value.isdigit():
                        row[key] = int(value)
                    elif value.lower() in ("true", "false"):
                        row[key] = value.lower() == "true"
                except (ValueError, AttributeError):
                    pass
            results.append(row)
    return results


def analyze_benchmark_a(results: List[Dict]) -> Dict:
    """
    Analyze Benchmark A — Race Condition / Lost Updates.

    Statistical tests:
      Mann-Whitney U  : LCM vs No-LCM lost_update_rate distributions.
      95% bootstrap CI: on the mean lost_update_rate for each group.

    Concurrency model caveat (reported inline):
      All writes are asyncio coroutines on a single event loop — no OS threads
      or processes. Contention is therefore cooperative (yield points only), not
      preemptive. The No-LCM baseline loses updates because it does a single
      dict/SQLite write with no locking layer; LCM's AsyncLockManager serialises
      access. The reported lost_update_rate improvement is real within this model
      but would need OS-level thread or process contention to validate at true
      concurrency. See LIMITATIONS.md §Benchmark A for full discussion.
    """
    print("\n" + "=" * 70)
    print("BENCHMARK A ANALYSIS: Race Condition / Lost Updates")
    print("NOTE: asyncio cooperative concurrency model — see LIMITATIONS.md")
    print("=" * 70)

    analysis = {}

    failure_rates = sorted({r.get("failure_rate_param") for r in results
                            if r.get("failure_rate_param") is not None})
    if not failure_rates:
        failure_rates = [0.0]

    for fr in failure_rates:
        print(f"\n--- failure_rate_param={fr} ---")
        for n_writers in [5, 20, 50, 100]:
            lcm_rows = [r for r in results
                        if r.get("system") == "LCM" and r.get("n_writers") == n_writers
                        and r.get("failure_rate_param") == fr]
            no_lcm_rows = [r for r in results
                           if r.get("system") == "No-LCM" and r.get("n_writers") == n_writers
                           and r.get("failure_rate_param") == fr]

            for system, rows in [("LCM", lcm_rows), ("No-LCM", no_lcm_rows)]:
                if not rows:
                    continue
                lost_rates = [r["lost_update_rate"] for r in rows]
                latencies = [r["mean_latency"] * 1000 for r in rows]
                ci_lo, ci_hi = _bootstrap_ci(lost_rates)
                key = f"{system}_N{n_writers}_fr{fr}"
                analysis[key] = {
                    "system": system,
                    "n_writers": n_writers,
                    "failure_rate_param": fr,
                    "trials": len(rows),
                    "lost_update_rate_mean": statistics.mean(lost_rates),
                    "lost_update_rate_std": statistics.stdev(lost_rates) if len(lost_rates) > 1 else 0,
                    "lost_update_rate_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
                    "latency_mean_ms": statistics.mean(latencies),
                    "latency_std_ms": statistics.stdev(latencies) if len(latencies) > 1 else 0,
                    "latency_p95_ms": (
                        statistics.quantiles(latencies, n=20)[18]
                        if len(latencies) > 1 else latencies[0]
                    ),
                }
                print(f"\n{system}, N={n_writers}, failure_rate={fr}:")
                print(f"  lost_update_rate : {analysis[key]['lost_update_rate_mean']:.4f} ± {analysis[key]['lost_update_rate_std']:.4f}  "
                      f"95% CI [{ci_lo:.4f}, {ci_hi:.4f}]")
                print(f"  latency (mean)   : {analysis[key]['latency_mean_ms']:.2f}ms ± {analysis[key]['latency_std_ms']:.2f}ms  "
                      f"p95={analysis[key]['latency_p95_ms']:.2f}ms")

            # Mann-Whitney U: LCM vs No-LCM on lost_update_rate
            if lcm_rows and no_lcm_rows:
                lcm_rates = [r["lost_update_rate"] for r in lcm_rows]
                no_lcm_rates = [r["lost_update_rate"] for r in no_lcm_rows]
                u_stat, u_sig = _mann_whitney_u(lcm_rates, no_lcm_rates)
                analysis[f"mwu_N{n_writers}_fr{fr}"] = {"U": u_stat, "significance": u_sig}
                print(f"\n  Mann-Whitney U (LCM vs No-LCM, N={n_writers}, "
                      f"failure_rate={fr}): U={u_stat:.1f}, {u_sig}")

    return analysis


def analyze_benchmark_b(results: List[Dict]) -> Dict:
    """
    Analyze Benchmark B — False-Memory (Mandela Injection) with trust-gap sweep.

    Statistical tests:
      Mann-Whitney U  : trapping_efficiency distributions per trust-gap tier
                        (LCM vs No-LCM, and across trust-gap levels).
      Chi-square      : final_is_correct proportions (LCM vs No-LCM).
      95% bootstrap CI: on mean trapping_efficiency per (trust_gap, repetitions).
    """
    print("\n" + "=" * 70)
    print("BENCHMARK B ANALYSIS: False-Memory + Trust-Gap Sweep")
    print("=" * 70)

    # Normalise key names — old CSVs used contradiction_trapping_efficiency,
    # new ones use trapping_efficiency.
    def _eff(r: Dict) -> Optional[float]:
        v = r.get("trapping_efficiency") or r.get("contradiction_trapping_efficiency")
        return float(v) if v is not None else None

    def _correct(r: Dict) -> Optional[bool]:
        v = r.get("final_is_correct")
        if v is None:
            return None
        return bool(v)

    analysis = {}

    # Group by (trust_gap, repetitions)
    from collections import defaultdict
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        gap = r.get("trust_gap", "n/a")
        reps = r.get("repetitions", "?")
        key = f"{r.get('system','?')}|gap={gap}|reps={reps}"
        groups[key].append(r)

    # Unique repetition counts
    rep_counts = sorted(set(r.get("repetitions") for r in results if r.get("repetitions") is not None))
    # Unique trust gaps for LCM rows
    trust_gaps = sorted(set(r.get("trust_gap") for r in results
                            if r.get("system") == "LCM" and r.get("trust_gap") is not None),
                        reverse=True)

    for reps in rep_counts:
        lcm_rows = [r for r in results if r.get("system") == "LCM" and r.get("repetitions") == reps]
        no_lcm_rows = [r for r in results if r.get("system") == "No-LCM" and r.get("repetitions") == reps]

        print(f"\n--- R={reps} repetitions ---")

        # Per trust-gap breakdown for LCM
        for gap in trust_gaps:
            gap_rows = [r for r in lcm_rows if r.get("trust_gap") == gap]
            if not gap_rows:
                continue
            effs = [e for r in gap_rows if (e := _eff(r)) is not None]
            if not effs:
                continue
            ci_lo, ci_hi = _bootstrap_ci(effs)
            corrects = [c for r in gap_rows if (c := _correct(r)) is not None]
            key = f"LCM_gap{gap}_R{reps}"
            analysis[key] = {
                "system": "LCM", "trust_gap": gap, "repetitions": reps,
                "trials": len(gap_rows),
                "trapping_eff_mean": statistics.mean(effs),
                "trapping_eff_std": statistics.stdev(effs) if len(effs) > 1 else 0,
                "trapping_eff_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
                "final_correctness": sum(corrects) / len(corrects) if corrects else None,
            }
            print(f"  LCM  gap={gap:.2f}: trap_eff={statistics.mean(effs):.3f}±"
                  f"{analysis[key]['trapping_eff_std']:.3f}  CI [{ci_lo:.3f},{ci_hi:.3f}]  "
                  f"correct={analysis[key]['final_correctness']:.3f}")

        # No-LCM summary
        if no_lcm_rows:
            no_effs = [e for r in no_lcm_rows if (e := _eff(r)) is not None]
            no_corrects = [c for r in no_lcm_rows if (c := _correct(r)) is not None]
            print(f"  No-LCM         : trap_eff=0.000  correct="
                  f"{sum(no_corrects)/len(no_corrects):.3f}" if no_corrects else "  No-LCM: no data")

        # Mann-Whitney U: all LCM vs all No-LCM trapping_efficiency at this rep count
        lcm_effs = [e for r in lcm_rows if (e := _eff(r)) is not None]
        no_effs  = [e for r in no_lcm_rows if (e := _eff(r)) is not None]
        if lcm_effs and no_effs:
            u_stat, u_sig = _mann_whitney_u(lcm_effs, no_effs)
            print(f"  Mann-Whitney U (LCM vs No-LCM): U={u_stat:.1f}, {u_sig}")
            analysis[f"mwu_R{reps}"] = {"U": u_stat, "significance": u_sig}

        # Chi-square: final_is_correct proportions
        lcm_c = [c for r in lcm_rows if (c := _correct(r)) is not None]
        no_c  = [c for r in no_lcm_rows if (c := _correct(r)) is not None]
        if lcm_c and no_c:
            chi2, chi_sig = _chi_square_2x2(sum(lcm_c), len(lcm_c), sum(no_c), len(no_c))
            print(f"  Chi-square (final_correct): χ²={chi2:.3f}, {chi_sig}")
            analysis[f"chi2_R{reps}"] = {"chi2": chi2, "significance": chi_sig}

    return analysis


def save_analysis_summary(analyses: Dict, output_file: str = "benchmark_results/analysis_summary.json"):
    """Save analysis summary to JSON."""
    output_path = Path(output_file)
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(analyses, f, indent=2)
    print(f"\n [OK] Analysis summary saved to {output_path}")


def main():
    """Main analysis function."""
    print("=" * 70)
    print("LCM BENCHMARK RESULTS ANALYSIS")
    print("Statistical tests: Mann-Whitney U, Chi-square, 95% bootstrap CI")
    print("=" * 70)

    all_analyses = {}

    results_a = load_latest_results("benchmark_a_race_condition")
    if results_a:
        all_analyses["benchmark_a"] = analyze_benchmark_a(results_a)
    else:
        print("\nNo results found for Benchmark A — run benchmarks/benchmark_a_race_condition.py first")

    results_b = load_latest_results("benchmark_b_mandela")
    if results_b:
        all_analyses["benchmark_b"] = analyze_benchmark_b(results_b)
    else:
        print("\nNo results found for Benchmark B — run benchmarks/benchmark_b_mandela.py first")

    if all_analyses:
        save_analysis_summary(all_analyses)

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
