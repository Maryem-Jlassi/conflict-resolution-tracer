"""
LCM-specific plotting functions for benchmark analysis and visualization.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
import json


def plot_conflict_resolution_results(results: List[Dict], output_path: str):
    """Plot conflict resolution results with confidence intervals."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Extract conflict types
    conflict_types = {}
    for r in results:
        c_type = r.get("conflict_type", "unknown")
        if c_type not in conflict_types:
            conflict_types[c_type] = []
        conflict_types[c_type].append(r.get("resolution_time", 0))
    
    # Plot resolution times
    ax1.bar(range(len(conflict_types)), 
            [np.mean(times) for times in conflict_types.values()],
            yerr=[np.std(times) if len(times) > 1 else 0 for times in conflict_types.values()],
            tick_label=list(conflict_types.keys()))
    ax1.set_xlabel('Conflict Type')
    ax1.set_ylabel('Resolution Time (ms)')
    ax1.set_title('Conflict Resolution Time by Type')
    ax1.tick_params(axis='x', rotation=45)
    ax1.grid(True, axis='y')
    
    # Plot resolution outcomes
    outcomes = {}
    for r in results:
        outcome = r.get("resolution_outcome", "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    
    ax2.pie(outcomes.values(), labels=outcomes.keys(), autopct='%1.1f%%')
    ax2.set_title('Conflict Resolution Outcomes')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_evidence_distribution(results: List[Dict], output_path: str):
    """Plot evidence type distribution across all writes."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    evidence_types = {}
    for r in results:
        evidence = r.get("evidence_type", "unknown")
        evidence_types[evidence] = evidence_types.get(evidence, 0) + 1
    
    ax.bar(evidence_types.keys(), evidence_types.values(), color='skyblue')
    ax.set_xlabel('Evidence Type')
    ax.set_ylabel('Count')
    ax.set_title('Evidence Type Distribution')
    ax.tick_params(axis='x', rotation=45)
    ax.grid(True, axis='y')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_trust_evolution(agent_trust_data: Dict[str, List[Dict]], output_path: str):
    """Plot trust score evolution for multiple agents."""
    fig, ax = plt.subplots(figsize=(12, 6))
    
    for agent_id, trust_history in agent_trust_data.items():
        timestamps = [t.get("timestamp", 0) for t in trust_history]
        trust_scores = [t.get("trust_score", 0.5) for t in trust_history]
        ax.plot(timestamps, trust_scores, 'o-', label=agent_id)
    
    ax.axhline(y=0.5, color='red', linestyle='--', label='Neutral Trust')
    ax.set_xlabel('Time')
    ax.set_ylabel('Trust Score')
    ax.set_title('Agent Trust Score Evolution')
    ax.legend()
    ax.grid(True)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_performance_comparison(comparison_data: Dict, output_path: str):
    """Plot performance comparison between LCM and baseline systems."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    metrics = ['latency', 'throughput', 'correctness', 'conflict_rate']
    titles = ['Latency Comparison', 'Throughput Comparison', 
              'Correctness Comparison', 'Conflict Rate Comparison']
    
    for i, (metric, title) in enumerate(zip(metrics, titles)):
        ax = axes[i // 2, i % 2]
        
        systems = list(comparison_data.keys())
        values = [comparison_data[s].get(metric, 0) for s in systems]
        
        ax.bar(systems, values, color=['blue', 'orange'])
        ax.set_ylabel(metric.replace('_', ' ').title())
        ax.set_title(title)
        ax.grid(True, axis='y')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_attack_success_rate(attack_results: List[Dict], output_path: str):
    """Plot attack success rates across different attack types."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    attack_types = {}
    for r in attack_results:
        a_type = r.get("attack_type", "unknown")
        success = r.get("attack_successful", False)
        if a_type not in attack_types:
            attack_types[a_type] = {"success": 0, "total": 0}
        attack_types[a_type]["total"] += 1
        if success:
            attack_types[a_type]["success"] += 1
    
    types = list(attack_types.keys())
    success_rates = [attack_types[t]["success"] / attack_types[t]["total"] 
                    for t in types]
    
    ax.bar(types, success_rates, color='red', alpha=0.7)
    ax.set_xlabel('Attack Type')
    ax.set_ylabel('Success Rate')
    ax.set_title('Attack Success Rate by Type')
    ax.set_ylim(0, 1)
    ax.tick_params(axis='x', rotation=45)
    ax.grid(True, axis='y')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def plot_multi_agent_coordination(coordination_data: List[Dict], output_path: str):
    """Plot multi-agent coordination effectiveness."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    
    # Extract coordination metrics
    agent_counts = [d.get("num_agents", 0) for d in coordination_data]
    coordination_scores = [d.get("coordination_score", 0) for d in coordination_data]
    conflict_counts = [d.get("conflict_count", 0) for d in coordination_data]
    
    ax1.scatter(agent_counts, coordination_scores, s=100, alpha=0.6)
    ax1.set_xlabel('Number of Agents')
    ax1.set_ylabel('Coordination Score')
    ax1.set_title('Coordination Score vs Agent Count')
    ax1.grid(True)
    
    ax2.scatter(agent_counts, conflict_counts, s=100, alpha=0.6, color='red')
    ax2.set_xlabel('Number of Agents')
    ax2.set_ylabel('Conflict Count')
    ax2.set_title('Conflict Count vs Agent Count')
    ax2.grid(True)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def generate_summary_dashboard(results: Dict, output_path: str):
    """Generate a comprehensive summary dashboard."""
    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)
    
    # Top row: Key metrics
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.text(0.5, 0.5, f"Total Writes: {results.get('total_writes', 0)}", 
             ha='center', va='center', fontsize=20, weight='bold')
    ax1.axis('off')
    
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.text(0.5, 0.5, f"Conflicts: {results.get('total_conflicts', 0)}", 
             ha='center', va='center', fontsize=20, weight='bold')
    ax2.axis('off')
    
    ax3 = fig.add_subplot(gs[0, 2])
    ax3.text(0.5, 0.5, f"Correctness: {results.get('correctness_rate', 0):.2%}", 
             ha='center', va='center', fontsize=20, weight='bold')
    ax3.axis('off')
    
    # Middle row: Charts
    ax4 = fig.add_subplot(gs[1, :2])
    if 'latency_distribution' in results:
        ax4.hist(results['latency_distribution'], bins=20, color='blue', alpha=0.7)
        ax4.set_xlabel('Latency (ms)')
        ax4.set_ylabel('Frequency')
        ax4.set_title('Latency Distribution')
        ax4.grid(True)
    
    ax5 = fig.add_subplot(gs[1, 2])
    if 'evidence_types' in results:
        evidence = results['evidence_types']
        ax5.pie(evidence.values(), labels=evidence.keys(), autopct='%1.1f%%')
        ax5.set_title('Evidence Type Distribution')
    
    # Bottom row: Trust evolution
    ax6 = fig.add_subplot(gs[2, :])
    if 'trust_evolution' in results:
        trust_data = results['trust_evolution']
        for agent, scores in trust_data.items():
            ax6.plot(range(len(scores)), scores, 'o-', label=agent)
        ax6.axhline(y=0.5, color='red', linestyle='--', label='Neutral')
        ax6.set_xlabel('Time Step')
        ax6.set_ylabel('Trust Score')
        ax6.set_title('Trust Score Evolution')
        ax6.legend()
        ax6.grid(True)
    
    fig.suptitle('LCM Benchmark Summary Dashboard', fontsize=16, weight='bold')
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
