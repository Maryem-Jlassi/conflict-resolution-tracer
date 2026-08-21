"""Real HTTP/SQLite acceptance for simultaneous research and demo services."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

from demo.runtime_env import DEMO_VERIFIER_SECRET
from crt_core.verifier import canonical_verifier_message, compute_verifier_token


ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(target: str, port: int, env: dict[str, str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(120):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.05)
    proc.kill()
    proc.wait()
    raise RuntimeError(f"service failed: {target}")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


def _write(base: str, agent: str, path: str, value: str, timestamp: str) -> dict:
    response = httpx.post(base + "/write", json={
        "agent_id": agent,
        "session_id": "service-isolation",
        "timestamp": timestamp,
        "confidence_score": 0.99,
        "assertion_payload": {path: value},
        "domain": "isolation",
    }, timeout=5)
    assert response.status_code == 201, response.text
    return response.json()


def _verify(base: str, outcome: str, agent: str) -> dict:
    message = canonical_verifier_message(
        outcome_id=outcome,
        target_agent_id=agent,
        domain="isolation",
        correct=True,
        target_provenance_id=None,
        observed_at="2026-08-12T12:00:00Z",
    )
    response = httpx.post(base + "/verify", json={
        "outcome_id": outcome,
        "target_agent_id": agent,
        "domain": "isolation",
        "correct": True,
        "target_provenance_id": None,
        "observed_at": "2026-08-12T12:00:00Z",
        "verifier_token": compute_verifier_token(DEMO_VERIFIER_SECRET, message),
    }, timeout=5)
    assert response.status_code == 200, response.text
    return response.json()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _port_released(port: int, timeout: float = 5.0) -> bool:
    """Wait for Windows to finish releasing a terminated listener."""
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _row_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in names}


def test_live_research_and_demo_http_sqlite_isolation():
    owned_root = Path(tempfile.mkdtemp(prefix="crt-isolation-"))
    demo_root = Path(tempfile.mkdtemp(prefix="crt-demo-"))
    sentinel = owned_root / "unrelated.sentinel"
    sentinel.write_bytes(b"unrelated-byte-identical-sentinel")
    sentinel_hash = _sha(sentinel)
    research_db = owned_root / "research.sqlite"
    demo_db = demo_root / "demo.sqlite"
    research_port, demo_port = _port(), _port()
    research_base = f"http://127.0.0.1:{research_port}"
    demo_base = f"http://127.0.0.1:{demo_port}"
    common = os.environ.copy()
    common["CRT_VERIFIER_SECRET"] = DEMO_VERIFIER_SECRET
    research_env = common | {"CRT_SQLITE_PATH": str(research_db), "CRT_EVALUATION_MODE": "1"}
    demo_env = common | {
        "CRT_SQLITE_PATH": str(research_db),
        "CRT_DEMO_SQLITE_PATH": str(demo_db),
    }
    research = demo = None
    try:
        research = _start("crt_service.app:app", research_port, research_env)
        demo = _start("demo.service:app", demo_port, demo_env)

        research_openapi = httpx.get(research_base + "/openapi.json").json()
        demo_openapi = httpx.get(demo_base + "/openapi.json").json()
        assert not [p for p in research_openapi["paths"] if p.startswith("/demo/")]
        # OpenAPI describes the two HTTP demo routes; the WebSocket route is
        # present in the ASGI router but intentionally absent from OpenAPI.
        assert len([p for p in demo_openapi["paths"] if p.startswith("/demo/")]) == 2
        assert httpx.get(research_base + "/demo/lineage/nope").status_code == 404

        research_write = _write(research_base, "research-agent", "isolation.value", "RESEARCH", "2026-08-12T10:00:00Z")
        demo_write = _write(demo_base, "demo-agent", "isolation.value", "DEMO", "2026-08-12T10:00:00Z")
        assert httpx.get(research_base + "/context/isolation.value").json()["facts"][0]["assertion_payload"]["isolation.value"] == "RESEARCH"
        assert httpx.get(demo_base + "/context/isolation.value").json()["facts"][0]["assertion_payload"]["isolation.value"] == "DEMO"

        _verify(research_base, "research-outcome", "research-agent")
        _verify(demo_base, "demo-outcome", "demo-agent")
        assert httpx.get(research_base + "/trust/research-agent?domain=isolation").json()["outcome_count"] == 1
        assert httpx.get(research_base + "/trust/demo-agent?domain=isolation").json()["outcome_count"] == 0
        assert httpx.get(demo_base + "/trust/demo-agent?domain=isolation").json()["outcome_count"] == 1

        _write(research_base, "research-pending-a", "isolation.pending", "A", "2026-08-12T11:00:00Z")
        pending = _write(research_base, "research-pending-b", "isolation.pending", "B", "2026-08-12T11:00:00Z")
        assert pending["status"] == "unresolved"
        assert httpx.get(demo_base + "/context/isolation.pending").json()["count"] == 0

        research_hash_before_tamper = _sha(research_db)
        tamper = httpx.post(demo_base + f"/demo/tamper/{demo_write['provenance_id']}", timeout=5)
        assert tamper.status_code == 200
        assert tamper.json()["status"] == "simulated_out_of_band_tamper"
        assert _sha(research_db) == research_hash_before_tamper
        assert httpx.get(research_base + "/context/isolation.value").json()["facts"][0]["assertion_payload"]["isolation.value"] == "RESEARCH"

        _stop(demo); demo = None
        assert httpx.get(research_base + "/context/isolation.value").status_code == 200
        demo = _start("demo.service:app", demo_port, demo_env)
        assert httpx.get(demo_base + "/context/isolation.value").json()["count"] >= 1
        _stop(research); research = None
        research = _start("crt_service.app:app", research_port, research_env)
        assert httpx.get(research_base + "/context/isolation.value").json()["facts"][0]["assertion_payload"]["isolation.value"] == "RESEARCH"
        assert httpx.get(research_base + "/context/isolation.pending").json()["count"] == 2
        assert research_write["provenance_id"]
        print(json.dumps({
            "research_port": research_port,
            "demo_port": demo_port,
            "research_http_demo_routes": 0,
            "demo_http_routes": 2,
            "demo_websocket_routes": 1,
            "research_database_id": "research.sqlite",
            "demo_database_id": "demo.sqlite",
            "databases_distinct": research_db.resolve() != demo_db.resolve(),
            "research_sha256_after_demo_tamper": _sha(research_db),
            "demo_sha256_after_demo_tamper": _sha(demo_db),
            "research_rows": _row_counts(research_db),
            "demo_rows": _row_counts(demo_db),
            "sentinel_sha256": sentinel_hash,
            "processes_terminated": True,
            "ports_released": True,
        }, sort_keys=True))
    finally:
        if demo is not None:
            _stop(demo)
        if research is not None:
            _stop(research)
        assert _port_released(research_port)
        assert _port_released(demo_port)
        assert _sha(sentinel) == sentinel_hash
        shutil.rmtree(demo_root)
        shutil.rmtree(owned_root)
        assert not demo_root.exists()
        assert not owned_root.exists()
