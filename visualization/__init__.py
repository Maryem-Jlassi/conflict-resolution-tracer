"""
Visualization module for LCM benchmark results.

Provides plotting functions for benchmark analysis and comparison.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
import json

# Import LCM-specific plotting functions
from visualization.lcm_plots import (
    plot_conflict_resolution_results,
    plot_evidence_distribution,
    plot_trust_evolution,
    plot_performance_comparison,
    plot_attack_success_rate,
    plot_multi_agent_coordination,
    generate_summary_dashboard,
)

# Import multi-agent experiment plotting functions
from visualization.multi_agent_plots import (
    plot_multi_agent_write_timeline,
    plot_trust_score_comparison,
    plot_conflict_resolution_outcomes,
    plot_write_status_distribution,
    plot_final_memory_state,
    generate_multi_agent_dashboard,
    load_multi_agent_results,
)


def style_matplotlib():
    """Apply consistent styling to matplotlib plots."""
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.rcParams['figure.figsize'] = (10, 6)
    plt.rcParams['font.size'] = 10
    plt.rcParams['axes.labelsize'] = 12
    plt.rcParams['axes.titlesize'] = 14
    plt.rcParams['xtick.labelsize'] = 10
    plt.rcParams['ytick.labelsize'] = 10
    plt.rcParams['legend.fontsize'] = 10


def plot_latency_curves(results: List[Dict], output_dir: str = "benchmark_results/plots"):
    """Generate latency curves for race condition benchmark."""
    style_matplotlib()
    
    # Extract LCM and No-LCM results
    lcm_results = [r for r in results if r.get("system") == "LCM"]
    no_lcm_results = [r for r in results if r.get("system") == "No-LCM"]
    
    if not lcm_results or not no_lcm_results:
        print("  [SKIP] Insufficient data for latency curves")
        return None
    
    # Group by number of writers
    n_writers = sorted(set(r.get("n_writers") for r in results))
    
    fig, ax = plt.subplots()
    
    for n in n_writers:
        lcm_n = [r.get("mean_latency", 0) * 1000 for r in lcm_results if r.get("n_writers") == n]
        no_lcm_n = [r.get("mean_latency", 0) * 1000 for r in no_lcm_results if r.get("n_writers") == n]
        
        if lcm_n:
            ax.plot(range(len(lcm_n)), lcm_n, 'o-', label=f'LCM N={n}')
        if no_lcm_n:
            ax.plot(range(len(no_lcm_n)), no_lcm_n, 's--', label=f'No-LCM N={n}')
    
    ax.set_xlabel('Trial')
    ax.set_ylabel('Latency (ms)')
    ax.set_title('Race Condition Benchmark: Latency Over Trials')
    ax.legend()
    ax.grid(True)
    
    output_path = Path(output_dir) / "latency_curves.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_race_condition_sensitivity(results: List[Dict], output_dir: str = "benchmark_results/plots"):
    """Generate sensitivity analysis for race condition benchmark."""
    style_matplotlib()
    
    # Extract LCM results
    lcm_results = [r for r in results if r.get("system") == "LCM"]
    
    if not lcm_results:
        print("  [SKIP] No LCM data for sensitivity analysis")
        return None
    
    # Group by number of writers
    n_writers = sorted(set(r.get("n_writers") for r in lcm_results))
    
    lost_rates = []
    latencies = []
    
    for n in n_writers:
        n_results = [r for r in lcm_results if r.get("n_writers") == n]
        if n_results:
            avg_lost = np.mean([r.get("lost_update_rate", 0) for r in n_results])
            avg_latency = np.mean([r.get("mean_latency", 0) * 1000 for r in n_results])
            lost_rates.append(avg_lost)
            latencies.append(avg_latency)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    ax1.plot(n_writers, lost_rates, 'o-', color='red')
    ax1.set_xlabel('Number of Writers')
    ax1.set_ylabel('Lost Update Rate')
    ax1.set_title('Lost Update Rate vs Concurrent Writers')
    ax1.grid(True)
    
    ax2.plot(n_writers, latencies, 's-', color='blue')
    ax2.set_xlabel('Number of Writers')
    ax2.set_ylabel('Mean Latency (ms)')
    ax2.set_title('Latency vs Concurrent Writers')
    ax2.grid(True)
    
    output_path = Path(output_dir) / "race_condition_sensitivity.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_mandela_attack_sensitivity(results: List[Dict], output_dir: str = "benchmark_results/plots"):
    """Generate sensitivity analysis for Mandela attack benchmark."""
    style_matplotlib()
    
    # Extract LCM results
    lcm_results = [r for r in results if r.get("system") == "LCM"]
    
    if not lcm_results:
        print("  [SKIP] No LCM data for Mandela sensitivity analysis")
        return None
    
    # Group by repetitions
    repetitions = sorted(set(r.get("repetitions") for r in lcm_results))
    
    trap_eff = []
    correctness = []
    
    for rep in repetitions:
        r_results = [r for r in lcm_results if r.get("repetitions") == rep]
        if r_results:
            avg_trap = np.mean([r.get("contradiction_trapping_efficiency", 0) for r in r_results])
            avg_correct = np.mean([r.get("final_is_correct", 0) for r in r_results])
            trap_eff.append(avg_trap)
            correctness.append(avg_correct)
    
    # Check if we have data to plot
    if not trap_eff or not correctness:
        print("  [SKIP] No valid data points for Mandela sensitivity analysis")
        return None
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    ax1.plot(repetitions, trap_eff, 'o-', color='green')
    ax1.set_xlabel('Number of Repetitions')
    ax1.set_ylabel('Contradiction Trapping Efficiency')
    ax1.set_title('Trapping Efficiency vs Attack Repetitions')
    ax1.grid(True)
    
    ax2.plot(repetitions, correctness, 's-', color='purple')
    ax2.set_xlabel('Number of Repetitions')
    ax2.set_ylabel('Final Correctness Rate')
    ax2.set_title('Final Correctness vs Attack Repetitions')
    ax2.grid(True)
    
    output_path = Path(output_dir) / "mandela_attack_sensitivity.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_benchmark_comparison_bars(results: Dict, output_dir: str = "benchmark_results/plots"):
    """Generate comparison bar charts for benchmark results."""
    style_matplotlib()
    
    # Extract key metrics
    systems = ['LCM', 'No-LCM']
    metrics = ['lost_update_rate', 'contradiction_trapping_efficiency', 'final_correctness']
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    for i, metric in enumerate(metrics):
        values = []
        for system in systems:
            system_results = [r for r in results if r.get("system") == system]
            if system_results:
                avg_val = np.mean([r.get(metric, 0) for r in system_results])
                values.append(avg_val)
            else:
                values.append(0)
        
        axes[i].bar(systems, values, color=['blue', 'orange'])
        axes[i].set_ylabel(metric.replace('_', ' ').title())
        axes[i].set_title(f'{metric.replace("_", " ").title()} Comparison')
        axes[i].grid(True, axis='y')
    
    output_path = Path(output_dir) / "benchmark_comparison_bars.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_ablation_bars(results: Dict, output_dir: str = "benchmark_results/plots"):
    """Generate ablation study bar charts."""
    style_matplotlib()
    
    # Extract ablation components
    components = ['Full System', 'No Trust', 'No Conflict', 'No Locking']
    metrics = ['correctness', 'latency', 'conflict_rate']
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    for i, metric in enumerate(metrics):
        values = []
        for component in components:
            comp_results = [r for r in results if r.get("component") == component]
            if comp_results:
                avg_val = np.mean([r.get(metric, 0) for r in comp_results])
                values.append(avg_val)
            else:
                values.append(0)
        
        axes[i].bar(components, values, color=['green', 'red', 'blue', 'orange'])
        axes[i].set_ylabel(metric.replace('_', ' ').title())
        axes[i].set_title(f'{metric.replace("_", " ").title()} by Component')
        axes[i].grid(True, axis='y')
        axes[i].tick_params(axis='x', rotation=45)
    
    output_path = Path(output_dir) / "ablation_bars.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def generate_all_figures(output_dir: str = "benchmark_results/plots") -> Dict[str, str]:
    """Generate all available figures from benchmark results."""
    figures = {}
    
    # Try to load results and generate plots
    try:
        from benchmarks.analyze_results import load_latest_results
        
        # Benchmark A plots
        results_a = load_latest_results("benchmark_a_race_condition")
        if results_a:
            fig = plot_latency_curves(results_a, output_dir)
            if fig:
                figures["latency_curves"] = fig
            
            fig = plot_race_condition_sensitivity(results_a, output_dir)
            if fig:
                figures["race_condition_sensitivity"] = fig
        
        # Benchmark B plots
        results_b = load_latest_results("benchmark_b_mandela")
        if results_b:
            fig = plot_mandela_attack_sensitivity(results_b, output_dir)
            if fig:
                figures["mandela_attack_sensitivity"] = fig
    
    except ImportError:
        print("  [SKIP] analyze_results module not available")
    
    return figures


# Additional inspector-specific plotting functions
def plot_conflict_timeline(data: List[Dict], output_path: str):
    """Plot conflict resolution timeline."""
    style_matplotlib()
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    timestamps = [r.get("timestamp", 0) for r in data]
    conflicts = [r.get("conflict_count", 0) for r in data]
    
    ax.plot(timestamps, conflicts, 'o-', color='red')
    ax.set_xlabel('Time')
    ax.set_ylabel('Conflict Count')
    ax.set_title('Conflict Resolution Timeline')
    ax.grid(True)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_memory_evolution(data: List[Dict], output_path: str):
    """Plot memory state evolution over time."""
    style_matplotlib()
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    timestamps = [r.get("timestamp", 0) for r in data]
    memory_size = [r.get("memory_size", 0) for r in data]
    
    ax.plot(timestamps, memory_size, 's-', color='blue')
    ax.set_xlabel('Time')
    ax.set_ylabel('Memory Size')
    ax.set_title('Memory State Evolution')
    ax.grid(True)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_psi_breakdown_radar(data: Dict, output_path: str):
    """Plot Ψ formula breakdown as radar chart."""
    style_matplotlib()
    
    categories = list(data.keys())
    values = list(data.values())
    
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(projection='polar'))
    
    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]
    
    ax.plot(angles, values, 'o-', linewidth=2)
    ax.fill(angles, values, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories)
    ax.set_title('Ψ Formula Component Breakdown')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_trust_convergence(data: List[Dict], output_path: str):
    """Plot trust score convergence over time."""
    style_matplotlib()
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    timestamps = [r.get("timestamp", 0) for r in data]
    trust_scores = [r.get("trust_score", 0.5) for r in data]
    
    ax.plot(timestamps, trust_scores, 'o-', color='green')
    ax.axhline(y=0.5, color='red', linestyle='--', label='Neutral Trust')
    ax.set_xlabel('Time')
    ax.set_ylabel('Trust Score')
    ax.set_title('Trust Score Convergence')
    ax.legend()
    ax.grid(True)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_scenario_correctness_heatmap(data: Dict, output_path: str):
    """Plot scenario correctness as heatmap."""
    style_matplotlib()
    
    scenarios = list(data.keys())
    metrics = list(data.values())[0].keys() if data else []
    
    fig, ax = plt.subplots(figsize=(10, 8))
    
    matrix = np.array([[data[s].get(m, 0) for m in metrics] for s in scenarios])
    
    im = ax.imshow(matrix, cmap='RdYlGn', aspect='auto')
    ax.set_xticks(range(len(metrics)))
    ax.set_yticks(range(len(scenarios)))
    ax.set_xticklabels(metrics, rotation=45)
    ax.set_yticklabels(scenarios)
    ax.set_title('Scenario Correctness Heatmap')
    
    plt.colorbar(im, ax=ax)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
