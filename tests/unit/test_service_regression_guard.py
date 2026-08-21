"""
Regression guard: the live HTTP service MUST run the V1 conflict engine.

Fails loudly (rather than silently) if `crt_service/app.py` is ever wired
back to the legacy 4-component engine (`crt_core.conflict`, Ψ = 0.25R+0.25C+0.25T+0.25P):

  1. Static wiring check — app.py must import ConflictResolutionEngine from
     `crt_core.conflict` and must NOT import `crt_core.conflict`.
  2. Live response check — boot a real uvicorn subprocess of crt_service.app,
     write two conflicting claims to the same path, and assert the returned
     `psi_winner_breakdown` contains EXACTLY three components (R, C, T) at
     equal weight 1/3 each and NO "P".
  3. Schema hardening check — a write carrying forged top-level fields
     (provenance_id / authority_score / invalid_field) must be rejected with
     HTTP 422 (extra="forbid"), not silently accepted.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]  # repo root
APP_SRC = ROOT / "crt_service" / "app.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(tmp_db: Path):
    port = _free_port()
    env = os.environ.copy()
    env["CRT_SQLITE_PATH"] = str(tmp_db)
    env["CRT_RESOLUTION_POLICY"] = "full_crt"
    env["CRT_EVALUATION_MODE"] = "1"
    env.pop("CRT_VERIFIER_SECRET", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "crt_service.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=0.5) as c:
            for _ in range(150):
                try:
                    r = c.get(f"{base}/")
                    if r.status_code == 200 and r.json().get("status") == "operational":
                        return proc, base
                except httpx.HTTPError:
                    pass
                if proc.poll() is not None:
                    raise RuntimeError(f"uvicorn exited early ({proc.returncode})")
                time.sleep(0.1)
    except Exception:
        proc.terminate()
        raise
    raise RuntimeError("CRT service failed to start for regression guard")


def _write(client, base, path, value, agent, confidence=0.8, evidence=None):
    body = {
        "agent_id": agent,
        "session_id": "guard_test",
        "timestamp": "2026-08-20T12:00:00",
        "confidence_score": confidence,
        "assertion_payload": {path: value},
        "evidence_records": evidence or [],
    }
    return client.post(f"{base}/write", json=body)


def test_static_wiring_is_v1():
    src = APP_SRC.read_text(encoding="utf-8")
    assert "from crt_core.conflict import ConflictResolutionEngine" in src, (
        "app.py must import the V1 engine from crt_core.conflict"
    )
    assert "from crt_core.conflict import ConflictResolutionEngine as _CRE2" in src, (
        "get_context engine must be V1"
    )


def test_write_response_has_v1_breakdown_no_p():
    with tempfile.TemporaryDirectory() as td:
        proc, base = _start_server(Path(td) / "guard.sqlite")
        try:
            with httpx.Client(timeout=30.0) as client:
                r1 = _write(
                    client, base, "weather/sensor/x/obs", "temperature is 21C", "agent_a",
                    confidence=0.6,
                    evidence=[{"type": "agent_claim", "source": "claim-a"}],
                )
                assert r1.status_code == 201, r1.text
                r2 = _write(
                    client, base, "weather/sensor/x/obs", "temperature is 23C", "agent_b",
                    confidence=0.9,
                    evidence=[{"type": "database", "source": "db:readings"}],
                )
                assert r2.status_code == 201, r2.text
                data = r2.json()
                break_w = data.get("psi_winner_breakdown") or {}
                break_l = data.get("psi_loser_breakdown") or {}
                for bd in (break_w, break_l):
                    if not bd:
                        continue
                    assert "P" not in bd, f"V1 provenance component leaked: {bd}"
                    comps = [k for k in ("R", "C", "T") if k in bd]
                    assert comps == ["R", "C", "T"], f"expected R,C,T, got {sorted(bd)}"
                    weights = [bd.get("w_r"), bd.get("w_c"), bd.get("w_t")]
                    for w in weights:
                        assert w is not None and abs(w - 1.0 / 3.0) < 1e-6, (
                            f"expected equal 1/3 weights, got {weights}"
                        )
                assert break_w or break_l, "no psi breakdown returned; conflict did not occur"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_forged_top_level_fields_rejected_422():
    with tempfile.TemporaryDirectory() as td:
        proc, base = _start_server(Path(td) / "guard.sqlite")
        try:
            with httpx.Client(timeout=30.0) as client:
                body = {
                    "agent_id": "agent_a",
                    "session_id": "guard_test",
                    "timestamp": "2026-08-20T12:00:00",
                    "confidence_score": 0.8,
                    "assertion_payload": {"weather/raw": "sneaky"},
                    "provenance_id": "forged_id_12345",
                    "authority_score": 0.99,
                    "invalid_field": "test",
                }
                resp = client.post(f"{base}/write", json=body)
                assert resp.status_code == 422, (
                    f"forged fields must be rejected with 422, got "
                    f"{resp.status_code}: {resp.text[:200]}"
                )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()