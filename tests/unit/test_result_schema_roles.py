"""
Role-specific conflict metrics for experiments/result_schema.py.

Covers existing/incoming/winner/loser/unresolved breakdowns and the
resolved-conflict denominators used for all win/loss rates.
"""

from experiments.result_schema import (
    compute_stats_from_logs,
    create_default_stats,
)


def _conflict(winner, loser, existing=None, incoming=None, unresolved=False):
    entry = {"winner": winner, "loser": loser, "unresolved": unresolved}
    if existing is not None:
        entry["existing_agent"] = existing
    if incoming is not None:
        entry["incoming_agent"] = incoming
    return entry


def test_defaults_cover_all_computed_keys():
    """create_default_stats must include every key compute_stats_from_logs emits."""
    computed = compute_stats_from_logs([], [])
    defaults = create_default_stats()
    assert all(k in defaults for k in computed)


def test_role_breakdown_with_resolved_denominator():
    write_log = [{"agent_id": "a", "status": "committed"}]
    conflict_log = [
        _conflict("a", "b", existing="b", incoming="a"),   # incoming wins
        _conflict("b", "a", existing="b", incoming="a"),   # existing wins
        {"unresolved": True},
    ]
    s = compute_stats_from_logs(write_log, conflict_log)
    assert s["conflict_resolved"] == 2
    assert s["conflict_unresolved"] == 1
    assert s["conflict_losses"] == 2
    assert s["existing_wins"] == 1
    assert s["existing_losses"] == 1
    assert s["incoming_wins"] == 1
    assert s["incoming_losses"] == 1
    assert s["existing_win_rate"] == 0.5
    assert s["incoming_win_rate"] == 0.5
    assert s["existing_loss_rate"] == 0.5
    assert s["incoming_loss_rate"] == 0.5


def test_unresolved_does_not_dilute_win_rate():
    """Unresolved conflicts have no winner/loser and must not dilute the rates."""
    conflict_log = [
        _conflict("a", "b", existing="b", incoming="a"),
        {"unresolved": True},
        {"unresolved": True},
    ]
    s = compute_stats_from_logs([], conflict_log)
    assert s["incoming_win_rate"] == 1.0
    assert s["existing_loss_rate"] == 1.0


def test_no_roles_recorded_counts_zero():
    """Without existing_agent/incoming_agent, role counts stay zero (no fabrication)."""
    conflict_log = [_conflict("a", "b")]
    s = compute_stats_from_logs([], conflict_log)
    assert s["conflict_losses"] == 1
    assert s["existing_wins"] == 0
    assert s["incoming_wins"] == 0
    assert s["existing_win_rate"] == 0.0
    assert s["incoming_loss_rate"] == 0.0


def test_attacker_and_verifier_rates_use_resolved_denominator():
    conflict_log = [
        _conflict("verifier", "attacker"),
        {"unresolved": True},
    ]
    s = compute_stats_from_logs(
        [],
        conflict_log,
        attacker_ids=["attacker"],
        verification_ids=["verifier"],
    )
    assert s["attacker_conflict_loss_rate"] == 1.0
    assert s["verification_agent_win_rate"] == 1.0
    assert s["honest_conflict_loss_rate"] == 0.0


def test_gate_rejection_rate_uses_total_writes():
    write_log = [
        {"agent_id": "a", "status": "committed"},
        {"agent_id": "b", "status": "rejected"},
        {"agent_id": "c", "status": "rejected_suspicious"},
    ]
    s = compute_stats_from_logs(write_log, [])
    assert s["gate_rejected"] == 2
    assert s["gate_rejection_rate"] == 2 / 3
