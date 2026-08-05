"""
Unified Result Schema for LCM Experiments

This module defines the standard result schema that all experiments should use.
This ensures consistent metrics reporting across different experiment types.

Required fields in every experiment result:
- total_writes: Total number of write attempts
- gate_rejected: Number of writes rejected at the gate (evidence/trust verification)
- conflict_resolved: Number of conflicts that were resolved
- conflict_unresolved: Number of conflicts that remained unresolved
- conflict_losses: Number of resolved conflicts with a recorded loser (not gate-rejected)
- committed: Number of writes that were successfully committed
- final_state_correct: Whether the final state is correct (requires ground truth)
- gate_rejection_rate: gate_rejected / total_writes
- conflict_loss_rate: conflict_losses / resolved_conflicts (or 0 if no resolved conflicts)
- Role-specific conflict metrics (existing/incoming/winner/loser/unresolved):
  each conflict pits the pre-existing memory holder ("existing") against the
  writer that triggered it ("incoming"). `existing_agent` / `incoming_agent`
  are read from each conflict-log entry when present; winner/loser are always
  available for resolved conflicts. Win/loss rates use a resolved-conflict
  denominator (conflicts with both a winner and a loser).
- attack_success_rate: For adversarial scenarios, rate of successful attacks
- tool_call_failed: Number of times tool calls failed (for real LLM agents)
- agent_mode: "real_llm" (all real-backend runs must be real LLM)
"""

from typing import TypedDict, Optional, Any, Dict, List
import hashlib
import json
from datetime import datetime

from lcm_core.status import (
    STATUS_COMMITTED,
    STATUS_CONFLICT_RESOLVED,
    STATUS_UNRESOLVED,
    COMMIT_STATUSES,
    CONFLICT_STATUSES,
    REJECTION_STATUSES,
)


class ExperimentStats(TypedDict):
    """Standardized statistics for experiment results."""
    total_writes: int
    gate_rejected: int
    conflict_resolved: int
    conflict_unresolved: int
    conflict_losses: int
    existing_wins: int
    existing_losses: int
    incoming_wins: int
    incoming_losses: int
    committed: int
    successful_live_commits: int
    resolved_conflict_commits: int
    final_state_correct: Optional[float]
    gate_rejection_rate: float
    conflict_loss_rate: float
    existing_win_rate: float
    existing_loss_rate: float
    incoming_win_rate: float
    incoming_loss_rate: float
    attacker_conflict_loss_rate: float
    honest_conflict_loss_rate: float
    verification_agent_win_rate: float
    attack_success_rate: Optional[float]
    tool_call_failed: int
    agent_mode: str


class ExperimentResult(TypedDict):
    """Standardized result schema for all experiments."""
    backend: str  # "langchain", "ollama"
    agent_mode: str  # "real_llm"
    llm_available: bool
    scenario: str
    trials: int
    verification: bool
    write_log: list
    conflict_log: list
    final_memory: dict
    stats: ExperimentStats
    timing: Dict[str, float]
    total_runtime_seconds: Optional[float]


def create_default_stats(agent_mode: str = "real_llm") -> ExperimentStats:
    """Create a default stats dictionary with all required fields."""
    return {
        "total_writes": 0,
        "gate_rejected": 0,
        "conflict_resolved": 0,
        "conflict_unresolved": 0,
        "conflict_losses": 0,
        "existing_wins": 0,
        "existing_losses": 0,
        "incoming_wins": 0,
        "incoming_losses": 0,
        "committed": 0,
        "successful_live_commits": 0,
        "resolved_conflict_commits": 0,
        "final_state_correct": None,
        "gate_rejection_rate": 0.0,
        "conflict_loss_rate": 0.0,
        "existing_win_rate": 0.0,
        "existing_loss_rate": 0.0,
        "incoming_win_rate": 0.0,
        "incoming_loss_rate": 0.0,
        "attacker_conflict_loss_rate": 0.0,
        "honest_conflict_loss_rate": 0.0,
        "verification_agent_win_rate": 0.0,
        "attack_success_rate": None,
        "tool_call_failed": 0,
        "agent_mode": agent_mode,
    }


def compute_stats_from_logs(
    write_log: list,
    conflict_log: list,
    agent_mode: str = "real_llm",
    final_state_correct: Optional[float] = None,
    attack_success_rate: Optional[float] = None,
    attacker_ids: Optional[list] = None,
    verification_ids: Optional[list] = None,
) -> ExperimentStats:
    """
    Compute standardized statistics from write and conflict logs.
    
    Args:
        write_log: List of write attempts with status field
        conflict_log: List of conflict records
        agent_mode: "real_llm"
        final_state_correct: Whether final state is correct (requires ground truth)
        attack_success_rate: For adversarial scenarios
        attacker_ids: List of attacker agent IDs (for attacker_conflict_loss_rate)
        verification_ids: List of verifier agent IDs (for verification_agent_win_rate)
    
    Returns:
        ExperimentStats dictionary with computed metrics
    """
    attacker_ids = attacker_ids or []
    verification_ids = verification_ids or []
    
    total_writes = len(write_log)
    
    # Count gate rejections using shared constants
    gate_rejected = sum(
        1 for w in write_log
        if w.get("status") in REJECTION_STATUSES
    )
    
    # Direct commits vs wins through conflict resolution
    direct_commits = sum(1 for w in write_log if w.get("status") == STATUS_COMMITTED)
    resolved_conflict_commits = sum(
        1 for c in conflict_log
        if not c.get("unresolved", False) and c.get("winner") is not None
    )
    successful_live_commits = direct_commits + resolved_conflict_commits
    
    # Count conflict resolution using shared constants
    conflict_resolved = sum(
        1 for c in conflict_log if not c.get("unresolved", False)
    )
    conflict_unresolved = sum(
        1 for c in conflict_log if c.get("unresolved", False)
    )
    
    # Role-aware conflict metrics (existing/incoming/winner/loser/unresolved).
    # A conflict always pits the pre-existing memory holder ("existing") against
    # the writer that triggered it ("incoming").  Conflict-log entries should
    # record ``existing_agent`` / ``incoming_agent`` when available; for entries
    # without them, existing/incoming win/loss counts simply stay zero (honest
    # under-reporting rather than a fabricated role split).
    existing_wins = 0
    existing_losses = 0
    incoming_wins = 0
    incoming_losses = 0
    attacker_losses = 0
    honest_losses = 0
    verification_wins = 0

    for c in conflict_log:
        if c.get("unresolved", False):
            continue
        loser = c.get("loser")
        winner = c.get("winner")
        if loser is None or winner is None:
            continue
        existing_agent = c.get("existing_agent")
        incoming_agent = c.get("incoming_agent")
        if existing_agent is not None:
            if existing_agent == winner:
                existing_wins += 1
            if existing_agent == loser:
                existing_losses += 1
        if incoming_agent is not None:
            if incoming_agent == winner:
                incoming_wins += 1
            if incoming_agent == loser:
                incoming_losses += 1
        if loser in attacker_ids:
            attacker_losses += 1
        elif attacker_ids:
            honest_losses += 1
        if winner in verification_ids:
            verification_wins += 1

    total_conflicts = len(conflict_log)

    # Resolved-conflict denominator: conflicts that actually produced a
    # winner/loser. Unresolved conflicts have no winner/loser and must not
    # dilute win/loss rates.
    resolved_conflicts = sum(
        1 for c in conflict_log
        if not c.get("unresolved", False)
        and c.get("winner") is not None
        and c.get("loser") is not None
    )

    return {
        "total_writes": total_writes,
        "gate_rejected": gate_rejected,
        "conflict_resolved": conflict_resolved,
        "conflict_unresolved": conflict_unresolved,
        "conflict_losses": resolved_conflicts,
        "existing_wins": existing_wins,
        "existing_losses": existing_losses,
        "incoming_wins": incoming_wins,
        "incoming_losses": incoming_losses,
        "committed": direct_commits,
        "successful_live_commits": successful_live_commits,
        "resolved_conflict_commits": resolved_conflict_commits,
        "final_state_correct": final_state_correct,
        "gate_rejection_rate": gate_rejected / total_writes if total_writes > 0 else 0.0,
        "conflict_loss_rate": resolved_conflicts / total_conflicts if total_conflicts > 0 else 0.0,
        "existing_win_rate": existing_wins / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "existing_loss_rate": existing_losses / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "incoming_win_rate": incoming_wins / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "incoming_loss_rate": incoming_losses / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "attacker_conflict_loss_rate": attacker_losses / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "honest_conflict_loss_rate": honest_losses / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "verification_agent_win_rate": verification_wins / resolved_conflicts if resolved_conflicts > 0 else 0.0,
        "attack_success_rate": attack_success_rate,
        "tool_call_failed": sum(1 for w in write_log if w.get("error") is not None),
        "agent_mode": agent_mode,
    }


def validate_result_schema(result: Dict[str, Any]) -> bool:
    """
    Validate that a result dictionary conforms to the required schema.
    
    Args:
        result: Result dictionary to validate
    
    Returns:
        True if valid, False otherwise
    """
    required_fields = [
        "backend",
        "agent_mode",
        "stats",
    ]
    
    # Check top-level fields
    for field in required_fields:
        if field not in result:
            return False
    
    # Check stats fields
    stats = result["stats"]
    required_stats = [
        "total_writes",
        "gate_rejected",
        "conflict_resolved",
        "conflict_unresolved",
        "conflict_losses",
        "committed",
        "gate_rejection_rate",
        "conflict_loss_rate",
        "agent_mode",
    ]
    
    for field in required_stats:
        if field not in stats:
            return False
    
    return True


def validate_real_agent_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a real-agent experiment artifact against qualifying-run rules.

    Enforces:
    - agent_mode must be "real_llm"
    - llm_available must be True
    - No simulation markers in write_log or agent results
    - Each trial must have a unique run_id
    - Each trial must have a unique session_id in write_log entries
    - verified_confidence must be distinguished from reported confidence
    - Server-stamped values must be read back from LCM (verified_confidence present)
    - No arbitrary trust initialization (cold-start prior only)
    - trial_results must be present when trials > 1

    Args:
        artifact: Result dictionary from a real-agent experiment

    Returns:
        Dict with "valid" (bool) and "issues" (list of str)
    """
    issues: List[str] = []

    # Check agent_mode
    if artifact.get("agent_mode") != "real_llm":
        issues.append(f"agent_mode must be 'real_llm', got '{artifact.get('agent_mode')}'")

    # Check llm_available
    if artifact.get("llm_available") is not True:
        issues.append("llm_available must be True for real-agent artifacts")

    # Check for simulation markers in write_log
    write_log = artifact.get("write_log", [])
    for entry in write_log:
        if "simulated" in str(entry.get("value", "")).lower():
            issues.append(f"Simulated value found in write_log: {entry}")
            break

    # Check for simulation markers in agent results
    agent_results = artifact.get("agent_results", {})
    for role, result in agent_results.items():
        if isinstance(result, dict):
            error = result.get("error", "")
            if "simulated" in error.lower() or "fallback" in error.lower():
                issues.append(f"Simulation/fallback marker in {role} result: {error}")

    # Check trial uniqueness when trials > 1
    trials = artifact.get("trials", 1)
    if trials > 1:
        trial_results = artifact.get("trial_results", [])
        if len(trial_results) != trials:
            issues.append(
                f"Expected {trials} trial results but got {len(trial_results)}"
            )

        run_ids = [t.get("run_id") for t in trial_results if t.get("run_id")]
        if len(run_ids) != len(set(run_ids)):
            issues.append("Duplicate run_ids found across trials")

    # Check verified_confidence is present and distinct from reported confidence
    final_memory = artifact.get("final_memory", {})
    for path, entry in final_memory.items():
        if isinstance(entry, dict) and "error" not in entry:
            reported = entry.get("confidence")
            verified = entry.get("verified_confidence")
            if verified is None:
                issues.append(
                    f"Missing verified_confidence for path '{path}' — "
                    f"server-stamped value not read back from LCM"
                )
            elif reported is not None and verified == reported:
                issues.append(
                    f"verified_confidence equals reported confidence for path '{path}' — "
                    f"server-stamped value should differ from agent-provided value"
                )

    # Check no arbitrary trust initialization (cold-start only)
    trust_scores = artifact.get("trust_scores", {})
    for agent_id, score in trust_scores.items():
        if isinstance(score, (int, float)) and score > 0.5:
            issues.append(
                f"Arbitrary trust initialization detected for '{agent_id}': "
                f"score={score} (cold-start prior should be ~0.5)"
            )

    return {
        "valid": len(issues) == 0,
        "issues": issues,
    }


# Strict qualification validator.  Kept as the final definition deliberately so
# older callers retain the public function name while qualification is based on
# execution evidence rather than self-declared metadata.
def validate_real_agent_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            issues.append(message)

    need(artifact.get("classification") == "real_agent", "classification must be 'real_agent'")
    need(artifact.get("agent_mode") == "real_llm", "agent_mode must be 'real_llm'")
    need(artifact.get("backend") == "ollama", "backend must be 'ollama'")
    need(artifact.get("model") == "llama3.1:8b", "model must be 'llama3.1:8b'")
    need(artifact.get("llm_available") is True, "llm_available must be True")
    for field in ("run_id", "started_at", "completed_at", "git_commit",
                  "configuration_hash", "database_identity", "service_url"):
        need(bool(artifact.get(field)), f"Missing required run metadata: {field}")
    need(artifact.get("git_commit") not in ("unavailable", "unknown"),
         "git_commit must be an actual commit, not a placeholder")

    invocations = artifact.get("model_invocations", [])
    agents = {i.get("agent_id") for i in invocations if i.get("agent_id")}
    need(len(agents) >= 2, "At least two distinct model agents must execute")
    need(any(i.get("response_sha256") and (i.get("raw_response") or i.get("response_redacted"))
             for i in invocations), "No non-empty Ollama response evidence")
    for invocation in invocations:
        for field in ("agent_id", "role", "model", "started_at", "completed_at",
                      "prompt_sha256", "response_sha256", "structured_tool_calls"):
            need(field in invocation, f"Invocation missing {field}")
        if not invocation.get("error"):
            need(bool(invocation.get("response_sha256")),
                 f"Successful invocation for {invocation.get('agent_id')} lacks response hash")
            need(bool(invocation.get("raw_response") or invocation.get("response_redacted")),
                 f"Successful invocation for {invocation.get('agent_id')} lacks response evidence")
    calls = [c for i in invocations for c in i.get("structured_tool_calls", [])]
    need(any(c.get("name") == "write_memory" and c.get("success") is True for c in calls),
         "No successful structured model-generated write_memory call")
    readbacks = artifact.get("memory_readbacks", [])
    need(any(r.get("success") is True for r in readbacks),
         "No successful readback from the HTTP LCM service")
    need(any(r.get("server_provenance") for r in readbacks), "Missing server-stamped provenance")
    need(not artifact.get("scripted_fallback_writes"), "Scripted fallback writes are forbidden")
    need(not artifact.get("mock_data_used"), "Mock data is forbidden")
    need(not artifact.get("trust_initialized"), "Arbitrary trust initialization is forbidden")

    write_log = artifact.get("write_log", [])
    need(not any("simulated" in str(e).lower() or "fallback_write" in str(e).lower()
                 for e in write_log), "Simulation/fallback marker in write log")
    need(all(e.get("status") in ("committed", "conflict_resolved", "unresolved")
             for e in write_log if e.get("success") is True),
         "Failed HTTP write presented as successful")

    trials = artifact.get("trials", 0)
    trial_results = artifact.get("trial_results", [])
    need(isinstance(trials, int) and trials > 0, "trials must be a positive integer")
    need(len(trial_results) == trials, f"Expected {trials} trial results but got {len(trial_results)}")
    run_ids = [t.get("run_id") for t in trial_results]
    need(bool(run_ids) and all(run_ids) and len(run_ids) == len(set(run_ids)),
         "Trial run_ids must be present and unique")

    initial_trust = artifact.get("initial_trust", {})
    need(bool(initial_trust), "Missing initial trust metadata")
    for agent_id, meta in initial_trust.items():
        score = meta.get("trust_score") if isinstance(meta, dict) else meta
        count = meta.get("outcome_count") if isinstance(meta, dict) else None
        need(score == 0.5 and count in (0, None),
             f"Non-neutral initial trust for {agent_id}: score={score}, outcomes={count}")

    for path, entry in artifact.get("final_memory", {}).items():
        if isinstance(entry, dict) and "error" not in entry:
            for field in ("verified_confidence", "authority_score", "source_type"):
                need(entry.get(field) is not None, f"Missing {field} for path '{path}'")

    if artifact.get("ground_truth_status") != "available":
        for key in ("final_state_correct", "resolution_accuracy", "attack_success_rate", "critic_correctness"):
            need(artifact.get(key) is None and artifact.get("stats", {}).get(key) is None,
                 f"{key} claimed without independently adjudicated ground truth")

    contract = artifact.get("scenario_contract") or {}
    need(contract.get("scenario_id") == artifact.get("scenario"),
         "Scenario metadata does not match actual execution")
    if contract.get("expected_conflict_opportunities", 0) > 0:
        paths: Dict[str, Dict[str, set]] = {}
        for entry in write_log:
            bucket = paths.setdefault(str(entry.get("path")), {"agents": set(), "values": set()})
            bucket["agents"].add(entry.get("agent_id"))
            bucket["values"].add(str(entry.get("value")))
        need(any(len(v["agents"]) >= 2 and len(v["values"]) >= 2 for v in paths.values()),
             "Conflict scenario lacks differing writes by distinct agents to one path")
        need(bool(artifact.get("conflict_log")), "Conflict scenario recorded no conflict")

    if artifact.get("artifact_sha256"):
        body = dict(artifact)
        body.pop("artifact_sha256", None)
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        need(hashlib.sha256(canonical.encode("utf-8")).hexdigest() == artifact["artifact_sha256"],
             "Artifact SHA-256 does not match canonical JSON")
    return {"valid": not issues, "issues": issues, "status": "VALID" if not issues else "INVALID"}
