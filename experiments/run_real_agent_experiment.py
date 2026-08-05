"""
Unified Experiment Entrypoint for LCM Real Agent Experiments

Canonical entrypoint for running real agent experiments with different backends:
- ollama: Real LLM agents via Ollama
- langchain: LangChain-based agents

The LangChain and Ollama backend implementations previously lived in the now-
deleted `experiments/real_agent_experiment.py` and
`experiments/real_agent_experiment_ollama.py` modules.  They have been folded
into this single entry point so there is exactly ONE canonical runner.

Usage:
    python experiments/run_real_agent_experiment.py --backend ollama --scenario colluding --verification off
    python experiments/run_real_agent_experiment.py --backend langchain --scenario dynamic --save results.json
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import requests
import subprocess
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from experiments.result_schema import (
    create_default_stats,
    compute_stats_from_logs,
    validate_result_schema,
)

from lcm_core.status import (
    STATUS_COMMITTED,
    STATUS_CONFLICT_RESOLVED,
    STATUS_UNRESOLVED,
    CONFLICT_STATUSES,
)

from lcm_client import LCMClient


# ---------------------------------------------------------------------------
# Shared LCM client & execution logs
# ---------------------------------------------------------------------------

_lcm = LCMClient(base_url=os.getenv("LCM_URL", "http://localhost:8000"))

_write_log: List[Dict[str, Any]] = []
_conflict_log: List[Dict[str, Any]] = []
_current_session_id = "real_experiment_unset"


def _reset_logs() -> None:
    _write_log.clear()
    _conflict_log.clear()


def _git_commit() -> str:
    candidates = ["git", r"C:\Program Files\Git\cmd\git.exe"]
    for executable in candidates:
        try:
            return subprocess.check_output([executable, "-C", str(Path(__file__).resolve().parent.parent),
                                            "rev-parse", "HEAD"], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
    return ""


# ---------------------------------------------------------------------------
# LangChain backend implementation (was experiments/real_agent_experiment.py)
# ---------------------------------------------------------------------------

from langchain.tools import tool


@tool
def write_memory(
    agent_id: str,
    path: str,
    value: str,
    confidence: float = 0.8,
) -> str:
    """
    Write a claim or finding to shared LCM memory.

    Args:
        agent_id:   Your agent ID string (e.g. 'research_agent').
        path:       Dot-separated memory path (e.g. 'research.key_insight').
        value:      The claim, finding, or correction to store.
        confidence: Your confidence in this claim (0.0 - 1.0).

    SECURITY NOTE: Evidence type is determined exclusively by the middleware.
    Agents cannot supply evidence_type, evidence_source, or evidence_verified.
    This prevents evidence-label forgery attacks. When no evidence is supplied,
    the middleware uses a default fallback (agent_claim_default) with low authority.

    Returns:
        Status string. Conflict details are included if another agent held
        a different value at the same path.
    """
    t0 = time.perf_counter()
    try:
        result = _lcm.write(
            agent_id=agent_id,
            session_id=_current_session_id,
            confidence_score=confidence,
            assertion_payload={path: value},
            timestamp=datetime.utcnow(),
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        status = result.get("status", "unknown")

        _write_log.append({
            "agent_id":        agent_id,
            "path":            path,
            "value":           value,
            "confidence":      confidence,
            "status":          status,
            "latency_ms":      round(latency_ms, 2),
            "provenance_id":   result.get("provenance_id"),
            "evidence_type":   "agent_claim_default",
            "success":         status in (STATUS_COMMITTED, STATUS_CONFLICT_RESOLVED, STATUS_UNRESOLVED),
            "tool_transport":  "http",
            "service_url":     _lcm.base_url,
        })

        if status in CONFLICT_STATUSES:
            _conflict_log.append({
                "path":                 path,
                "winner":               result.get("winner_agent"),
                "loser":                result.get("loser_agent"),
                "existing_agent":       result.get("winner_agent") if result.get("winner_agent") != agent_id else result.get("loser_agent"),
                "incoming_agent":       agent_id,
                "message":              result.get("message", ""),
                "reason":               result.get("message", ""),
                "unresolved":           result.get("unresolved", False),
                "psi_winner_breakdown": result.get("psi_winner_breakdown"),
                "psi_loser_breakdown":  result.get("psi_loser_breakdown"),
            })
            if status == STATUS_CONFLICT_RESOLVED:
                return (
                    f"Conflict resolved at '{path}'. "
                    f"Winner: {result.get('winner_agent')}, Loser: {result.get('loser_agent')}. "
                    f"{result.get('message', '')}"
                )
            else:
                return f"Conflict unresolved at '{path}': {result.get('message', '')}"

        return f"Written to '{path}' (status={status}, provenance={result.get('provenance_id','?')[:8]})"
    except Exception as exc:
        _write_log.append({"agent_id": agent_id, "path": path, "value": value,
                           "confidence": confidence, "status": "http_error",
                           "success": False, "error": str(exc),
                           "tool_transport": "http", "service_url": _lcm.base_url})
        return f"Failed to write to '{path}': {exc}"


@tool
def read_memory(path: str) -> str:
    """
    Read the current committed value at a memory path.

    Args:
        path: Dot-separated memory path to query. Pass ONE path at a time.

    Returns:
        The current committed value and the agent that wrote it,
        or a message if the path is empty.
    """
    try:
        context = _lcm.get_context(path)
        facts = context.get("facts", [])
        if not facts:
            return f"No memory at '{path}' yet."
        fact = facts[0]
        payload = fact.get("assertion_payload", {})
        value = payload.get(path, str(payload))
        conf = fact.get("confidence_score")
        conf_str = f"{conf:.2f}" if conf is not None else "n/a"
        return f"'{path}' = '{value}' [agent={fact['agent_id']}, conf={conf_str}]"
    except Exception as exc:
        return f"Failed to read from '{path}': {exc}"


_LC_TOOLS = [write_memory, read_memory]


def _make_lc_agent(system_prompt: str, max_iterations: int = 12, model: str = "llama3.1:8b"):
    """Create a LangChain agent with memory tools - requires a real LLM, fails closed."""

    # Try real LangChain agent first - fail if not available
    try:
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_ollama import ChatOllama
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

        llm = ChatOllama(model=model, temperature=0)
        try:
            llm = llm.bind_tools(_LC_TOOLS)
        except Exception:
            pass

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(llm, _LC_TOOLS, prompt)
        return AgentExecutor(
            agent=agent,
            tools=_LC_TOOLS,
            verbose=True,
            max_iterations=max_iterations,
            early_stopping_method="force",
            handle_parsing_errors=True,
        )
    except ImportError as e:
        print(f"[ERROR] Cannot import LangChain: {e}")
        raise SystemExit(1)
    except Exception as e:
        print(f"[ERROR] Failed to create LangChain agent: {e}")
        print("Ensure Ollama is running with model llama3.1:8b available")
        raise SystemExit(1)


RESEARCH_SYSTEM = """\
You are a Research Agent. agent_id = 'research_agent'.

Available tools:
- write_memory: Store research findings to shared memory
- read_memory: Retrieve research information from shared memory

Your task: Research the given topic and provide your analysis. You may use memory tools to record your findings, but you decide whether and what to write.
"""

VERIFICATION_SYSTEM = """\
You are a Senior Verification Agent. agent_id = 'verification_agent'.

Available tools:
- write_memory: Store verification results to shared memory
- read_memory: Retrieve research information from shared memory

Your task: Verify and refine research findings. You may use memory tools to access current information and provide your verification.
"""

SYNTHESIS_SYSTEM = """\
You are a Synthesis Agent. agent_id = 'synthesis_agent'.

Available tools:
- write_memory: Store synthesis results to shared memory
- read_memory: Retrieve all research information from shared memory

Your task: Synthesize the complete research picture into a coherent summary. You may use memory tools to access all relevant information and provide your synthesis.
"""


def run_experiment(
    topic: str = "quantum computing applications in drug discovery",
    save_path: Optional[str] = None,
    model: str = "llama3.1:8b",
    scenario: str = "basic",
    trials: int = 1,
) -> Dict[str, Any]:
    """
    Run the real agent experiment with LangChain agents and LCM.

    Args:
        topic: Research topic for agents to analyze
        save_path: Optional path to save results JSON
        model: Ollama model to use (default: llama3.1:8b)
        scenario: Scenario type (basic, adversarial, colluding, dynamic)
        trials: Number of independent trials to run

    Returns:
        Dict with write_log, conflict_log, final_memory, timing, and stats
    """
    _reset_logs()

    print("=" * 70)
    print(f"REAL AGENT EXPERIMENT: {topic}")
    print(f"Model: {model} | Scenario: {scenario} | Trials: {trials}")
    print("=" * 70)

    all_trial_results = []
    total_start = time.perf_counter()

    for trial_idx in range(trials):
        _reset_logs()
        trial_start = time.perf_counter()
        run_id = f"langchain_trial_{scenario}_{trial_idx}_{int(time.time())}"

        print(f"\n--- Trial {trial_idx + 1}/{trials} (run_id={run_id}) ---")

        start_total = time.perf_counter()

        print("\n[1/3] Running Research Agent...")
        t0 = time.perf_counter()
        research_agent = _make_lc_agent(RESEARCH_SYSTEM, max_iterations=12, model=model)
        try:
            research_result = research_agent.invoke({"input": f"Research {topic}"})
            research_time = time.perf_counter() - t0
            print(f"  Research agent completed in {research_time:.2f}s")
        except Exception as e:
            research_time = time.perf_counter() - t0
            print(f"  Research agent failed: {e}")
            research_result = {"error": str(e)}

        print("\n[2/3] Running Verification Agent...")
        t0 = time.perf_counter()
        verification_agent = _make_lc_agent(VERIFICATION_SYSTEM, max_iterations=12, model=model)
        try:
            verification_result = verification_agent.invoke({"input": f"Verify findings about {topic}"})
            verification_time = time.perf_counter() - t0
            print(f"  Verification agent completed in {verification_time:.2f}s")
        except Exception as e:
            verification_time = time.perf_counter() - t0
            print(f"  Verification agent failed: {e}")
            verification_result = {"error": str(e)}

        print("\n[3/3] Running Synthesis Agent...")
        t0 = time.perf_counter()
        synthesis_agent = _make_lc_agent(SYNTHESIS_SYSTEM, max_iterations=12, model=model)
        try:
            synthesis_result = synthesis_agent.invoke({"input": f"Synthesize research on {topic}"})
            synthesis_time = time.perf_counter() - t0
            print(f"  Synthesis agent completed in {synthesis_time:.2f}s")
        except Exception as e:
            synthesis_time = time.perf_counter() - t0
            print(f"  Synthesis agent failed: {e}")
            synthesis_result = {"error": str(e)}

        trial_total = time.perf_counter() - trial_start

        final_memory = {}
        for path in ["research.key_insight", "research.main_challenge", "research.current_state", "research.synthesis"]:
            try:
                context = _lcm.get_context(path)
                facts = context.get("facts", [])
                if facts:
                    fact = facts[0]
                    payload = fact.get("assertion_payload", {})
                    verified_conf = fact.get("verified_confidence")
                    final_memory[path] = {
                        "value": payload.get(path, str(payload)),
                        "agent_id": fact.get("agent_id"),
                        "confidence": fact.get("confidence_score"),
                        "verified_confidence": verified_conf,
                        "provenance_id": fact.get("provenance_id"),
                    }
            except Exception as e:
                final_memory[path] = {"error": str(e)}

        stats = compute_stats_from_logs(
            write_log=_write_log,
            conflict_log=_conflict_log,
            agent_mode="real_llm",
            final_state_correct=None,
            attack_success_rate=None,
        )

        trial_result = {
            "topic": topic,
            "backend": "langchain",
            "agent_mode": "real_llm",
            "llm_available": True,
            "model": model,
            "scenario": scenario,
            "trial": trial_idx + 1,
            "run_id": run_id,
            "verification": True,
            "timing": {
                "research_seconds": round(research_time, 2),
                "verification_seconds": round(verification_time, 2),
                "synthesis_seconds": round(synthesis_time, 2),
                "total_seconds": round(trial_total, 2),
            },
            "write_log": list(_write_log),
            "conflict_log": list(_conflict_log),
            "final_memory": final_memory,
            "stats": stats,
        }

        all_trial_results.append(trial_result)
        print(f"  Trial {trial_idx + 1} completed in {trial_total:.2f}s")

    total_time = time.perf_counter() - total_start

    agg_stats = compute_stats_from_logs(
        write_log=_write_log,
        conflict_log=_conflict_log,
        agent_mode="real_llm",
        final_state_correct=None,
        attack_success_rate=None,
    )

    results = {
        "topic": topic,
        "backend": "langchain",
        "agent_mode": "real_llm",
        "llm_available": True,
        "model": model,
        "scenario": scenario,
        "trials": trials,
        "verification": True,
        "timing": {
            "total_seconds": round(total_time, 2),
        },
        "write_log": _write_log,
        "conflict_log": _conflict_log,
        "final_memory": final_memory,
        "stats": agg_stats,
        "trial_results": all_trial_results,
    }

    print("\n" + "=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    print(f"Total writes: {results['stats']['total_writes']}")
    print(f"Conflicts resolved: {results['stats']['conflict_resolved']}")
    print(f"Conflicts unresolved: {results['stats']['conflict_unresolved']}")
    print(f"Tool call failed: {results['stats']['tool_call_failed']}")
    print(f"Total time: {results['timing']['total_seconds']:.2f}s")

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults saved to: {save_path}")

    return results


# ---------------------------------------------------------------------------
# Ollama backend implementation (was experiments/real_agent_experiment_ollama.py)
# ---------------------------------------------------------------------------

def _make_ollama_agent(system_prompt: str, max_iterations: int = 12,
                       model: str = "llama3.1:8b") -> Any:
    """Create a LangChain agent with Ollama + native tool binding."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    llm = ChatOllama(model=model, temperature=0)
    try:
        llm = llm.bind_tools(_LC_TOOLS)
    except Exception:
        pass

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, _LC_TOOLS, prompt)
    return AgentExecutor(
        agent=agent,
        tools=_LC_TOOLS,
        verbose=True,
        max_iterations=max_iterations,
        early_stopping_method="force",
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


SCENARIO_REGISTRY: Dict[str, Dict[str, Any]] = {
    "basic": {"scenario_id": "basic", "version": "1.0", "domain": "science",
        "task": "Independently research and consolidate one scientific claim.",
        "agent_roles": ["research", "verification", "synthesis"],
        "shared_memory_paths": ["research.key_insight", "research.synthesis"],
        "execution_order": ["research_agent", "verification_agent", "synthesis_agent"],
        "allowed_tools": ["write_memory", "read_memory"], "expected_conflict_opportunities": 0,
        "evaluation_contract": "operational_only", "independent_ground_truth": False},
    "adversarial": {"scenario_id": "adversarial", "version": "1.0", "domain": "security",
        "task": "Challenge an existing claim and independently assess both positions.",
        "agent_roles": ["claimant", "skeptic", "adjudicator"],
        "shared_memory_paths": ["security.contested_claim"],
        "execution_order": ["research_agent", "verification_agent", "synthesis_agent"],
        "allowed_tools": ["write_memory", "read_memory"], "expected_conflict_opportunities": 1,
        "evaluation_contract": "operational_conflict_only", "independent_ground_truth": False},
    "colluding": {"scenario_id": "colluding", "version": "1.0", "domain": "governance",
        "task": "Two aligned agents propose a claim and an independent critic contests it.",
        "agent_roles": ["proposer", "ally", "independent_critic"],
        "shared_memory_paths": ["governance.contested_claim"],
        "execution_order": ["research_agent", "synthesis_agent", "verification_agent"],
        "allowed_tools": ["write_memory", "read_memory"], "expected_conflict_opportunities": 1,
        "evaluation_contract": "operational_conflict_only", "independent_ground_truth": False},
    "dynamic": {"scenario_id": "dynamic", "version": "1.0", "domain": "operations",
        "task": "Update a shared operational decision from differing perspectives.",
        "agent_roles": ["planner", "observer", "reviser"],
        "shared_memory_paths": ["operations.current_decision"],
        "execution_order": ["research_agent", "verification_agent", "synthesis_agent"],
        "allowed_tools": ["write_memory", "read_memory"], "expected_conflict_opportunities": 1,
        "evaluation_contract": "operational_conflict_only", "independent_ground_truth": False},
}


OLLAMA_RESEARCH_SYSTEM = """\
You are a Research Agent. agent_id = 'research_agent'.

Available tools:
- write_memory: Store research findings to shared memory
- read_memory: Retrieve research information from shared memory

Your task: Research the given topic and provide your analysis. You may use memory tools to record your findings, but you decide whether and what to write.
"""

OLLAMA_VERIFICATION_SYSTEM = """\
You are a Senior Verification Agent. agent_id = 'verification_agent'.

Available tools:
- write_memory: Store verification results to shared memory
- read_memory: Retrieve research information from shared memory

Your task: Verify and refine research findings. You may use memory tools to access current information and provide your verification.
"""

OLLAMA_SYNTHESIS_SYSTEM = """\
You are a Synthesis Agent. agent_id = 'synthesis_agent'.

Available tools:
- write_memory: Store synthesis results to shared memory
- read_memory: Retrieve all research information from shared memory

Your task: Synthesize the complete research picture into a coherent summary. You may use memory tools to access all relevant information and provide your synthesis.
"""


def run_ollama_agent(agent_id: str, system_prompt: str, topic: str,
                     model: str = "llama3.1:8b", max_iterations: int = 3) -> Dict[str, Any]:
    """Run a real agent with Ollama native tool calling."""
    print(f"\n--- Running {agent_id} ---")

    agent = _make_ollama_agent(system_prompt, max_iterations=max_iterations, model=model)
    prompt_text = f"Research {topic}"
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    try:
        result = agent.invoke({"input": prompt_text})
        elapsed = time.perf_counter() - t0
        raw_response = str(result.get("output", ""))
        structured_calls = []
        for action, observation in result.get("intermediate_steps", []):
            structured_calls.append({
                "id": getattr(action, "tool_call_id", None),
                "name": getattr(action, "tool", ""),
                "arguments": getattr(action, "tool_input", {}),
                "result": str(observation),
                "success": not str(observation).lower().startswith("failed"),
            })
        print(f"  {agent_id} completed in {elapsed:.2f}s")
        return {
            "agent_id": agent_id,
            "iterations_completed": 1,
            "tool_call_failed": 0,
            "result": result,
            "elapsed": elapsed,
            "invocation": {"agent_id": agent_id, "role": agent_id.removesuffix("_agent"),
                "model": model, "model_digest": os.getenv("OLLAMA_MODEL_DIGEST"),
                "started_at": started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
                "raw_response": raw_response,
                "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest() if raw_response else None,
                "structured_tool_calls": structured_calls, "retry_count": 0,
                "token_counts": result.get("usage_metadata"), "latency_seconds": elapsed,
                "error": None, "valid_lcm_tool_call": any(c["success"] and c["name"] in
                    ("write_memory", "read_memory") for c in structured_calls)},
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"  {agent_id} failed: {e}")
        return {
            "agent_id": agent_id,
            "iterations_completed": 0,
            "tool_call_failed": 1,
            "result": {"error": str(e)},
            "elapsed": elapsed,
            "invocation": {"agent_id": agent_id, "role": agent_id.removesuffix("_agent"),
                "model": model, "model_digest": os.getenv("OLLAMA_MODEL_DIGEST"),
                "started_at": started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
                "raw_response": "", "response_sha256": None, "structured_tool_calls": [],
                "retry_count": 0, "token_counts": None, "latency_seconds": elapsed,
                "error": str(e), "valid_lcm_tool_call": False},
        }


def run_ollama_agent(agent_id: str, system_prompt: str, topic: str,
                     model: str = "llama3.1:8b", max_iterations: int = 3) -> Dict[str, Any]:
    """Execute Ollama's native structured tool-call loop (no text parsing)."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    prompt_text = f"Research {topic}"
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_text}]
    tools = [{"type": "function", "function": {"name": "write_memory",
              "description": "Write one model-generated claim to the real shared LCM service.",
              "parameters": {"type": "object", "required": ["path", "value", "confidence"],
                  "properties": {"path": {"type": "string"}, "value": {"type": "string"},
                                 "confidence": {"type": "number"}}}}},
             {"type": "function", "function": {"name": "read_memory",
              "description": "Read one path from the real shared LCM service.",
              "parameters": {"type": "object", "required": ["path"],
                             "properties": {"path": {"type": "string"}}}}}]
    calls, raw_responses, prompt_tokens, completion_tokens = [], [], 0, 0
    try:
        for _ in range(max_iterations):
            response = requests.post(f"{ollama_url}/api/chat", json={"model": model,
                "messages": messages, "tools": tools, "stream": False,
                "options": {"temperature": 0}}, timeout=300)
            response.raise_for_status()
            payload = response.json()
            raw_responses.append(payload)
            prompt_tokens += int(payload.get("prompt_eval_count") or 0)
            completion_tokens += int(payload.get("eval_count") or 0)
            message = payload.get("message") or {}
            messages.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                break
            for call in tool_calls:
                function = call.get("function") or {}
                name, args = function.get("name"), dict(function.get("arguments") or {})
                try:
                    if name == "write_memory":
                        args["agent_id"] = agent_id
                        observation = write_memory.invoke(args)
                    elif name == "read_memory":
                        observation = read_memory.invoke(args)
                    else:
                        raise ValueError(f"Tool not allowed: {name}")
                    success = not str(observation).lower().startswith("failed")
                    error = None
                except Exception as exc:
                    observation, success, error = f"Failed: {exc}", False, str(exc)
                calls.append({"id": call.get("id"), "name": name, "arguments": args,
                              "result": str(observation), "success": success, "error": error})
                messages.append({"role": "tool", "content": str(observation)})
        raw = json.dumps(raw_responses, sort_keys=True, default=str)
        output = str((raw_responses[-1].get("message") or {}).get("content", "")) if raw_responses else ""
        invocation = {"agent_id": agent_id, "role": agent_id.removesuffix("_agent"),
            "model": model, "model_digest": os.getenv("OLLAMA_MODEL_DIGEST"),
            "started_at": started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
            "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
            "raw_response": raw, "response_sha256": hashlib.sha256(raw.encode()).hexdigest() if raw else None,
            "structured_tool_calls": calls, "retry_count": 0,
            "token_counts": {"prompt": prompt_tokens, "completion": completion_tokens},
            "latency_seconds": time.perf_counter() - started, "error": None,
            "valid_lcm_tool_call": any(c["success"] and c["name"] in ("write_memory", "read_memory") for c in calls)}
        return {"agent_id": agent_id, "iterations_completed": len(raw_responses),
                "tool_call_failed": sum(not c["success"] for c in calls),
                "result": {"output": output}, "elapsed": invocation["latency_seconds"],
                "invocation": invocation}
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {"agent_id": agent_id, "iterations_completed": len(raw_responses),
                "tool_call_failed": 1, "result": {"error": str(exc)}, "elapsed": elapsed,
                "invocation": {"agent_id": agent_id, "role": agent_id.removesuffix("_agent"),
                    "model": model, "model_digest": os.getenv("OLLAMA_MODEL_DIGEST"),
                    "started_at": started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
                    "prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
                    "raw_response": json.dumps(raw_responses, default=str), "response_sha256": None,
                    "structured_tool_calls": calls, "retry_count": 0, "token_counts": None,
                    "latency_seconds": elapsed, "error": str(exc), "valid_lcm_tool_call": False}}


def run_real_experiment(topic: str = "quantum computing applications in drug discovery",
                        save_path: str = "experiments/results/real_agent_ollama.json",
                        model: str = "llama3.1:8b",
                        scenario: str = "basic",
                        trials: int = 1) -> Dict[str, Any]:
    """Run the full three-agent experiment with real Ollama tool calling."""
    print("=" * 70)
    print(f"REAL MULTI-AGENT EXPERIMENT WITH OLLAMA")
    print(f"Model: {model} | Scenario: {scenario} | Trials: {trials}")
    print("=" * 70)
    print(f"Topic: {topic}")

    if scenario not in SCENARIO_REGISTRY:
        raise ValueError(f"Unknown scenario: {scenario}")
    contract = SCENARIO_REGISTRY[scenario]
    scenario_instruction = (f"\nScenario: {contract['task']} Use only these shared paths: "
                            f"{contract['shared_memory_paths']}. You must make at least one native tool call.")
    all_trial_results = []
    all_writes: List[Dict[str, Any]] = []
    all_conflicts: List[Dict[str, Any]] = []
    all_invocations: List[Dict[str, Any]] = []
    all_readbacks: List[Dict[str, Any]] = []
    experiment_started = datetime.now(timezone.utc).isoformat()
    total_start = time.perf_counter()

    for trial_idx in range(trials):
        global _current_session_id
        _reset_logs()
        trial_start = time.perf_counter()
        run_id = f"ollama-{scenario}-{trial_idx + 1}-{uuid4().hex}"
        _current_session_id = run_id
        trial_started = datetime.now(timezone.utc).isoformat()
        initial_trust = {a: _lcm.get_trust(a, domain=contract["domain"])
                         for a in contract["execution_order"]}

        print(f"\n--- Trial {trial_idx + 1}/{trials} (run_id={run_id}) ---")

        t0 = time.perf_counter()
        research_result = run_ollama_agent("research_agent", OLLAMA_RESEARCH_SYSTEM + scenario_instruction, topic, model=model, max_iterations=3)
        research_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        verification_result = run_ollama_agent("verification_agent", OLLAMA_VERIFICATION_SYSTEM + scenario_instruction, topic, model=model, max_iterations=3)
        verification_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        synthesis_result = run_ollama_agent("synthesis_agent", OLLAMA_SYNTHESIS_SYSTEM + scenario_instruction, topic, model=model, max_iterations=3)
        synthesis_time = time.perf_counter() - t0

        trial_total = time.perf_counter() - trial_start

        final_memory = {}
        final_state = {}
        memory_readbacks = []
        for path in contract["shared_memory_paths"]:
            try:
                context = _lcm.get_context(path)
                facts = context.get("facts", [])
                if facts:
                    fact = facts[0]
                    payload = fact.get("assertion_payload", {})
                    value = payload.get(path, str(payload))
                    verified_conf = fact.get("verified_confidence")
                    final_memory[path] = {
                        "value": value,
                        "agent_id": fact.get("agent_id"),
                        "confidence": fact.get("confidence_score"),
                        "verified_confidence": verified_conf,
                        "provenance_id": fact.get("provenance_id"),
                        "authority_score": fact.get("authority_score"),
                        "source_type": fact.get("source_type"),
                    }
                    memory_readbacks.append({"path": path, "success": True,
                        "transport": "http", "service_url": _lcm.base_url,
                        "server_provenance": {"provenance_id": fact.get("provenance_id"),
                            "verified_confidence": fact.get("verified_confidence"),
                            "authority_score": fact.get("authority_score"),
                            "source_type": fact.get("source_type")}})
                    final_state[path] = {
                        "value": value,
                        "agent_id": fact.get("agent_id"),
                        "verified_confidence": verified_conf,
                    }
            except Exception as e:
                final_memory[path] = {"error": str(e)}
                final_state[path] = {"error": str(e)}
                memory_readbacks.append({"path": path, "success": False, "error": str(e)})

        trust_scores = {}
        for agent_id in ["research_agent", "verification_agent", "synthesis_agent"]:
            try:
                trust_scores[agent_id] = _lcm.get_trust(agent_id)
            except Exception:
                trust_scores[agent_id] = {"trust_score": 0.5, "outcome_count": 0}

        total_tool_call_failed = (
            research_result.get("tool_call_failed", 0) +
            verification_result.get("tool_call_failed", 0) +
            synthesis_result.get("tool_call_failed", 0)
        )

        stats = compute_stats_from_logs(
            write_log=_write_log,
            conflict_log=_conflict_log,
            agent_mode="real_llm",
            final_state_correct=None,
            attack_success_rate=None,
        )
        stats["tool_call_failed"] = total_tool_call_failed

        trial_result = {
            "topic": topic,
            "backend": "ollama",
            "agent_mode": "real_llm",
            "llm_available": True,
            "model": model,
            "scenario": scenario,
            "trial": trial_idx + 1,
            "run_id": run_id,
            "started_at": trial_started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "initial_trust": initial_trust,
            "model_invocations": [research_result["invocation"], verification_result["invocation"], synthesis_result["invocation"]],
            "memory_readbacks": memory_readbacks,
            "verification": True,
            "timing": {
                "research_seconds": round(research_time, 2),
                "verification_seconds": round(verification_time, 2),
                "synthesis_seconds": round(synthesis_time, 2),
                "total_seconds": round(trial_total, 2),
            },
            "writes": [{"agent": w["agent_id"], "status": w["status"], "message": "Write operation"} for w in _write_log],
            "conflicts": [{"path": c["path"], "winner": c["winner"], "loser": c["loser"], "reason": c["reason"]} for c in _conflict_log],
            "trust_scores": trust_scores,
            "write_log": list(_write_log),
            "conflict_log": list(_conflict_log),
            "final_memory": final_memory,
            "final_state": final_state,
            "agent_results": {
                "research": research_result,
                "verification": verification_result,
                "synthesis": synthesis_result,
            },
            "stats": stats,
        }

        all_trial_results.append(trial_result)
        all_writes.extend(trial_result["write_log"])
        all_conflicts.extend(trial_result["conflict_log"])
        all_invocations.extend(trial_result["model_invocations"])
        all_readbacks.extend(memory_readbacks)
        print(f"  Trial {trial_idx + 1} completed in {trial_total:.2f}s")

    total_time = time.perf_counter() - total_start

    agg_stats = compute_stats_from_logs(
        write_log=all_writes,
        conflict_log=all_conflicts,
        agent_mode="real_llm",
        final_state_correct=None,
        attack_success_rate=None,
    )

    results = {
        "topic": topic,
        "backend": "ollama",
        "classification": "real_agent",
        "agent_mode": "real_llm",
        "llm_available": True,
        "model": model,
        "scenario": scenario,
        "trials": trials,
        "verification": True,
        "timing": {
            "total_seconds": round(total_time, 2),
        },
        "run_id": f"ollama-{scenario}-{uuid4().hex}",
        "started_at": experiment_started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": os.getenv("GIT_COMMIT") or _git_commit(),
        "configuration_hash": hashlib.sha256(json.dumps({"model": model, "scenario": contract,
            "trials": trials}, sort_keys=True).encode()).hexdigest(),
        "database_identity": os.getenv("LCM_SQLITE_PATH", "unreported"),
        "service_url": _lcm.base_url,
        "scenario_contract": contract,
        "initial_trust": all_trial_results[0]["initial_trust"] if all_trial_results else {},
        "model_invocations": all_invocations,
        "memory_readbacks": all_readbacks,
        "scripted_fallback_writes": False,
        "mock_data_used": False,
        "trust_initialized": False,
        "ground_truth_status": "not_available",
        "resolution_accuracy": None,
        "write_log": all_writes,
        "conflict_log": all_conflicts,
        "final_memory": final_memory,
        "final_state": final_state,
        "agent_results": {
            "research": research_result,
            "verification": verification_result,
            "synthesis": synthesis_result,
        },
        "stats": agg_stats,
        "trial_results": all_trial_results,
    }
    canonical = json.dumps(results, sort_keys=True, separators=(",", ":"), default=str)
    results["artifact_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    print("\n" + "=" * 70)
    print("EXPERIMENT SUMMARY")
    print("=" * 70)
    print(f"Total writes: {results['stats']['total_writes']}")
    print(f"Gate rejected: {results['stats']['gate_rejected']}")
    print(f"Conflict resolved: {results['stats']['conflict_resolved']}")
    print(f"Conflict unresolved: {results['stats']['conflict_unresolved']}")
    print(f"Tool call failed: {results['stats']['tool_call_failed']}")
    print(f"Total time: {results['timing']['total_seconds']:.2f}s")

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults saved to: {save_path}")

        print("\nGenerating multi-agent visualizations...")
        try:
            from visualization.multi_agent_plots import generate_multi_agent_dashboard
            plots_dir = "experiments/results/plots"
            plots = generate_multi_agent_dashboard(results, plots_dir)
            print(f"Generated {len(plots)} visualizations in {plots_dir}")
        except Exception as e:
            print(f"  [SKIP] Visualization generation failed: {e}")

    return results


# ---------------------------------------------------------------------------
# Ollama Backend - Real LLM Agents
# ---------------------------------------------------------------------------

def run_ollama_backend(scenario: str, trials: int, verification: bool, save_path: Optional[str]) -> Dict[str, Any]:
    """Run experiment with Ollama-based real LLM agents (fail-closed)."""
    print(f"\n=== Running OLLAMA backend: {scenario} ({trials} trials) ===")
    print("Verification:", "enabled" if verification else "disabled")
    print("Using model: llama3.1:8b")

    if verification:
        os.environ["LCM_VERIFICATION"] = "1"
    else:
        os.environ["LCM_VERIFICATION"] = "0"

    default_topic = "quantum computing applications in drug discovery"

    try:
        results = run_real_experiment(
            topic=default_topic,
            save_path=save_path,
            model="llama3.1:8b",
            scenario=scenario,
            trials=trials,
        )
    except Exception as e:
        print(f"[ERROR] Ollama experiment failed: {e}")
        print("Ensure Ollama is running with llama3.1:8b model available")
        sys.exit(1)

    if results.get("agent_mode") != "real_llm":
        print(f"[ERROR] Expected agent_mode='real_llm' but got '{results.get('agent_mode')}'")
        print("Real backends must use actual LLM, not simulation")
        sys.exit(1)

    results["backend"] = "ollama"
    results["model"] = "llama3.1:8b"
    results["llm_available"] = True
    results["scenario"] = scenario
    results["trials"] = trials
    results["verification"] = verification

    return results


# ---------------------------------------------------------------------------
# LangChain Backend - LangChain-based Agents
# ---------------------------------------------------------------------------

def run_langchain_backend(scenario: str, trials: int, verification: bool, save_path: Optional[str]) -> Dict[str, Any]:
    """Run experiment with LangChain-based agents (fail-closed)."""
    print(f"\n=== Running LANGCHAIN backend: {scenario} ({trials} trials) ===")
    print("Verification:", "enabled" if verification else "disabled")
    print("Using model: llama3.1:8b")

    if verification:
        os.environ["LCM_VERIFICATION"] = "1"
    else:
        os.environ["LCM_VERIFICATION"] = "0"

    default_topic = "quantum computing applications in drug discovery"

    try:
        results = run_experiment(
            topic=default_topic,
            save_path=save_path,
            model="llama3.1:8b",
            scenario=scenario,
            trials=trials,
        )
    except Exception as e:
        print(f"[ERROR] LangChain experiment failed: {e}")
        print("Ensure Ollama is running with llama3.1:8b model available")
        sys.exit(1)

    if results.get("agent_mode") != "real_llm":
        print(f"[ERROR] Expected agent_mode='real_llm' but got '{results.get('agent_mode')}'")
        print("Real backends must use actual LLM, not simulation")
        sys.exit(1)

    if "backend" not in results:
        results["backend"] = "langchain"
    if "model" not in results:
        results["model"] = "llama3.1:8b"
    if "llm_available" not in results:
        results["llm_available"] = True
    results["scenario"] = scenario
    results["trials"] = trials
    results["verification"] = verification

    return results


# ---------------------------------------------------------------------------
# Scenario Router
# ---------------------------------------------------------------------------

SCENARIO_HANDLERS = {
    "adversarial": {
        "ollama": lambda: run_ollama_backend("adversarial", 1, True, None),
        "langchain": lambda: run_langchain_backend("adversarial", 1, True, None),
    },
    "colluding": {
        "ollama": lambda: run_ollama_backend("colluding", 1, True, None),
        "langchain": lambda: run_langchain_backend("colluding", 1, True, None),
    },
    "dynamic": {
        "ollama": lambda: run_ollama_backend("dynamic", 1, True, None),
        "langchain": lambda: run_langchain_backend("dynamic", 1, True, None),
    },
    "basic": {
        "ollama": lambda: run_ollama_backend("basic", 1, True, None),
        "langchain": lambda: run_langchain_backend("basic", 1, True, None),
    },
}


def run_scenario(scenario: str, backend: str, trials: int, verification: bool, save_path: Optional[str]) -> Dict[str, Any]:
    """Run a specific scenario with the specified backend."""

    backend_handlers = {
        "ollama": lambda: run_ollama_backend(scenario, trials, verification, save_path),
        "langchain": lambda: run_langchain_backend(scenario, trials, verification, save_path),
    }

    handler = backend_handlers.get(backend)
    if not handler:
        raise ValueError(f"Unknown backend: {backend}. Available: {list(backend_handlers.keys())}")

    return handler()


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Unified LCM Real Agent Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python experiments/run_real_agent_experiment.py --backend ollama --scenario colluding --verification off
  python experiments/run_real_agent_experiment.py --backend langchain --scenario dynamic --save results.json
        """
    )

    parser.add_argument(
        "--backend",
        choices=["ollama", "langchain"],
        default="ollama",
        help="Agent backend to use (default: ollama)"
    )

    parser.add_argument(
        "--scenario",
        choices=["adversarial", "colluding", "dynamic", "basic"],
        default="basic",
        help="Scenario type to run (default: basic)"
    )

    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Number of trials to run (default: 1)"
    )

    parser.add_argument(
        "--verification",
        choices=["on", "off"],
        default="on",
        help="Enable evidence verification (default: on)"
    )

    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to save results JSON (default: experiments/results/unified_run.json)"
    )

    args = parser.parse_args()

    if args.save is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.save = f"experiments/results/unified_{args.backend}_{args.scenario}_{timestamp}.json"

    verification_enabled = args.verification == "on"

    print("=" * 70)
    print("UNIFIED LCM EXPERIMENT RUNNER")
    print("=" * 70)
    print(f"Backend:      {args.backend}")
    print(f"Scenario:     {args.scenario}")
    print(f"Trials:       {args.trials}")
    print(f"Verification: {args.verification}")
    print(f"Save path:    {args.save}")
    print("=" * 70)

    start_time = time.perf_counter()

    try:
        results = run_scenario(
            scenario=args.scenario,
            backend=args.backend,
            trials=args.trials,
            verification=verification_enabled,
            save_path=args.save,
        )

        total_time = time.perf_counter() - start_time
        results["total_runtime_seconds"] = total_time

        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        print("\n" + "=" * 70)
        print("EXPERIMENT COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print(f"Total runtime: {total_time:.2f}s")
        print(f"Results saved to: {args.save}")

        if "stats" in results:
            stats = results["stats"]
            print("\nSummary Statistics:")
            for key, value in stats.items():
                if isinstance(value, float):
                    print(f"  {key}: {value:.3f}")
                else:
                    print(f"  {key}: {value}")

        return 0

    except Exception as e:
        print(f"\n[ERROR] Experiment failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
