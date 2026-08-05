"""
LCM Evaluation Harness (Phase 10)

Deterministic, offline evaluation of the core LCM scenarios through the REAL
pipeline — WritePipeline + validate_and_stamp + TrustManager +
ConflictResolutionEngine — with NO LLM/Ollama or LCM server required. Results
are emitted in the standard ``experiments/result_schema.ExperimentResult``
shape so they are comparable with real-agent runs.

Modes
-----
``offline`` (default)
    Deterministic scenario playback; scores final-state correctness against
    declared ground truth. ``agent_mode`` is honestly labelled ``"deterministic"``.

``real_llm``
    Requires a live LCM server and Ollama. This harness does not implement a
    live agent runner (that is ``experiments/run_real_agent_experiment.py``), so
    it probes availability and returns an honest ``skipped`` / ``blocked`` result
    — it never fabricates a live run.

Usage::

    python experiments/eval_harness.py --mode offline --save results/eval_offline.json
    python experiments/eval_harness.py --mode real_llm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# The crypto layer signs/verifies with the development Ed25519 provider key.
# Like the test-suite conftest, enable that dev fallback for this harness only
# when no real provider key is configured (fail-closed in production).
if not os.environ.get("LCM_EVIDENCE_PUBLIC_KEY"):
    os.environ.setdefault("LCM_ALLOW_DEV_EVIDENCE_KEY", "1")

from lcm_core.pipeline import WritePipeline
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager
from lcm_core.locking import AsyncLockManager
from lcm_core.loop_detection import LoopDetector
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType
from lcm_core.crypto import sign_evidence_message
from lcm_service.storage import SQLiteStorage

from experiments.result_schema import (
    compute_stats_from_logs,
    validate_result_schema,
)

# Fixed reference time so offline playback is fully deterministic.
REFERENCE_TIME = datetime(2026, 7, 14, 10, 0, 0)


# ---------------------------------------------------------------------------
# Scenario model
# ---------------------------------------------------------------------------

@dataclass
class WriteStep:
    """One pipeline write in a scenario's playback script."""
    agent_id: str
    path: str
    value: Any
    confidence: float = 0.8
    source: Optional[str] = None        # evidence source type (None → agent_claim_default)
    source_id: Optional[str] = None     # evidence source identifier
    sign: bool = True                   # attach a gateway evidence signature
    delta_seconds: float = 0.0          # recency offset from REFERENCE_TIME
    attacker: bool = False
    verifier: bool = False


@dataclass
class Scenario:
    name: str
    description: str
    steps: List[WriteStep] = field(default_factory=list)
    ground_truth: Dict[str, Any] = field(default_factory=dict)  # path -> value
    attacker_ids: List[str] = field(default_factory=list)
    verification_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

def _scenario_blood_type() -> Scenario:
    """User_input (attested) must override conflicting automated agents."""
    return Scenario(
        name="blood_type_conflict",
        description="Automated agents claim wrong blood types; attested user input sets the truth.",
        ground_truth={"patient.blood_type": "O+"},
        attacker_ids=["admin_bot", "doctor_agent"],
        verification_ids=["patient_user"],
        steps=[
            WriteStep("admin_bot", "patient.blood_type", "AB+",
                      confidence=0.9, attacker=True),
            WriteStep("doctor_agent", "patient.blood_type", "A-",
                      confidence=0.85, attacker=True),
            WriteStep("patient_user", "patient.blood_type", "O+",
                      confidence=1.0, source="user_input", source_id="user://patient",
                      verifier=True),
        ],
    )


def _scenario_mandela_injection() -> Scenario:
    """Adversarial agent_claim injection must NOT displace verified document fact."""
    return Scenario(
        name="mandela_injection",
        description="Attacker injects a false 'Mandela effect' claim; verified document evidence must win.",
        ground_truth={"quote.origin": "No, I am your father"},
        attacker_ids=["memes_bot"],
        verification_ids=["archive_service"],
        steps=[
            WriteStep("archive_service", "quote.origin", "No, I am your father",
                      confidence=0.9, source="document", source_id="doc://starwars_script",
                      verifier=True),
            WriteStep("memes_bot", "quote.origin", "Luke, I am your father",
                      confidence=0.99, attacker=True),
        ],
    )


def _scenario_two_frameworks() -> Scenario:
    """Equal-trust agents from two frameworks write opposite values; the higher-
    authority (signed database) claim must win the conflict."""
    return Scenario(
        name="two_frameworks",
        description="LangChain-style and CrewAI-style agents disagree; signed tool output decides.",
        ground_truth={"market.rate": "4.25"},
        attacker_ids=["framework_b_agent"],
        verification_ids=["market_feed"],
        steps=[
            WriteStep("framework_a_agent", "market.rate", "4.25",
                      confidence=0.8, source="tool_output", source_id="tool://market",
                      verifier=True),
            WriteStep("framework_b_agent", "market.rate", "3.75",
                      confidence=0.8, attacker=True),
        ],
    )


ALL_SCENARIOS = [
    _scenario_blood_type(),
    _scenario_mandela_injection(),
    _scenario_two_frameworks(),
]


# ---------------------------------------------------------------------------
# Deterministic offline runner
# ---------------------------------------------------------------------------

def _run_scenario(scenario: Scenario) -> Dict[str, Any]:
    """Play a scenario against a fresh pipeline; return an ExperimentResult."""
    t0 = time.perf_counter()
    storage = SQLiteStorage(":memory:")
    trust = TrustManager()
    pipeline = WritePipeline(
        storage=storage,
        trust_manager=trust,
        conflict_engine=ConflictResolutionEngine(uncertainty_threshold=0.05),
        lock_manager=AsyncLockManager(),
        loop_detector=LoopDetector(),
    )

    write_log: List[Dict[str, Any]] = []
    conflict_log: List[Dict[str, Any]] = []

    async def _play():
        for step in scenario.steps:
            ts = REFERENCE_TIME - timedelta(seconds=step.delta_seconds)
            evidence_records = None
            evidence_signature = None
            if step.source:
                ev_type = EvidenceType(step.source)
                evidence_records = [EvidenceRecord(
                    evidence_type=ev_type,
                    source_id=step.source_id,
                    relevance_score=1.0,
                )]
                if step.sign:
                    evidence_signature = sign_evidence_message(
                        ev_type, step.source_id,
                    )
            result = await pipeline.process(
                {
                    "agent_id": step.agent_id,
                    "session_id": f"eval_{scenario.name}_{step.agent_id}",
                    "timestamp": ts,
                    "confidence_score": step.confidence,
                    "assertion_payload": {step.path: step.value},
                },
                evidence_records=evidence_records,
                evidence_signature=evidence_signature,
            )
            entry = {
                "agent_id": step.agent_id,
                "path": step.path,
                "value": step.value,
                "status": result.status,
                "error": None,
                "attacker": step.attacker,
                "verifier": step.verifier,
            }
            write_log.append(entry)
            if result.conflict is not None:
                existing = storage.get_existing(step.path)
                conflict_log.append({
                    "existing_agent": existing.agent_id if existing is not None else None,
                    "incoming_agent": step.agent_id,
                    "winner": result.conflict.winner.agent_id if result.conflict.winner else None,
                    "loser": result.conflict.loser.agent_id if result.conflict.loser else None,
                    "unresolved": result.conflict.unresolved,
                    "psi_winner": result.conflict.psi_winner,
                    "psi_loser": result.conflict.psi_loser,
                })

    asyncio.run(_play())

    # Score final state against ground truth (path -> expected value).
    correct_counts = 0
    for path, expected in scenario.ground_truth.items():
        live = storage.get_existing(path)
        actual = None
        if live is not None:
            payload = live.assertion_payload
            actual = payload.get(path) if isinstance(payload, dict) else payload
        if actual == expected:
            correct_counts += 1
    final_correct = (
        correct_counts / len(scenario.ground_truth) if scenario.ground_truth else None
    )

    final_memory = {}
    for path in scenario.ground_truth:
        live = storage.get_existing(path)
        if live is not None:
            payload = live.assertion_payload
            final_memory[path] = payload.get(path) if isinstance(payload, dict) else payload

    stats = compute_stats_from_logs(
        write_log,
        conflict_log,
        agent_mode="deterministic",
        final_state_correct=final_correct,
        attacker_ids=scenario.attacker_ids,
        verification_ids=scenario.verification_ids,
    )

    result = {
        "backend": "lcm_pipeline",
        "agent_mode": "deterministic",
        "llm_available": False,
        "scenario": scenario.name,
        "description": scenario.description,
        "trials": 1,
        "verification": True,
        "write_log": write_log,
        "conflict_log": conflict_log,
        "final_memory": final_memory,
        "stats": stats,
        "timing": {"scenario_seconds": round(time.perf_counter() - t0, 4)},
        "total_runtime_seconds": round(time.perf_counter() - t0, 4),
    }
    if not validate_result_schema(result):
        raise RuntimeError(f"Scenario '{scenario.name}' produced an invalid result schema")
    return result


def run_offline_eval(scenarios: Optional[List[Scenario]] = None) -> List[Dict[str, Any]]:
    """Run all scenarios deterministically; return result dicts."""
    results = []
    for scenario in scenarios or ALL_SCENARIOS:
        results.append(_run_scenario(scenario))
    return results


# ---------------------------------------------------------------------------
# Honest real-LLM availability probe
# ---------------------------------------------------------------------------

def _server_available(url: str, timeout: float = 1.5) -> bool:
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def run_real_llm_eval() -> Dict[str, Any]:
    """Probe live servers; return an honest skipped/blocked result.

    Live agent execution belongs to ``experiments/run_real_agent_experiment.py``;
    this harness only reports availability so a run is never mislabelled as live
    when no servers are reachable.
    """
    lcm_url = os.getenv("LCM_URL", "http://localhost:8000")
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    lcm_up = _server_available(lcm_url)
    ollama_up = _server_available(ollama_url)

    if lcm_up and ollama_up:
        return {
            "backend": "real_agent",
            "agent_mode": "real_llm",
            "llm_available": True,
            "scenario": "all",
            "skipped": True,
            "skip_reason": (
                "Servers are reachable but live agent execution is delegated to "
                "experiments/run_real_agent_experiment.py; this harness never "
                "runs or fabricates live agents."
            ),
            "servers": {"lcm": lcm_up, "ollama": ollama_up},
            "trials": 0,
            "stats": compute_stats_from_logs([], [], agent_mode="real_llm"),
        }

    return {
        "backend": "real_agent",
        "agent_mode": "real_llm",
        "llm_available": False,
        "scenario": "all",
        "skipped": True,
        "skip_reason": (
            "No live LCM/Ollama server available; real-agent runs are blocked. "
            "Deterministic offline evaluation is available via --mode offline."
        ),
        "servers": {"lcm": lcm_up, "ollama": ollama_up},
        "trials": 0,
        "stats": compute_stats_from_logs([], [], agent_mode="real_llm"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _summary(results: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append(f"{'scenario':<28} {'status':<10} {'final_correct':<14} {'conflicts':<9} {'rejected':<9}")
    for r in results:
        s = r["stats"]
        fc = s.get("final_state_correct")
        lines.append(
            f"{r['scenario']:<28} {r['agent_mode']:<10} "
            f"{(f'{fc:.0%}' if fc is not None else 'n/a'):<14} "
            f"{s['conflict_resolved'] + s['conflict_unresolved']:<9} "
            f"{s['gate_rejected']:<9}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LCM evaluation harness (Phase 10)")
    parser.add_argument("--mode", choices=["offline", "real_llm"], default="offline")
    parser.add_argument("--save", default=None, help="Path to write JSON result(s)")
    parser.add_argument("--list", action="store_true", help="List scenario names and exit")
    args = parser.parse_args(argv)

    if args.list:
        for sc in ALL_SCENARIOS:
            print(f"{sc.name:<28} {sc.description}")
        return 0

    if args.mode == "real_llm":
        result = run_real_llm_eval()
        print(json.dumps(result, indent=2, default=str))
        if args.save:
            Path(args.save).parent.mkdir(parents=True, exist_ok=True)
            Path(args.save).write_text(json.dumps(result, indent=2, default=str))
        return 0

    results = run_offline_eval()
    print(_summary(results))
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nSaved {len(results)} result(s) to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
