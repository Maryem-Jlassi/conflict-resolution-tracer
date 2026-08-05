"""
Release verification gate (Phase 15).

A single entry point that verifies every claim shipped across Phases 1–14 and
reports honest evidence. Results are grouped into SIX verdict categories:

=======================  ==========================================================
Category                 What it checks
=======================  ==========================================================
core_invariants          In-process security/correctness invariants (Phases 1–12)
pytest_suite             The full pytest suite, parsed from real pytest output
real_component_acceptance Live uvicorn subprocess against a temp-file SQLite DB,
                         real Ed25519 keypair, HTTP write/read, key rotation,
                         revocation, tamper/replay/temporal rejection and
                         restart persistence
real_agent_evaluation    Ollama probe (honest SKIP if unavailable) + figure
                         cache/manifest integrity + real-agent honesty rules
documentation_consistency Regenerated docs vs committed docs + README (Phase 14)
artifact_validation      Pinned benchmark artifacts: manifest hashes + the frozen
                         held-out numbers (16 clear, 11 correct, 0 wrong, 5
                         abstentions; 3/3 ambiguous unresolved)
=======================  ==========================================================

Nothing is fabricated: every check executes the real ``lcm_core`` /
``lcm_service`` / ``lcm_client`` code and reports the observed result honestly.
Real-component acceptance uses a real server subprocess, a real temp-file SQLite
database and a throwaway Ed25519 keypair (never the dev key). If an optional
prerequisite is unavailable (e.g. Ollama), the check is SKIPPED with the exact
reason instead of being faked.

Verdicts (per category and overall): PASS, PASS_WITH_SKIPS, INCOMPLETE, FAIL.

Usage::

    python tools/verify_release.py                    # full gate (all six categories)
    python tools/verify_release.py --no-suite         # skip the pytest suite
    python tools/verify_release.py --skip-acceptance  # skip the server subprocess acceptance
    python tools/verify_release.py --skip-real-agent  # skip the Ollama / figures category
    python tools/verify_release.py --out out.json     # custom report path
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# The crypto layer signs with the development Ed25519 provider key and verifies
# against the SAME key (see lcm_core.crypto). That dev fallback requires an
# explicit opt-in (fail-closed by default). This tool is exactly the
# self-consistent consumer the dev key exists for, so enable it unless a real
# provider key is configured — mirroring tests/conftest.py.
if not os.environ.get("LCM_EVIDENCE_PUBLIC_KEY"):
    os.environ.setdefault("LCM_ALLOW_DEV_EVIDENCE_KEY", "1")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — non-interactive stdout may not support reconfigure
    pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_LAST_PYTEST_RUN: Dict[str, Any] = {}
_GATE_SUBPROCESSES: List[Dict[str, Any]] = []
_ALLOCATED_PORTS: List[int] = []

from lcm_core.canonical import canonical_json  # noqa: E402
from lcm_core.confidence_engine import EvidenceRecord, EvidenceType  # noqa: E402
from lcm_core.crypto import (  # noqa: E402
    reset_replay_guard,
    sign_evidence_message,
    sign_evidence_message_with_key,
    verify_evidence_signature_crypto,
)
from lcm_core.provenance import (  # noqa: E402
    validate_and_stamp,
    verify_evidence_signature,
)
from lcm_core.user_input_policy import (  # noqa: E402
    UserInputPolicy,
    set_user_input_policy,
)
from lcm_core.schema import StampedUMF, ProvenanceInfo  # noqa: E402
from lcm_core.conflict import ConflictResolutionEngine  # noqa: E402
from lcm_core.trust_manager import TrustManager  # noqa: E402
from lcm_core.metrics import (  # noqa: E402
    compute_metrics_snapshot,
    get_metrics_registry,
    record_write_status,
    reset_metrics_registry,
)

REF_TIME = datetime(2026, 7, 14, 10, 0, 0)

# The six verdict categories of the gate.
CATEGORIES = [
    "core_invariants",
    "pytest_suite",
    "real_component_acceptance",
    "real_agent_evaluation",
    "documentation_consistency",
    "artifact_validation",
]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    tag: str                      # verification | experiment | diagnostic
    detail: str
    category: str
    skipped: bool = False
    skip_reason: str = ""
    blocked: bool = False         # environmental blocker (category → INCOMPLETE)


def _result(name: str, passed: bool, category: str, detail: str,
            tag: str = "verification") -> CheckResult:
    return CheckResult(name, passed, tag, detail, category)


def _skipped(name: str, category: str, reason: str) -> CheckResult:
    return CheckResult(name, True, "verification", f"SKIP: {reason}",
                       category, skipped=True, skip_reason=reason)


def _blocked(name: str, category: str, reason: str) -> CheckResult:
    return CheckResult(name, True, "verification", f"BLOCKED: {reason}",
                       category, skipped=True, skip_reason=reason, blocked=True)


# ---------------------------------------------------------------------------
# Category verdicts
# ---------------------------------------------------------------------------

def category_verdict(checks: List[CheckResult]) -> str:
    """PASS / PASS_WITH_SKIPS / INCOMPLETE / FAIL for one category."""
    if any(not c.skipped and not c.passed for c in checks):
        return "FAIL"
    if any(c.blocked for c in checks):
        return "INCOMPLETE"
    if any(c.skipped for c in checks):
        return "PASS_WITH_SKIPS"
    return "PASS"


def overall_verdict(category_verdicts: Dict[str, str]) -> str:
    if any(v == "FAIL" for v in category_verdicts.values()):
        return "FAIL"
    if any(v == "INCOMPLETE" for v in category_verdicts.values()):
        return "INCOMPLETE"
    if any(v == "PASS_WITH_SKIPS" for v in category_verdicts.values()):
        return "PASS_WITH_SKIPS"
    return "PASS"


# ===========================================================================
# Category 1 — core_invariants (in-process, deterministic, no mocking)
# ===========================================================================

def _raw_packet(agent: str, value: str, confidence: float = 0.8) -> Dict[str, Any]:
    return {
        "agent_id": agent,
        "session_id": "verify_release",
        "timestamp": REF_TIME,
        "confidence_score": confidence,
        "assertion_payload": {"path": value},
    }


def check_confidence_semantics() -> CheckResult:
    """Verified evidence elevates authority; unverified degrades; agent_claim bypasses."""
    # Direct crypto gate: signature bound to a specific claim hash.
    sig = sign_evidence_message(EvidenceType.DATABASE, "db", assertion_hash="h")
    verified = verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db", sig, assertion_hash="h", reference_time=REF_TIME
    )
    garbage_rejected = not verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db", "not-a-signature", reference_time=REF_TIME
    )
    agent_claim_bypass = verify_evidence_signature(
        EvidenceType.AGENT_CLAIM, "anything", None
    ) is True
    # Provenance stamping path: a plain signed database record keeps authority.
    ev = EvidenceRecord(evidence_type=EvidenceType.DATABASE, source_id="db", relevance_score=1.0)
    stamped = validate_and_stamp(
        _raw_packet("agent-a", "v"),
        evidence_records=[ev],
        evidence_signature=sign_evidence_message(EvidenceType.DATABASE, "db"),
    )
    passed = (
        verified
        and garbage_rejected
        and agent_claim_bypass
        and stamped.provenance_info.authority_score == 0.9
    )
    return _result(
        "confidence_semantics", passed, "core_invariants",
        f"verified={verified} garbage_rejected={garbage_rejected} "
        f"agent_claim_bypass={agent_claim_bypass} "
        f"signed_database_authority={stamped.provenance_info.authority_score}",
    )


def check_temporal_enforcement() -> CheckResult:
    """Expired / not-yet-valid evidence is rejected; a valid window passes."""
    from lcm_core.crypto import evidence_temporal_status
    issued = "2026-07-14T08:00:00"
    expires = "2026-07-14T09:00:00"
    expired_sig = sign_evidence_message(
        EvidenceType.DATABASE, "db", issued_at=issued, expires_at=expires
    )
    expired_rejected = not verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db", expired_sig,
        issued_at=issued, expires_at=expires, reference_time=REF_TIME,
    )
    v_issued = "2026-07-14T09:00:00"
    v_expires = "2026-07-14T11:00:00"
    valid_sig = sign_evidence_message(
        EvidenceType.DATABASE, "db", issued_at=v_issued, expires_at=v_expires
    )
    valid_accepted = verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db", valid_sig,
        issued_at=v_issued, expires_at=v_expires, reference_time=REF_TIME,
    )
    future_rejected = evidence_temporal_status(
        "2026-07-14T11:00:00", None, reference_time=REF_TIME
    ) == "not_yet_valid"
    passed = expired_rejected and valid_accepted and future_rejected
    return _result(
        "temporal_enforcement", passed, "core_invariants",
        f"expired_rejected={expired_rejected} valid_accepted={valid_accepted} "
        f"future_rejected={future_rejected}",
    )


def check_replay_protection() -> CheckResult:
    """A consumed nonce is rejected on reuse (Phase 5)."""
    reset_replay_guard()
    try:
        nonce = "verify-release-nonce"
        issued, expires = "2026-07-14T09:00:00", "2026-07-14T11:00:00"
        sig = sign_evidence_message(
            EvidenceType.DATABASE, "db", assertion_hash="h",
            nonce=nonce, issued_at=issued, expires_at=expires,
        )
        first = verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig, assertion_hash="h",
            nonce=nonce, issued_at=issued, expires_at=expires,
            reference_time=REF_TIME,
        )
        second = verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db", sig, assertion_hash="h",
            nonce=nonce, issued_at=issued, expires_at=expires,
            reference_time=REF_TIME,
        )
        passed = first is True and second is False
        return _result(
            "replay_protection", passed, "core_invariants",
            f"first_accepted={first} second_rejected={not second}",
        )
    finally:
        reset_replay_guard()


def check_provider_key_lifecycle() -> CheckResult:
    """Sign/verify round-trip holds; a message bound to another claim fails (Phase 6)."""
    sig = sign_evidence_message(
        EvidenceType.DATABASE, "db://orders",
        assertion_hash="claim-1", provider_id="db-provider", key_id="k1",
    )
    ok = verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db://orders", sig,
        assertion_hash="claim-1", provider_id="db-provider", key_id="k1",
        reference_time=REF_TIME,
    )
    tampered = verify_evidence_signature_crypto(
        EvidenceType.DATABASE, "db://orders", sig,
        assertion_hash="claim-2", provider_id="db-provider", key_id="k1",
        reference_time=REF_TIME,
    )
    passed = ok is True and tampered is False
    return _result(
        "provider_key_lifecycle", passed, "core_invariants",
        f"round_trip={ok} cross_claim_rejected={not tampered}",
    )


def check_canonical_hashing() -> CheckResult:
    """Canonical JSON is order-independent and content-sensitive (Phase 7)."""
    a = canonical_json({"b": 1, "a": {"y": 2, "x": [3, 1, 2]}})
    b = canonical_json({"a": {"x": [3, 1, 2], "y": 2}, "b": 1})
    c = canonical_json({"b": 1, "a": {"x": [3, 1, 3], "y": 2}})
    passed = (a == b) and (a != c)
    return _result(
        "canonical_hashing", passed, "core_invariants",
        f"order_independent={a == b} content_sensitive={a != c}",
    )


def check_provenance_lineage() -> CheckResult:
    """Lineage nodes are tamper-evident: any payload change alters the hash (Phase 8)."""
    from lcm_core.lineage import node_from_stamped
    stamped = validate_and_stamp(_raw_packet("agent-a", "original"))
    node = node_from_stamped(stamped, path="path")
    node2 = node_from_stamped(
        validate_and_stamp(_raw_packet("agent-a", "tampered")), path="path"
    )
    node3 = node_from_stamped(stamped, path="other")  # same content, different path
    passed = (
        node.content_hash != node2.content_hash
        and node.content_hash == node3.content_hash
    )
    return _result(
        "provenance_lineage", passed, "core_invariants",
        f"tamper_detected={node.content_hash != node2.content_hash} "
        f"content_stable_across_paths={node.content_hash == node3.content_hash}",
    )


def check_user_input_policy() -> CheckResult:
    """Default policy: signed user_input keeps authority, unsigned degrades (Phase 9)."""
    set_user_input_policy(UserInputPolicy())  # default: attestation required
    try:
        ev = EvidenceRecord(
            evidence_type=EvidenceType.USER_INPUT, source_id="user://x", relevance_score=1.0
        )
        signed = validate_and_stamp(
            _raw_packet("agent-a", "v"),
            evidence_records=[ev],
            evidence_signature=sign_evidence_message(EvidenceType.USER_INPUT, "user://x"),
        )
        unsigned = validate_and_stamp(_raw_packet("agent-a", "v"), evidence_records=[ev])
        passed = (
            signed.provenance_info.source_type == "user_input"
            and signed.provenance_info.authority_score == 1.0
            and unsigned.provenance_info.authority_score == 0.1
        )
        return _result(
            "user_input_policy", passed, "core_invariants",
            f"signed_source={signed.provenance_info.source_type} "
            f"signed_authority={signed.provenance_info.authority_score} "
            f"unsigned_authority={unsigned.provenance_info.authority_score}",
        )
    finally:
        set_user_input_policy(UserInputPolicy())


def check_metrics_telemetry() -> CheckResult:
    """Write outcomes are recorded and derived rates compute (Phase 12)."""
    reset_metrics_registry()
    record_write_status("committed")
    record_write_status("committed")
    record_write_status("rejected")
    get_metrics_registry().observe("pipeline.latency_ms", 1.5)
    snap = compute_metrics_snapshot(get_metrics_registry())
    counts = snap["counters"]
    hist = snap["histograms"]["pipeline.latency_ms"]
    passed = (
        counts.get("writes.total") == 3.0
        and counts.get("writes.status.committed") == 2.0
        and snap["rates"]["gate_rejection_rate"] == round(1 / 3, 6)
        and hist["count"] == 1
        and hist["max_ms"] == 1.5
    )
    return _result(
        "metrics_telemetry", passed, "core_invariants",
        f"writes.total={counts.get('writes.total')} "
        f"gate_rejection_rate={snap['rates']['gate_rejection_rate']} "
        f"latency_histogram_count={hist['count']}",
        tag="diagnostic",
    )


def _umf(agent: str, authority: float, confidence: float = 0.8) -> StampedUMF:
    return StampedUMF(
        agent_id=agent,
        session_id="verify_release",
        timestamp=REF_TIME,
        confidence_score=confidence,
        assertion_payload={"path": agent},
        provenance_id=f"pid-{agent}",
        ingested_at=REF_TIME,
        provenance_info=ProvenanceInfo(
            source_type="database" if authority > 0.3 else "agent_claim",
            authority_score=authority,
            verified_confidence=confidence,
        ),
    )


def check_conflict_resolution() -> CheckResult:
    """Higher-authority evidence wins a tie on confidence/freshness (Phase 3)."""
    engine = ConflictResolutionEngine(uncertainty_threshold=0.05)
    existing = _umf("low-authority", authority=0.3, confidence=0.8)
    incoming = _umf("high-authority", authority=0.9, confidence=0.8)
    result = engine.resolve_conflict(
        existing=existing,
        incoming=incoming,
        trust_table={},
        domain="_global",
        trust_manager=TrustManager(),
    )
    passed = (not result.unresolved) and result.winner.agent_id == "high-authority"
    return _result(
        "conflict_resolution", passed, "core_invariants",
        f"unresolved={result.unresolved} winner={result.winner.agent_id} "
        f"psi_delta={round(abs(result.psi_winner - result.psi_loser), 4)}",
    )


def check_trust_uncertainty() -> CheckResult:
    """Correct outcomes raise trust, incorrect outcomes lower it (uncertainty-aware)."""
    tm = TrustManager()
    t0 = tm.get_trust("agent-t", "_global")
    tm.record_outcome("agent-t", correct=True, domain="_global")
    t1 = tm.get_trust("agent-t", "_global")
    tm.record_outcome("agent-t", correct=False, domain="_global")
    t2 = tm.get_trust("agent-t", "_global")
    passed = t1 > t0 and t2 < t1
    return _result(
        "trust_uncertainty", passed, "core_invariants",
        f"cold_start={round(t0, 4)} after_correct={round(t1, 4)} "
        f"after_incorrect={round(t2, 4)}",
        tag="diagnostic",
    )


INVARIANT_CHECKS: List[Any] = [
    check_confidence_semantics,
    check_temporal_enforcement,
    check_replay_protection,
    check_provider_key_lifecycle,
    check_canonical_hashing,
    check_provenance_lineage,
    check_user_input_policy,
    check_metrics_telemetry,
    check_conflict_resolution,
    check_trust_uncertainty,
]


def run_invariant_checks() -> List[CheckResult]:
    """Execute every invariant check against the real lcm_core code."""
    return [check() for check in INVARIANT_CHECKS]


# ===========================================================================
# Category 2 — pytest_suite
# ===========================================================================

_SUMMARY_TOKEN = re.compile(r"(?<!\d)(\d+)\s+(passed|skipped|failed|errors?)\b")


def _parse_pytest_summary(output: str) -> Dict[str, int]:
    """Extract passed/skipped/failed/error counts from pytest's summary line.

    pytest reorders the summary by severity (e.g. ``1 failed, 2 passed in
    0.3s`` vs ``3 passed, 1 skipped in 0.2s``), so each metric is extracted
    independently rather than assuming a fixed field order.
    """
    counts: Dict[str, int] = {}
    for m in _SUMMARY_TOKEN.finditer(output):
        count = int(m.group(1))
        label = "error" if m.group(2).startswith("error") else m.group(2)
        counts[label] = counts.get(label, 0) + count
    return counts


def run_pytest_suite(suite_dir: str, python: Optional[str] = None) -> Dict[str, Any]:
    """Run ``pytest`` over ``suite_dir`` and report the parsed outcome.

    Returns ``{"status": "ok"|"failed"|"skipped", ...}``. A non-zero exit code,
    zero collected tests, or an unparseable summary all count as failures —
    nothing is inferred optimistically.
    """
    global _LAST_PYTEST_RUN
    exe = python or sys.executable
    suite_path = Path(suite_dir)
    isolated_external_suite = suite_path.is_absolute() and _ROOT not in suite_path.parents
    pytest_target = "." if isolated_external_suite else suite_dir
    cmd = [exe, "-m", "pytest", pytest_target, "-q", "--tb=short", "-p", "no:cacheprovider"]
    started_at = datetime.utcnow().isoformat()
    cwd = str(suite_path) if isolated_external_suite else str(_ROOT)
    env_before = dict(os.environ)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env_before)
    except OSError as exc:
        result = {"status": "skipped", "exit_code": -1, "detail": f"pytest unavailable: {exc}"}
        _LAST_PYTEST_RUN = {**result, "command": cmd, "working_directory": cwd,
                            "started_at": started_at, "completed_at": datetime.utcnow().isoformat(),
                            "stdout": "", "stderr": str(exc), "parsed_summary": {},
                            "failure_node_ids": [], "environment_diff": {}}
        return result

    output = proc.stdout + proc.stderr
    counts = _parse_pytest_summary(output)
    failure_ids = sorted(set(re.findall(r"(?:FAILED|ERROR)\s+([^\s]+)", output)))
    evidence = {
        "command": cmd,
        "working_directory": cwd,
        "started_at": started_at,
        "completed_at": datetime.utcnow().isoformat(),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "parsed_summary": counts,
        "failure_node_ids": failure_ids,
        "environment_diff": {},
        "temporary_directory_paths": [env_before.get("TEMP"), env_before.get("TMP")],
    }
    passed = counts.get("passed", 0)
    skipped = counts.get("skipped", 0)
    failed = counts.get("failed", 0)
    error = counts.get("error", 0)
    total_failures = failed + error

    if proc.returncode == 5:
        result = {
            "status": "failed",
            "exit_code": proc.returncode,
            "passed": 0, "skipped": 0, "failed": 0,
            "detail": "pytest exited 5: no tests collected",
        }
        _LAST_PYTEST_RUN = {**evidence, **result}
        return result
    if not counts and proc.returncode != 0:
        result = {
            "status": "failed",
            "exit_code": proc.returncode,
            "passed": 0, "skipped": 0, "failed": 0,
            "detail": f"could not parse pytest summary: {output[-500:]}",
        }
        _LAST_PYTEST_RUN = {**evidence, **result}
        return result

    status = "ok" if (proc.returncode == 0 and total_failures == 0) else "failed"
    result = {
        "status": status,
        "exit_code": proc.returncode,
        "passed": passed,
        "skipped": skipped,
        "failed": total_failures,
        "detail": f"{passed} passed, {skipped} skipped, {total_failures} failed",
    }
    _LAST_PYTEST_RUN = {**evidence, **result}
    return result


def pytest_suite_checks(suite_dir: str, python: Optional[str] = None) -> List[CheckResult]:
    """Run pytest once and produce a single category check."""
    if not importlib.util.find_spec("pytest"):
        return [_blocked("pytest.run", "pytest_suite",
                         "pytest is not installed; the test suite cannot run")]
    suite = run_pytest_suite(suite_dir, python=python)
    if suite["status"] == "ok":
        return [_result("pytest.run", True, "pytest_suite", suite["detail"])]
    if suite["status"] == "skipped":
        return [_blocked("pytest.run", "pytest_suite", suite["detail"])]
    return [_result("pytest.run", False, "pytest_suite", suite["detail"])]


# ===========================================================================
# Category 3 — real_component_acceptance
# ===========================================================================

def _uvicorn_available() -> bool:
    return importlib.util.find_spec("uvicorn") is not None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(client: "LCMClient", timeout_s: float = 30.0) -> Tuple[bool, str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            return True, str(client.health_check())
        except Exception as exc:  # noqa: BLE001 — server may still be starting
            time.sleep(0.25)
    return False, f"no response on {client.base_url} within {timeout_s:.0f}s"


def _server_env(db_path: Optional[str]) -> Dict[str, str]:
    """Environment for the server subprocess.

    The real-key acceptance must NOT inherit the dev-key fallback or any
    LCM_EVIDENCE_PUBLIC_KEY: evidence must verify against the provider keys we
    seed into the SQLite registry, or fail closed. Setting LCM_SQLITE_PATH
    points the server at the temp-file DB.
    """
    env = dict(os.environ)
    env.pop("LCM_ALLOW_DEV_EVIDENCE_KEY", None)
    env.pop("LCM_EVIDENCE_PUBLIC_KEY", None)
    if db_path is not None:
        env["LCM_SQLITE_PATH"] = db_path
    else:
        env.pop("LCM_SQLITE_PATH", None)
    return env


@contextmanager
def _running_server(db_path: Optional[str],
                    env_extra: Optional[Dict[str, str]] = None) -> Iterator[Dict[str, Any]]:
    """Boot a real uvicorn subprocess and yield {client, port, proc, base_url}."""
    port = _free_port()
    _ALLOCATED_PORTS.append(port)
    env = _server_env(db_path)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lcm_service.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    from lcm_client.client import LCMClient
    base = f"http://127.0.0.1:{port}"
    proc_record = {"pid": proc.pid, "port": port, "base_url": base,
                   "database_path": db_path, "started_at": datetime.utcnow().isoformat(),
                   "terminated_normally": False, "forced_termination": False,
                   "exit_code": None, "pipes_closed": False}
    _GATE_SUBPROCESSES.append(proc_record)
    client = LCMClient(base_url=base, timeout=10)
    try:
        yield {"client": client, "port": port, "proc": proc, "base_url": base}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
            proc_record["terminated_normally"] = True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc_record["forced_termination"] = True
            proc.wait(timeout=10)
        proc.communicate()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        proc_record["exit_code"] = proc.returncode
        proc_record["completed_at"] = datetime.utcnow().isoformat()
        proc_record["pipes_closed"] = bool(
            (proc.stdout is None or proc.stdout.closed) and
            (proc.stderr is None or proc.stderr.closed)
        )


def _sign(priv, evidence_type: EvidenceType, source: str, provider_id: str, key_id: str,
          issued_at: str, expires_at: str, nonce: Optional[str]) -> str:
    return sign_evidence_message_with_key(
        priv, evidence_type, source,
        provider_id=provider_id, key_id=key_id,
        issued_at=issued_at, expires_at=expires_at, nonce=nonce,
    )


def _write_signed(client, agent: str, payload: Dict[str, Any], source: str,
                  provider_id: str, key_id: str, sig: str,
                  issued_at: str, expires_at: str, nonce: Optional[str]) -> Dict[str, Any]:
    return client.write(
        agent_id=agent, session_id=f"accept-{agent}", confidence_score=0.8,
        assertion_payload=payload,
        evidence_records=[{
            "type": "database", "source": source, "relevance": 1.0,
            "issued_at": issued_at, "expires_at": expires_at,
            "nonce": nonce, "provider_id": provider_id, "key_id": key_id,
        }],
        evidence_signature=sig,
    )


def _find_authority(client, path_prefix: str, expected_value: Any) -> Optional[float]:
    """Read /context and return the authority_score of the matching fact."""
    ctx = client.get_context(path_prefix)
    for fact in ctx.get("facts", []):
        payload = fact.get("assertion_payload") or {}
        if expected_value in payload.values():
            return fact.get("authority_score")
    return None


def run_real_component_acceptance() -> List[CheckResult]:
    """Live server subprocess + temp-file SQLite + real Ed25519 keypair.

    Everything runs against a throwaway temp directory; nothing is mocked and
    no synthetic results are recorded. If uvicorn is not installed the whole
    category is BLOCKED (honest reason).
    """
    from lcm_service.storage import SQLiteProviderRegistry, SQLiteStorage

    if not _uvicorn_available():
        return [_blocked("acceptance.uvicorn", "real_component_acceptance",
                         "uvicorn is not installed; the live-server acceptance cannot run")]

    checks: List[CheckResult] = []
    evidence: Dict[str, Any] = {}

    def _record(name: str, passed: bool, detail: str) -> None:
        checks.append(_result(name, passed, "real_component_acceptance", detail))

    with tempfile.TemporaryDirectory(prefix="lcm_accept_") as tmp:
        db = os.path.join(tmp, "lcm_accept.db")

        # Throwaway real Ed25519 keypair (NOT the dev key).
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        k1_priv = Ed25519PrivateKey.generate()
        k1_pub = k1_priv.public_key().public_bytes_raw()
        k2_priv = Ed25519PrivateKey.generate()
        k2_pub = k2_priv.public_key().public_bytes_raw()

        # Seed provider key k1 into the temp-file SQLite registry (pre-restart).
        SQLiteProviderRegistry(SQLiteStorage(db)).register_provider("labs", "key-1", k1_pub)

        now = datetime.utcnow()
        valid_issued = (now - timedelta(minutes=5)).isoformat()
        valid_expires = (now + timedelta(hours=1)).isoformat()
        expired_issued = (now - timedelta(hours=2)).isoformat()
        expired_expires = (now - timedelta(hours=1)).isoformat()

        # ---- Phase A: server #1 -------------------------------------------
        boot_err: Optional[str] = None
        with _running_server(db) as srv:
            client = srv["client"]
            ok, detail = _wait_health(client)
            _record("acceptance.boot_and_health", ok,
                    f"uvicorn pid={srv['proc'].pid} {detail}")
            if not ok:
                boot_err = detail
                srv["proc"].terminate()
                srv["proc"].wait(timeout=10)

            if ok:
                # A1 — real-key write is committed with full database authority.
                sig_valid = _sign(k1_priv, EvidenceType.DATABASE, "db://orders",
                                  "labs", "key-1", valid_issued, valid_expires, "n-a")
                r = _write_signed(client, "agent-a", {"sales.total": 100},
                                  "db://orders", "labs", "key-1", sig_valid,
                                  valid_issued, valid_expires, "n-a")
                auth = _find_authority(client, "sales", 100)
                _record("acceptance.real_key_write_read",
                        r["status"] == "committed" and auth == 0.9,
                        f"status={r['status']} authority={auth} (expect 0.9)")

                # A2 — tampered signature degrades to the unverified fallback.
                tampered = base64.b64encode(
                    base64.b64decode(sig_valid)[:-1] + bytes([0])).decode("ascii")
                r2 = _write_signed(client, "agent-b", {"tampered.claim": "x"},
                                   "db://orders", "labs", "key-1", tampered,
                                   valid_issued, valid_expires, "n-b")
                auth2 = _find_authority(client, "tampered", "x")
                _record("acceptance.tamper_rejection",
                        auth2 is not None and auth2 <= 0.1,
                        f"status={r2['status']} authority={auth2} (expect <=0.1)")

                # A3 — expired evidence degrades (temporal enforcement).
                sig_exp = _sign(k1_priv, EvidenceType.DATABASE, "db://orders",
                                "labs", "key-1", expired_issued, expired_expires, "n-c")
                r3 = _write_signed(client, "agent-c", {"expired.claim": "y"},
                                   "db://orders", "labs", "key-1", sig_exp,
                                   expired_issued, expired_expires, "n-c")
                auth3 = _find_authority(client, "expired", "y")
                _record("acceptance.temporal_rejection",
                        auth3 is not None and auth3 <= 0.1,
                        f"status={r3['status']} authority={auth3} (expect <=0.1)")

                # A4 — replay of a consumed nonce degrades.
                sig_rp = _sign(k1_priv, EvidenceType.DATABASE, "db://orders",
                               "labs", "key-1", valid_issued, valid_expires, "n-replay")
                _write_signed(client, "agent-d", {"replay.first": "v1"},
                              "db://orders", "labs", "key-1", sig_rp,
                              valid_issued, valid_expires, "n-replay")
                r4 = _write_signed(client, "agent-e", {"replay.second": "v2"},
                                   "db://orders", "labs", "key-1", sig_rp,
                                   valid_issued, valid_expires, "n-replay")
                auth4 = _find_authority(client, "replay", "v2")
                _record("acceptance.replay_rejection",
                        auth4 is not None and auth4 <= 0.1,
                        f"second_use_authority={auth4} (expect <=0.1)")

                # A5 — key rotation: registering key-2 accepts evidence signed with it.
                SQLiteProviderRegistry(SQLiteStorage(db)).register_provider(
                    "labs", "key-2", k2_pub)
                sig_k2 = _sign(k2_priv, EvidenceType.DATABASE, "db://orders",
                               "labs", "key-2", valid_issued, valid_expires, "n-k2")
                r5 = _write_signed(client, "agent-f", {"rotated.claim": "z"},
                                   "db://orders", "labs", "key-2", sig_k2,
                                   valid_issued, valid_expires, "n-k2")
                auth5 = _find_authority(client, "rotated", "z")
                _record("acceptance.rotation_acceptance",
                        auth5 == 0.9,
                        f"status={r5['status']} authority={auth5} (expect 0.9)")

                # A6 — revoking key-1 stops accepting its signatures.
                SQLiteProviderRegistry(SQLiteStorage(db)).revoke_key("labs", "key-1")
                sig_rev = _sign(k1_priv, EvidenceType.DATABASE, "db://orders",
                                "labs", "key-1", valid_issued, valid_expires, "n-rev")
                r6 = _write_signed(client, "agent-g", {"revoked.claim": "w"},
                                   "db://orders", "labs", "key-1", sig_rev,
                                   valid_issued, valid_expires, "n-rev")
                auth6 = _find_authority(client, "revoked", "w")
                _record("acceptance.revocation_rejection",
                        auth6 is not None and auth6 <= 0.1,
                        f"status={r6['status']} authority={auth6} (expect <=0.1)")

                # A7 — an unknown provider fails closed (no configured key).
                sig_ghost = _sign(k1_priv, EvidenceType.DATABASE, "db://orders",
                                  "ghost", "key-x", valid_issued, valid_expires, "n-ghost")
                r7 = _write_signed(client, "agent-h", {"ghost.claim": "q"},
                                   "db://orders", "ghost", "key-x", sig_ghost,
                                   valid_issued, valid_expires, "n-ghost")
                auth7 = _find_authority(client, "ghost", "q")
                _record("acceptance.unknown_provider_fail_closed",
                        auth7 is not None and auth7 <= 0.1,
                        f"status={r7['status']} authority={auth7} (expect <=0.1)")

            evidence["phase_a"] = {
                "db_path": db,
                "port": srv["port"],
                "pid": srv["proc"].pid,
                "boot_ok": ok,
                "nonces_used": ["n-a", "n-b", "n-c", "n-replay", "n-k2", "n-rev", "n-ghost"],
            }

        # ---- Phase B: server #2 — restart persistence ----------------------
        if boot_err is None:
            with _running_server(db) as srv:
                client = srv["client"]
                ok2, detail2 = _wait_health(client)
                _record("acceptance.restart.boot", ok2,
                        f"pid={srv['proc'].pid} {detail2}")
                if ok2:
                    # B1 — committed packet survived the restart.
                    auth_persist = _find_authority(client, "sales", 100)
                    _record("acceptance.restart.persistence",
                            auth_persist == 0.9,
                            f"authority after restart={auth_persist} (expect 0.9)")

                    # B2 — the consumed nonce is still rejected (durable replay guard).
                    r8 = _write_signed(client, "agent-i", {"replay.third": "v3"},
                                       "db://orders", "labs", "key-1", sig_rp,
                                       valid_issued, valid_expires, "n-replay")
                    auth8 = _find_authority(client, "replay", "v3")
                    _record("acceptance.restart.replay_persisted",
                            auth8 is not None and auth8 <= 0.1,
                            f"authority after restart={auth8} (expect <=0.1)")

                    # B3 — the rotated key-2 survived the restart.
                    sig_k2b = _sign(k2_priv, EvidenceType.DATABASE, "db://orders",
                                    "labs", "key-2", valid_issued, valid_expires, "n-k2b")
                    r9 = _write_signed(client, "agent-j", {"restart.rotated": "zz"},
                                       "db://orders", "labs", "key-2", sig_k2b,
                                       valid_issued, valid_expires, "n-k2b")
                    auth9 = _find_authority(client, "restart", "zz")
                    _record("acceptance.restart.registry_persisted",
                            auth9 == 0.9,
                            f"status={r9['status']} authority={auth9} (expect 0.9)")

                evidence["phase_b"] = {"db_path": db, "port": srv["port"],
                                       "pid": srv["proc"].pid, "boot_ok": ok2}

        # ---- Phase C: server #3 — default in-memory path still boots --------
        if boot_err is None:
            with _running_server(None) as srv:
                client = srv["client"]
                ok3, detail3 = _wait_health(client)
                _record("acceptance.inmemory_default", ok3,
                        f"pid={srv['proc'].pid} no LCM_SQLITE_PATH {detail3}")
                evidence["phase_c"] = {"port": srv["port"], "pid": srv["proc"].pid,
                                       "boot_ok": ok3}

    if boot_err is not None:
        _record("acceptance.fatal", False,
                f"server failed to boot with seeded temp-file SQLite: {boot_err}")
    return checks


# ===========================================================================
# Category 4 — real_agent_evaluation
# ===========================================================================

# Exactly the hash publication_figures.py records: sha256 of the canonical
# JSON serialization of the cache payload, not of the raw file bytes.
def _cache_sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _probe_ollama(ollama_url: str = "http://localhost:11434",
                  model: str = "llama3.1:8b") -> Tuple[bool, str]:
    """Return (available, detail). Never fabricates a real-agent result."""
    import httpx
    try:
        resp = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
        names = [m.get("name", "") for m in resp.json().get("models", [])]
        if model in names:
            return True, f"Ollama reachable, {model} available ({len(names)} models)"
        return False, (f"Ollama reachable but {model} not installed "
                       f"(available: {names[:8]})")
    except Exception as exc:  # noqa: BLE001
        return False, f"Ollama not reachable at {ollama_url} ({exc})"


def _figure_integrity_checks(category: str) -> List[CheckResult]:
    """Figure cache/manifest integrity (part of the real-agent evaluation gate).

    Verifies the committed ``results/figures_data.json`` cache is present and
    schema-current, that the figures manifest's recorded cache SHA-256 matches
    the cache exactly, that all twelve figures are recorded, and that no figure
    claims a real-agent/mixed classification without validated experiment data.
    """
    checks: List[CheckResult] = []

    cache_path = _ROOT / "results" / "figures_data.json"
    if not cache_path.exists():
        checks.append(_result(
            "figures.cache_present", False, category,
            f"{cache_path.relative_to(_ROOT)} is missing; run "
            f"`python results/publication_figures.py` to rebuild it"))
        return checks
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        schema = cache.get("schema_version", 1)
        cache_ok = schema >= 2
        checks.append(_result(
            "figures.cache_present", cache_ok, category,
            f"cache present, schema_version={schema}"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_result("figures.cache_present", False, category,
                              f"cache unreadable: {exc}"))
        return checks

    manifest_path = _ROOT / "results" / "figures" / "manifest.json"
    if not manifest_path.exists():
        checks.append(_result(
            "figures.cache_manifest_match", False, category,
            "results/figures/manifest.json is missing (figures were generated "
            "without recording a manifest)"))
        return checks
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest.get("cache", {}).get("sha256")
    recomputed = _cache_sha256_text(json.dumps(cache, sort_keys=True, default=str))
    checks.append(_result(
        "figures.cache_manifest_match", recorded == recomputed, category,
        f"manifest cache sha {recorded[:16]}... vs recomputed {recomputed[:16]}... "
        f"({'match' if recorded == recomputed else 'MISMATCH — re-run publication_figures.py'})"))

    expected_figures = {
        "fig_architecture", "fig_race_condition", "fig_mandela_trapping",
        "fig_trust_gap_sweep", "fig_benchmark_c_accuracy", "fig_ablation",
        "fig_conflict_attribution", "fig_deciding_factor", "fig_psi_explainer",
        "fig_multi_agent_experiment", "fig_sensitivity_sweeps",
        "fig_results_comparison",
    }
    recorded_figures = {f["figure"] for f in manifest.get("figures", [])}
    missing = sorted(expected_figures - recorded_figures)
    checks.append(_result(
        "figures.manifest_schema", not missing and manifest.get("manifest_version") == 1,
        category,
        f"manifest_version={manifest.get('manifest_version')} "
        f"figures_recorded={len(recorded_figures)} missing={missing}"))

    experiments = cache.get("experiments", []) or []
    validated_exps = [e for e in experiments if (e.get("_source") or {}).get("validated")]
    violations: List[str] = []
    for f in manifest.get("figures", []):
        cls = f.get("data_classification")
        if cls in ("real-agent", "mixed") and not validated_exps:
            violations.append(f"{f['figure']} classified {cls} with no validated experiments")
    checks.append(_result(
        "figures.real_agent_honesty", not violations, category,
        f"validated_real_agent_artifacts={len(validated_exps)} "
        f"classification_violations={violations or 'none'}"))

    return checks


def run_real_agent_evaluation(
    run_real_agent: bool = False,
    real_agent_model: str = "llama3.1:8b",
    real_agent_scenarios: str = "basic",
    real_agent_trials: int = 1,
    real_agent_timeout: int = 300,
    real_agent_artifact_dir: str = "experiments/results",
    ollama_url: str = "http://localhost:11434",
) -> List[CheckResult]:
    """Run real-agent experiment or probe availability."""
    from experiments.result_schema import validate_real_agent_artifact

    checks: List[CheckResult] = []
    category = "real_agent_evaluation"

    if not run_real_agent:
        # Probe-only mode: check if Ollama is reachable and that the figure
        # cache/manifest are intact (honest SKIP when Ollama is unavailable).
        ok, detail = _probe_ollama()
        if ok:
            checks.append(_result("ollama.probe", True, category, detail))
        else:
            checks.append(_skipped(
                "ollama.probe", category,
                f"{detail} — real-agent evaluation skipped; no synthetic fallback "
                f"(run `ollama serve` + `ollama pull llama3.1:8b` to enable)"))
        checks.extend(_figure_integrity_checks(category))
        return checks

    # Real-agent experiment mode
    print(f"\n=== Running Real-Agent Experiment ===")
    print(f"Model: {real_agent_model}")
    print(f"Scenarios: {real_agent_scenarios}")
    print(f"Trials: {real_agent_trials}")
    print(f"Timeout: {real_agent_timeout}s")
    print(f"Artifact dir: {real_agent_artifact_dir}")

    try:
        ok, probe_detail = _probe_ollama(ollama_url, real_agent_model)
        if not ok:
            checks.append(_blocked("experiment.prerequisites", category, probe_detail))
            return checks
        import httpx
        tags = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10).json().get("models", [])
        model_row = next((m for m in tags if m.get("name") == real_agent_model), {})
        model_digest = model_row.get("digest", "unavailable")
        artifact_dir = Path(real_agent_artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        scenario_list = [s.strip() for s in real_agent_scenarios.split(",") if s.strip()]
        all_results = []
        artifact_rows = []

        for scenario in scenario_list:
            for trial_number in range(1, real_agent_trials + 1):
                try:
                    with tempfile.TemporaryDirectory(prefix="lcm-real-agent-") as tmp:
                        db_path = str(Path(tmp) / "lcm.sqlite3")
                        with _running_server(db_path) as server:
                            ready, detail = _wait_health(server["client"], timeout_s=30)
                            if not ready:
                                raise RuntimeError(f"LCM readiness failed: {detail}")
                            os.environ["LCM_URL"] = server["base_url"]
                            os.environ["LCM_SQLITE_PATH"] = db_path
                            os.environ["OLLAMA_URL"] = ollama_url
                            os.environ["OLLAMA_MODEL_DIGEST"] = model_digest
                            from experiments import run_real_agent_experiment as runner
                            runner._lcm = server["client"]
                            result = runner.run_real_experiment(
                                model=real_agent_model, scenario=scenario, trials=1, save_path=None)
                            result["database_identity"] = db_path
                            result["service_url"] = server["base_url"]
                            result["model_digest"] = model_digest
                            result["requested_trial_number"] = trial_number
                            body = dict(result); body.pop("artifact_sha256", None)
                            result["artifact_sha256"] = _cache_sha256_text(
                                json.dumps(body, sort_keys=True, separators=(",", ":"), default=str))
                    artifact_path = artifact_dir / f"real_agent_{scenario}_{trial_number}_{result['run_id']}.json"
                    artifact_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
                    all_results.append(result)
                    artifact_rows.append({"path": str(artifact_path), "sha256": result["artifact_sha256"],
                                          "scenario": scenario, "trial": trial_number})
                except Exception as exc:
                    checks.append(_result(f"experiment.{scenario}.{trial_number}", False, category,
                                          f"Trial failed and was retained in report: {exc}"))

        if not all_results:
            checks.append(_blocked(
                "experiment.run", category,
                "All scenarios failed — no real-agent data produced"))
            return checks

        # Validate each independently isolated result.
        for i, result in enumerate(all_results):
            validation = validate_real_agent_artifact(result)
            if validation["valid"]:
                checks.append(_result(
                    f"experiment.{result.get('scenario', f'trial_{i}')}.valid",
                    True, category,
                    f"Scenario '{result.get('scenario', f'trial_{i}')}' passed artifact validation"))
            else:
                for issue in validation["issues"]:
                    checks.append(_result(
                        f"experiment.{result.get('scenario', f'trial_{i}')}.valid",
                        False, category, issue))

        manifest = {"schema_version": "1.0", "generated_at": datetime.utcnow().isoformat(),
                    "model": real_agent_model, "model_digest": model_digest,
                    "ollama_url": ollama_url, "requested_trials": real_agent_trials,
                    "requested_scenarios": scenario_list, "artifacts": artifact_rows}
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    except ImportError as e:
        checks.append(_blocked(
            "experiment.run", category,
            f"Cannot import real-agent experiment runner: {e}"))
    except Exception as e:
        checks.append(_result(
            "experiment.run", False, category,
            f"Real-agent experiment failed: {e}"))

    return checks


# ===========================================================================
# Category 5 — documentation_consistency
# ===========================================================================

def run_documentation_consistency() -> List[CheckResult]:
    """Regenerate docs in memory and require a byte-for-byte match with committed."""
    from tools.generate_documentation import REPO_ROOT, check_consistency

    manifest_path = REPO_ROOT / "benchmark_results" / "manifest.json"
    category = "documentation_consistency"
    try:
        report = check_consistency(manifest_path)
    except Exception as exc:  # noqa: BLE001 — manifest missing/tampered etc.
        return [_result("docs.consistency", False, category,
                        f"check_consistency raised: {exc}")]
    failing = [c["name"] for c in report["checks"] if not c["ok"]]
    checks = [_result(
        "docs.consistency", report["ok"], category,
        f"{len(report['checks'])} sub-checks, "
        f"{'all passed' if report['ok'] else 'failing: ' + ', '.join(failing)}")]
    if not report["ok"]:
        for c in report["checks"]:
            if not c["ok"]:
                checks.append(_result(f"docs.sub.{c['name']}", False, category,
                                      c.get("detail", "")))
    return checks


# ===========================================================================
# Category 6 — artifact_validation
# ===========================================================================

# Frozen held-out set pinned targets (identical to tools/generate_documentation).
FROZEN_TARGETS = {
    "n_clear": 16,
    "correct": 11,
    "wrong": 0,
    "abstentions": 5,
    "n_ambiguous": 3,
    "ambiguous_unresolved": 3,
}
FROZEN_RATES = {
    "coverage": 0.6875,        # 11 / 16
    "selective_accuracy": 1.0, # 11 / 11
    "strict_accuracy": 0.6875, # 11 / 16
}


def run_artifact_validation() -> List[CheckResult]:
    """Pinned benchmark artifacts: manifest hashes + frozen held-out numbers."""
    from tools.generate_documentation import (
        FROZEN_TARGETS as _FROZEN_TARGETS,
        REPO_ROOT,
        frozen_held_out_stats,
        load_artifacts,
        load_manifest,
    )

    category = "artifact_validation"
    manifest_path = REPO_ROOT / "benchmark_results" / "manifest.json"
    if not manifest_path.exists():
        return [_result(
            "artifacts.manifest_present", False, category,
            "benchmark_results/manifest.json is missing; run "
            "`python tools/pin_artifacts.py` to pin the validated artifacts")]

    checks: List[CheckResult] = []
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:  # noqa: BLE001 — missing/tampered artifact
        return [_result("artifacts.hashes", False, category,
                        f"manifest hash verification failed: {exc}")]

    checks.append(_result(
        "artifacts.manifest_present", True, category,
        f"manifest schema_version={manifest.get('schema_version')} "
        f"kind={manifest.get('kind')}"))

    n_artifacts = len(manifest["artifacts"])
    checks.append(_result(
        "artifacts.hashes", True, category,
        f"all {n_artifacts} pinned artifacts verified against manifest SHA-256"))

    # Frozen held-out numbers parsed from the pinned benchmark_d artifact.
    arts = load_artifacts(manifest)
    frozen = frozen_held_out_stats(arts["benchmark_d"])
    bad: List[str] = []
    for key, expected in _FROZEN_TARGETS.items():
        actual = frozen[key]
        if actual != expected:
            bad.append(f"{key}={actual}!=expected={expected}")
    for key, expected in FROZEN_RATES.items():
        actual = round(frozen[key], 4)
        if abs(actual - expected) > 1e-6:
            bad.append(f"{key}={actual}!=expected={expected}")

    checks.append(_result(
        "artifacts.frozen_numbers", not bad, category,
        f"frozen held-out: n_clear={frozen['n_clear']} correct={frozen['correct']} "
        f"wrong={frozen['wrong']} abstentions={frozen['abstentions']} "
        f"ambiguous={frozen['n_ambiguous']}/{frozen['ambiguous_unresolved']} "
        f"coverage={round(frozen['coverage'], 4)} "
        f"selective={round(frozen['selective_accuracy'], 4)} "
        f"strict={round(frozen['strict_accuracy'], 4)}"
        + ("" if not bad else " | MISMATCH: " + "; ".join(bad))))

    # Each artifact is non-empty and well-formed.
    sizes: List[str] = []
    empty: List[str] = []
    for key, entry in manifest["artifacts"].items():
        full = REPO_ROOT / entry["path"]
        try:
            n = len(full.read_bytes())
            sizes.append(f"{key}={n}B")
            if n == 0:
                empty.append(key)
        except OSError:
            sizes.append(f"{key}=UNREADABLE")
            empty.append(key)
    checks.append(_result(
        "artifacts.nonempty", not empty, category,
        ", ".join(sizes) + (f" | EMPTY/UNREADABLE: {empty}" if empty else "")))
    return checks


# ===========================================================================
# Orchestration
# ===========================================================================

def verify_release(
    *,
    run_suite: bool = True,
    suite_dir: Optional[str] = None,
    out_dir: str = "results/release",
    python: Optional[str] = None,
    skip_acceptance: bool = False,
    skip_real_agent: bool = False,
    skip_docs: bool = False,
    skip_artifacts: bool = False,
    run_real_agent: bool = False,
    real_agent_model: str = "llama3.1:8b",
    real_agent_scenarios: str = "basic",
    real_agent_trials: int = 1,
    real_agent_timeout: int = 300,
    real_agent_artifact_dir: str = "experiments/results",
    ollama_url: str = "http://localhost:11434",
) -> Dict[str, Any]:
    """Run the six verification categories and produce the report."""
    global _LAST_PYTEST_RUN, _GATE_SUBPROCESSES, _ALLOCATED_PORTS
    _LAST_PYTEST_RUN = {}
    _GATE_SUBPROCESSES = []
    _ALLOCATED_PORTS = []
    started = datetime.utcnow().isoformat()

    category_checks: Dict[str, List[CheckResult]] = {}

    category_checks["core_invariants"] = run_invariant_checks()

    if run_suite:
        category_checks["pytest_suite"] = pytest_suite_checks(suite_dir or "tests", python=python)

    if not skip_acceptance:
        category_checks["real_component_acceptance"] = run_real_component_acceptance()

    if not skip_real_agent:
        category_checks["real_agent_evaluation"] = run_real_agent_evaluation(
            run_real_agent=run_real_agent,
            real_agent_model=real_agent_model,
            real_agent_scenarios=real_agent_scenarios,
            real_agent_trials=real_agent_trials,
            real_agent_timeout=real_agent_timeout,
            real_agent_artifact_dir=real_agent_artifact_dir,
            ollama_url=ollama_url,
        )

    if not skip_docs:
        category_checks["documentation_consistency"] = run_documentation_consistency()

    if not skip_artifacts:
        category_checks["artifact_validation"] = run_artifact_validation()

    verdicts = {cat: category_verdict(category_checks[cat]) for cat in CATEGORIES
                if cat in category_checks}

    report: Dict[str, Any] = {
        "tool": "verify_release",
        "phase": 15,
        "generated_at": started,
        "categories": {},
        "verdict": overall_verdict(verdicts),
        "execution_evidence": {
            "pytest": _LAST_PYTEST_RUN,
            "gate_subprocesses": _GATE_SUBPROCESSES,
            "allocated_ports": _ALLOCATED_PORTS,
        },
    }
    for cat in CATEGORIES:
        if cat not in category_checks:
            continue
        checks = category_checks[cat]
        report["categories"][cat] = {
            "verdict": verdicts[cat],
            "total": len(checks),
            "passed": sum(1 for c in checks if c.passed and not c.skipped),
            "skipped": sum(1 for c in checks if c.skipped and not c.blocked),
            "blocked": sum(1 for c in checks if c.blocked),
            "failed": sum(1 for c in checks if not c.skipped and not c.passed),
            "checks": [asdict(c) for c in checks],
        }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fname = out / f"verify_release_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    fname.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=True))
    report["saved_to"] = str(fname)
    return report


def _print_report(report: Dict[str, Any]) -> None:
    print(f"\nverify_release (phase 15) — {report['generated_at']}")
    for cat, data in report["categories"].items():
        print(f"\n  [{data['verdict']:<15}] {cat} "
              f"({data['passed']} passed, {data['skipped']} skipped, "
              f"{data['blocked']} blocked, {data['failed']} failed)")
        for c in data["checks"]:
            if not c["passed"] and not c["skipped"]:
                mark = "FAIL"
            elif c["blocked"]:
                mark = "BLOCKED"
            elif c["skipped"]:
                mark = "SKIP"
            else:
                mark = "PASS"
            print(f"    [{mark:<7}] {c['name']}")
            print(f"        {c['detail']}")
    print(f"\n  verdict: {report['verdict']}")
    print(f"  report: {report['saved_to']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LCM release verification gate (6 categories)")
    parser.add_argument("--no-suite", action="store_true",
                        help="Run invariant checks only (skip the pytest suite)")
    parser.add_argument("--suite-dir", default=None,
                        help="pytest target directory (default: tests/)")
    parser.add_argument("--out", default="results/release",
                        help="Output directory for the JSON report")
    parser.add_argument("--skip-acceptance", action="store_true",
                        help="Skip the live-server real-component acceptance")
    parser.add_argument("--skip-real-agent", action="store_true",
                        help="Skip the Ollama probe + figures category")
    parser.add_argument("--skip-docs", action="store_true",
                        help="Skip the documentation-consistency category")
    parser.add_argument("--skip-artifacts", action="store_true",
                        help="Skip the artifact-validation category")
    parser.add_argument("--run-real-agent", action="store_true",
                        help="Run real Ollama agent experiments (default: probe only)")
    parser.add_argument("--real-agent-model", default="llama3.1:8b",
                        help="Ollama model for real-agent experiments (default: llama3.1:8b)")
    parser.add_argument("--real-agent-scenarios", default="basic",
                        help="Comma-separated scenarios to run (default: basic)")
    parser.add_argument("--real-agent-trials", type=int, default=1,
                        help="Number of independent trials per scenario (default: 1)")
    parser.add_argument("--real-agent-timeout", type=int, default=300,
                        help="Timeout in seconds for real-agent experiments (default: 300)")
    parser.add_argument("--real-agent-artifact-dir", default="experiments/results",
                        help="Directory to save real-agent artifacts (default: experiments/results)")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama URL (default: http://localhost:11434)")
    args = parser.parse_args(argv)

    report = verify_release(
        run_suite=not args.no_suite,
        suite_dir=args.suite_dir,
        out_dir=args.out,
        skip_acceptance=args.skip_acceptance,
        skip_real_agent=args.skip_real_agent,
        skip_docs=args.skip_docs,
        skip_artifacts=args.skip_artifacts,
        run_real_agent=args.run_real_agent,
        real_agent_model=args.real_agent_model,
        real_agent_scenarios=args.real_agent_scenarios,
        real_agent_trials=args.real_agent_trials,
        real_agent_timeout=args.real_agent_timeout,
        real_agent_artifact_dir=args.real_agent_artifact_dir,
        ollama_url=args.ollama_url,
    )
    _print_report(report)
    return 0 if report["verdict"] in ("PASS", "PASS_WITH_SKIPS") else 1


if __name__ == "__main__":
    sys.exit(main())
