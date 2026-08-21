"""Permanent fail-closed isolation checks for research and demo ASGI modes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _probe(source: str, *, env: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env or os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(
            f"probe failed with exit {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(completed.stdout)


def test_default_service_fails_closed_without_demo_import_or_routes():
    result = _probe(
        "import json,sys; import crt_service.app as s; "
        "print(json.dumps({'demo_imported': 'demo.orchestrator.events_ws' in sys.modules, "
        "'demo_routes': sorted(r.path for r in s.app.routes if r.path.startswith('/demo/')), "
        "'ledger_wrapped': bool(getattr(s._ledger.refresh_trust, '_demo_observed', False))}))"
    )
    assert result == {"demo_imported": False, "demo_routes": [], "ledger_wrapped": False}


def test_importing_demo_router_module_has_no_service_side_effects():
    result = _probe(
        "import json,sys; import demo.orchestrator.events_ws; "
        "print(json.dumps({'service_imported': 'crt_service.app' in sys.modules}))"
    )
    assert result == {"service_imported": False}


def test_demo_launcher_replaces_inherited_research_database(tmp_path):
    research_db = tmp_path / "research.sqlite"
    sentinel = b"research-database-sentinel"
    research_db.write_bytes(sentinel)
    env = os.environ.copy()
    env["CRT_SQLITE_PATH"] = str(research_db)
    result = _probe(
        "import json,os; import demo.service as d; "
        "print(json.dumps({'demo_path': d.DEMO_SQLITE_PATH, "
        "'effective_path': os.environ['CRT_SQLITE_PATH'], "
        "'mode': d.app.state.service_mode, "
        "'demo_routes': sorted(r.path for r in d._demo_router.routes if r.path.startswith('/demo/')), "
        "'ledger_wrapped': bool(getattr(d._service_module._ledger.refresh_trust, '_demo_observed', False))}))",
        env=env,
    )
    assert Path(result["demo_path"]) != research_db
    assert result["effective_path"] == result["demo_path"]
    assert result["mode"] == "demo"
    assert result["demo_routes"] == [
        "/demo/lineage/{provenance_id}",
        "/demo/stream",
        "/demo/tamper/{provenance_id}",
    ]
    assert result["ledger_wrapped"] is True
    assert research_db.read_bytes() == sentinel


def test_demo_and_research_share_evaluation_handlers_but_not_demo_surface():
    research = _probe(
        "import json; import crt_service.app as s; "
        "print(json.dumps({'evaluation': sorted((r.path, sorted(r.methods or [])) for r in s.app.routes "
        "if getattr(r, 'path', None) in {'/write','/verify','/metrics','/trust/{agent_id}','/context/{path:path}'}), "
        "'demo': sorted(r.path for r in s.app.routes if getattr(r, 'path', '').startswith('/demo/'))}))"
    )
    demo = _probe(
        "import json; import demo.service as d; "
        "print(json.dumps({'evaluation': sorted((r.path, sorted(r.methods or [])) for r in d.app.routes "
        "if getattr(r, 'path', None) in {'/write','/verify','/metrics','/trust/{agent_id}','/context/{path:path}'}), "
        "'demo': sorted(r.path for r in d._demo_router.routes if r.path.startswith('/demo/'))}))"
    )
    assert demo["evaluation"] == research["evaluation"]
    assert research["demo"] == []
    assert demo["demo"]
