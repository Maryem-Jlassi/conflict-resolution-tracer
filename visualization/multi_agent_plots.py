"""
Visualization functions for multi-agent experiment results.

These functions generate plots specifically for real agent experiments,
showing conflict resolution patterns, trust evolution, and agent behavior.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from typing import Dict, List, Any
import json


def style_matplotlib():
    """Apply consistent styling to matplotlib plots."""
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.rcParams['figure.figsize'] = (12, 6)
    plt.rcParams['font.size'] = 10
    plt.rcParams['axes.labelsize'] = 12
    plt.rcParams['axes.titlesize'] = 14
    plt.rcParams['xtick.labelsize'] = 10
    plt.rcParams['ytick.labelsize'] = 10
    plt.rcParams['legend.fontsize'] = 10


def plot_multi_agent_write_timeline(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Plot timeline of write attempts by different agents."""
    style_matplotlib()
    
    writes = results.get("writes", [])
    if not writes:
        print("  [SKIP] No write data for timeline plot")
        return None
    
    # Extract agent info
    agents = [w["agent"] for w in writes]
    statuses = [w["status"] for w in writes]
    
    # Create timeline plot
    fig, ax = plt.subplots(figsize=(14, 6))
    
    # Assign colors to agents
    unique_agents = list(set(agents))
    colors = plt.cm.tab10(np.linspace(0, 1, len(unique_agents)))
    agent_colors = {agent: colors[i] for i, agent in enumerate(unique_agents)}
    
    # Plot each write as a point
    for i, (agent, status) in enumerate(zip(agents, statuses)):
        color = agent_colors[agent]
        marker = 'o' if status == "committed" else ('x' if "rejected" in status else 's')
        ax.scatter(i, 1 if status == "committed" else (0 if "rejected" in status else 0.5), 
                   c=[color], marker=marker, s=100, label=agent if i == 0 or agent != agents[i-1] else "")
    
    # Customize plot
    ax.set_xlabel('Write Attempt Number')
    ax.set_ylabel('Status')
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['Rejected', 'Conflict', 'Committed'])
    ax.set_title('Multi-Agent Write Timeline')
    
    # Create legend
    handles, labels = plt.gca().get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    if by_label:
        ax.legend(by_label.values(), by_label.keys(), loc='upper right')
    
    ax.grid(True, alpha=0.3)
    
    output_path = Path(output_dir) / "multi_agent_write_timeline.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_trust_score_comparison(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Plot trust scores for different agents."""
    style_matplotlib()
    
    trust_scores = results.get("trust_scores", {})
    if not trust_scores:
        print("  [SKIP] No trust score data for comparison plot")
        return None
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    agents = list(trust_scores.keys())
    # Handle both float values and dict returns from get_trust_with_meta
    scores = []
    for score in trust_scores.values():
        if isinstance(score, dict):
            scores.append(score.get("trust", 0.5))
        else:
            scores.append(float(score) if score is not None else 0.5)
    
    # Color code by trust level
    colors = ['green' if score >= 0.7 else 'orange' if score >= 0.3 else 'red' for score in scores]
    
    bars = ax.bar(agents, scores, color=colors, alpha=0.7)
    
    # Add value labels on bars
    for bar, score in zip(bars, scores):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{score:.2f}',
                ha='center', va='bottom')
    
    # Add threshold lines
    ax.axhline(y=0.1, color='red', linestyle='--', alpha=0.5, label='Reject Threshold (0.1)')
    ax.axhline(y=0.3, color='orange', linestyle='--', alpha=0.5, label='Low Trust Threshold (0.3)')
    ax.axhline(y=0.7, color='green', linestyle='--', alpha=0.5, label='High Trust (0.7)')
    
    ax.set_xlabel('Agent ID')
    ax.set_ylabel('Trust Score')
    ax.set_title('Agent Trust Score Comparison')
    ax.set_ylim(0, 1.1)
    ax.legend(loc='lower right')
    ax.grid(True, alpha=0.3, axis='y')
    
    output_path = Path(output_dir) / "trust_score_comparison.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_conflict_resolution_outcomes(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Plot conflict resolution outcomes."""
    style_matplotlib()
    
    conflicts = results.get("conflicts", [])
    if not conflicts:
        print("  [SKIP] No conflict data for resolution outcomes plot")
        return None
    
    # Extract winner/loser data
    winners = [c["winner"] for c in conflicts]
    losers = [c["loser"] for c in conflicts]
    
    # Count wins per agent
    from collections import Counter
    win_counts = Counter(winners)
    loss_counts = Counter(losers)
    
    # Get all unique agents
    all_agents = set(win_counts.keys()) | set(loss_counts.keys())
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    x = np.arange(len(all_agents))
    width = 0.35
    
    wins = [win_counts.get(agent, 0) for agent in all_agents]
    losses = [loss_counts.get(agent, 0) for agent in all_agents]
    
    bars1 = ax.bar(x - width/2, wins, width, label='Wins', color='green', alpha=0.7)
    bars2 = ax.bar(x + width/2, losses, width, label='Losses', color='red', alpha=0.7)
    
    ax.set_xlabel('Agent ID')
    ax.set_ylabel('Count')
    ax.set_title('Conflict Resolution Outcomes by Agent')
    ax.set_xticks(x)
    ax.set_xticklabels(all_agents, rotation=45, ha='right')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    
    # Add value labels on bars
    for bars in [bars1, bars2]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height,
                        f'{int(height)}',
                        ha='center', va='bottom')
    
    output_path = Path(output_dir) / "conflict_resolution_outcomes.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_write_status_distribution(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Plot distribution of write statuses."""
    style_matplotlib()
    
    writes = results.get("writes", [])
    if not writes:
        print("  [SKIP] No write data for status distribution plot")
        return None
    
    # Count statuses
    from collections import Counter
    status_counts = Counter([w["status"] for w in writes])
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    statuses = list(status_counts.keys())
    counts = list(status_counts.values())
    
    # Color coding
    colors = ['green' if 'committed' in s else 'red' if 'rejected' in s else 'orange' for s in statuses]
    
    bars = ax.bar(statuses, counts, color=colors, alpha=0.7)
    
    # Add value labels on bars
    for bar, count in zip(bars, counts):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{count}',
                ha='center', va='bottom')
    
    ax.set_xlabel('Write Status')
    ax.set_ylabel('Count')
    ax.set_title('Write Status Distribution')
    ax.set_xticks(range(len(statuses)))
    ax.set_xticklabels(statuses, rotation=45, ha='right')
    ax.grid(True, alpha=0.3, axis='y')
    
    output_path = Path(output_dir) / "write_status_distribution.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def plot_final_memory_state(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Plot final memory state showing which agent owns each path."""
    style_matplotlib()
    
    final_state = results.get("final_state", {})
    if not final_state:
        print("  [SKIP] No final memory state data")
        return None
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    paths = list(final_state.keys())
    agents = [final_state[path]["agent_id"] for path in paths]
    
    # Color code by agent
    unique_agents = list(set(agents))
    colors = plt.cm.tab10(np.linspace(0, 1, len(unique_agents)))
    agent_colors = {agent: colors[i] for i, agent in enumerate(unique_agents)}
    
    bar_colors = [agent_colors[agent] for agent in agents]
    
    bars = ax.barh(paths, [1] * len(paths), color=bar_colors, alpha=0.7)
    
    # Add agent labels on bars
    for bar, agent in zip(bars, agents):
        width = bar.get_width()
        ax.text(width/2, bar.get_y() + bar.get_height()/2.,
                agent,
                ha='center', va='center', color='white', fontweight='bold')
    
    ax.set_xlabel('Memory Path')
    ax.set_title('Final Memory State by Agent')
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.grid(True, alpha=0.3, axis='x')
    
    # Create legend
    from matplotlib.patches import Patch
    legend_elements = [Patch(facecolor=agent_colors[agent], edgecolor='black', label=agent) 
                      for agent in unique_agents]
    ax.legend(handles=legend_elements, loc='lower right')
    
    output_path = Path(output_dir) / "final_memory_state.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(output_path)


def generate_multi_agent_dashboard(results: Dict[str, Any], output_dir: str = "experiments/results/plots"):
    """Generate comprehensive dashboard for multi-agent experiment results."""
    style_matplotlib()
    
    plots = {}
    
    print("Generating multi-agent experiment visualizations...")
    
    # Generate individual plots
    plot = plot_multi_agent_write_timeline(results, output_dir)
    if plot:
        plots["write_timeline"] = plot
        print(f"  [OK] Write timeline: {plot}")
    
    plot = plot_trust_score_comparison(results, output_dir)
    if plot:
        plots["trust_comparison"] = plot
        print(f"  [OK] Trust comparison: {plot}")
    
    plot = plot_conflict_resolution_outcomes(results, output_dir)
    if plot:
        plots["conflict_outcomes"] = plot
        print(f"  [OK] Conflict outcomes: {plot}")
    
    plot = plot_write_status_distribution(results, output_dir)
    if plot:
        plots["status_distribution"] = plot
        print(f"  [OK] Status distribution: {plot}")
    
    plot = plot_final_memory_state(results, output_dir)
    if plot:
        plots["memory_state"] = plot
        print(f"  [OK] Memory state: {plot}")
    
    # Generate summary dashboard
    if len(plots) >= 2:
        generate_summary_dashboard(results, plots, output_dir)
    
    return plots


def generate_summary_dashboard(results: Dict[str, Any], plots: Dict[str, str], output_dir: str):
    """Generate a summary dashboard with key metrics and plots."""
    style_matplotlib()
    
    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)
    
    # Summary statistics
    writes = results.get("writes", [])
    conflicts = results.get("conflicts", [])
    trust_scores = results.get("trust_scores", {})
    
    total_writes = len(writes)
    committed = sum(1 for w in writes if w["status"] == "committed")
    rejected = sum(1 for w in writes if "rejected" in w["status"])
    conflicts_resolved = len(conflicts)
    
    # Text summary
    ax_text = fig.add_subplot(gs[0, 0])
    ax_text.axis('off')
    
    # Handle both float values and dict returns from get_trust_with_meta
    numeric_scores = []
    for score in trust_scores.values():
        if isinstance(score, dict):
            numeric_scores.append(score.get("trust", 0.5))
        else:
            numeric_scores.append(float(score) if score is not None else 0.5)
    
    trust_min = min(numeric_scores) if numeric_scores else 0.0
    trust_max = max(numeric_scores) if numeric_scores else 1.0
    
    summary_text = f"""
Multi-Agent Experiment Summary

Total Writes: {total_writes}
Committed: {committed}
Rejected: {rejected}
Conflicts Resolved: {conflicts_resolved}

Agents: {len(trust_scores)}
Trust Range: {trust_min:.2f} - {trust_max:.2f}
"""
    ax_text.text(0.1, 0.5, summary_text, fontsize=12, verticalalignment='center',
                 bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # Trust scores
    ax_trust = fig.add_subplot(gs[0, 1])
    agents = list(trust_scores.keys())
    # Handle both float values and dict returns from get_trust_with_meta
    scores = []
    for score in trust_scores.values():
        if isinstance(score, dict):
            scores.append(score.get("trust", 0.5))
        else:
            scores.append(float(score) if score is not None else 0.5)
    colors = ['green' if score >= 0.7 else 'orange' if score >= 0.3 else 'red' for score in scores]
    ax_trust.bar(agents, scores, color=colors, alpha=0.7)
    ax_trust.set_title('Trust Scores')
    ax_trust.set_ylim(0, 1.1)
    ax_trust.tick_params(axis='x', rotation=45)
    
    # Write status distribution
    ax_status = fig.add_subplot(gs[0, 2])
    from collections import Counter
    status_counts = Counter([w["status"] for w in writes])
    status_colors = ['green' if 'committed' in s else 'red' if 'rejected' in s else 'orange' for s in status_counts.keys()]
    ax_status.bar(status_counts.keys(), status_counts.values(), color=status_colors, alpha=0.7)
    ax_status.set_title('Write Status Distribution')
    ax_status.tick_params(axis='x', rotation=45)
    
    # Write timeline (spans 2 columns)
    ax_timeline = fig.add_subplot(gs[1, :])
    agents_timeline = [w["agent"] for w in writes]
    statuses_timeline = [w["status"] for w in writes]
    unique_agents = list(set(agents_timeline))
    agent_colors = {agent: plt.cm.tab10(i) for i, agent in enumerate(unique_agents)}
    
    for i, (agent, status) in enumerate(zip(agents_timeline, statuses_timeline)):
        color = agent_colors[agent]
        marker = 'o' if status == "committed" else ('x' if "rejected" in status else 's')
        y_pos = 1 if status == "committed" else (0 if "rejected" in status else 0.5)
        ax_timeline.scatter(i, y_pos, c=[color], marker=marker, s=80)
    
    ax_timeline.set_title('Write Timeline')
    ax_timeline.set_xlabel('Write Attempt')
    ax_timeline.set_ylabel('Status')
    ax_timeline.set_yticks([0, 0.5, 1])
    ax_timeline.set_yticklabels(['Rejected', 'Conflict', 'Committed'])
    
    # Conflict outcomes
    if conflicts:
        ax_conflict = fig.add_subplot(gs[2, 0])
        winners = [c["winner"] for c in conflicts]
        losers = [c["loser"] for c in conflicts]
        win_counts = Counter(winners)
        loss_counts = Counter(losers)
        all_agents = set(win_counts.keys()) | set(loss_counts.keys())
        
        x = np.arange(len(all_agents))
        width = 0.35
        wins = [win_counts.get(agent, 0) for agent in all_agents]
        losses = [loss_counts.get(agent, 0) for agent in all_agents]
        
        ax_conflict.bar(x - width/2, wins, width, label='Wins', color='green', alpha=0.7)
        ax_conflict.bar(x + width/2, losses, width, label='Losses', color='red', alpha=0.7)
        ax_conflict.set_title('Conflict Outcomes')
        ax_conflict.set_xticks(x)
        ax_conflict.set_xticklabels(all_agents, rotation=45, ha='right')
        ax_conflict.legend()
    
    # Final memory state
    final_state = results.get("final_state", {})
    if final_state:
        ax_memory = fig.add_subplot(gs[2, 1])
        paths = list(final_state.keys())
        agents_memory = [final_state[path]["agent_id"] for path in paths]
        unique_agents_memory = list(set(agents_memory))
        agent_colors_memory = {agent: plt.cm.tab10(i) for i, agent in enumerate(unique_agents_memory)}
        bar_colors = [agent_colors_memory[agent] for agent in agents_memory]
        
        ax_memory.barh(paths, [1] * len(paths), color=bar_colors, alpha=0.7)
        ax_memory.set_title('Final Memory State')
        ax_memory.set_xlim(0, 1)
        ax_memory.set_xticks([])
        
        for i, (bar, agent) in enumerate(zip(ax_memory.patches, agents_memory)):
            ax_memory.text(0.5, bar.get_y() + bar.get_height()/2., agent,
                          ha='center', va='center', color='white', fontweight='bold')
    
    # Security verification
    ax_security = fig.add_subplot(gs[2, 2])
    ax_security.axis('off')
    
    adversarial_rejected = any(w["agent"] == "adversarial_agent" and "rejected" in w["status"] for w in writes)
    trusted_succeeds = any(w["agent"] == "trusted_researcher" and w["status"] == "committed" for w in writes)
    
    security_text = f"""
Security Verification

Adversarial Rejected: {'PASS' if adversarial_rejected else 'FAIL'}
Trusted Agent Success: {'PASS' if trusted_succeeds else 'FAIL'}
Conflicts Resolved: {'PASS' if conflicts_resolved > 0 else 'N/A'}
"""
    security_color = 'lightgreen' if (adversarial_rejected and trusted_succeeds) else 'lightcoral'
    ax_security.text(0.1, 0.5, security_text, fontsize=11, verticalalignment='center',
                    bbox=dict(boxstyle='round', facecolor=security_color, alpha=0.5))
    
    plt.suptitle('Multi-Agent Experiment Dashboard', fontsize=16, fontweight='bold')
    
    output_path = Path(output_dir) / "multi_agent_dashboard.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"  [OK] Summary dashboard: {output_path}")
    return str(output_path)


def load_multi_agent_results(results_path: str) -> Dict[str, Any]:
    """Load multi-agent experiment results from JSON file."""
    try:
        with open(results_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"  [ERROR] Failed to load results from {results_path}: {e}")
        return {}


if __name__ == "__main__":
    # Example usage
    import sys
    results_path = sys.argv[1] if len(sys.argv) > 1 else "experiments/results/minimal_multi_agent_test.json"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "experiments/results/plots"
    
    results = load_multi_agent_results(results_path)
    if results:
        plots = generate_multi_agent_dashboard(results, output_dir)
        print(f"\nGenerated {len(plots)} visualizations in {output_dir}")
    else:
        print("No results to visualize")