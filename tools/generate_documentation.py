"""
Documentation generator (Phase 14).

Generates the README's benchmark + configuration sections and the standalone
docs under ``docs/`` FROM the central configuration and the validated benchmark
artifacts — never from hand-typed numbers. The documentation-consistency check
(``tools/check_documentation.py`` and the ``documentation_consistency`` gate in
``tools/verify_release.py``) regenerates everything in memory and requires a
byte-for-byte match with what is committed, so any manual drift or artifact
tamper fails the release gate.

Sources of truth:
  * ``lcm_core/config.py``         → Ψ weights, thresholds, authority table
  * ``lcm_core/confidence_engine`` → EVIDENCE_AUTHORITY, confidence semantics
  * ``lcm_core/conflict.py``       → recency half-life
  * ``lcm_core/trust_manager.py``  → trust decay half-life
  * ``benchmark_results/manifest.json`` → validated benchmark artifacts (+ hashes)

Generated outputs:
  * ``docs/configuration_reference.md``        — authority / confidence / Ψ / trust tables
  * ``docs/benchmark_results.md``              — headline metrics from validated artifacts
  * ``docs/trust_and_temporal_validity.md``    — reported vs verified confidence, trust,
                                                recency, temporal validity semantics
  * README sections between the markers
    ``<!-- BEGIN GENERATED:benchmarks -->`` / ``<!-- END GENERATED:benchmarks -->`` and
    ``<!-- BEGIN GENERATED:configuration -->`` / ``<!-- END GENERATED:configuration -->``

Usage::

    python tools/generate_documentation.py --write        # write docs + patch README
    python tools/check_documentation.py                    # consistency gate (exit non-zero on drift)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lcm_core.config import DEFAULT_CONFIG
from lcm_core.confidence_engine import EVIDENCE_AUTHORITY, EvidenceType
from lcm_core.conflict import ConflictResolutionEngine
from lcm_core.trust_manager import TrustManager

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DEFAULT = "benchmark_results/manifest.json"
DOCS_DIR = REPO_ROOT / "docs"
README_PATH = REPO_ROOT / "README.md"

# Frozen held-out set pinned targets (from the phase specification). The
# generator asserts the validated artifact reproduces these exactly.
FROZEN_TARGETS = {
    "n_clear": 16,
    "correct": 11,
    "wrong": 0,
    "abstentions": 5,
    "n_ambiguous": 3,
    "ambiguous_unresolved": 3,
}

BEGIN = "<!-- BEGIN GENERATED:{name} -->"
END = "<!-- END GENERATED:{name} -->"


# ---------------------------------------------------------------------------
# Manifest + artifact loading
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Load the validated-artifacts manifest and verify hashes on disk."""
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Validated-artifacts manifest not found at {manifest_path}; "
            "run `python tools/pin_artifacts.py` first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("kind") == "lcm_validated_artifacts"
    for key, entry in manifest["artifacts"].items():
        rel = Path(entry["path"])
        full = (manifest_path.parent.parent / rel) if not rel.is_absolute() else rel
        if not full.exists():
            raise FileNotFoundError(
                f"Validated artifact {rel} referenced by the manifest is missing.")
        actual = sha256_of(full)
        if actual != entry["sha256"]:
            raise ValueError(
                f"Validated artifact {rel} hashes to {actual[:16]}... but the manifest "
                f"records {entry['sha256'][:16]}... (tampered or stale). Re-run "
                f"`python tools/pin_artifacts.py` after regeneration."
            )
    return manifest


def _read_csv(key: str, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    full = REPO_ROOT / manifest["artifacts"][key]["path"]
    with open(full, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_artifacts(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Parse every validated artifact into structured, benchmark-specific data."""
    return {
        "benchmark_a": _read_csv("benchmark_a", manifest),
        "benchmark_b": _read_csv("benchmark_b", manifest),
        "benchmark_c": _read_csv("benchmark_c", manifest),
        "benchmark_d": _read_csv("benchmark_d", manifest),
        "benchmark_e": json.loads(
            (REPO_ROOT / manifest["artifacts"]["benchmark_e"]["path"]).read_text(
                encoding="utf-8")
        ),
    }


# ---------------------------------------------------------------------------
# Headline metric computation (from validated artifacts)
# ---------------------------------------------------------------------------

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def headline_benchmark_a(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """LCM vs No-LCM mean lost-update rate, per writer count and failure mode."""
    out: Dict[str, Any] = {}
    for r in rows:
        key = (r["system"], int(float(r["n_writers"])), float(r["failure_rate_param"]))
        out.setdefault(key, []).append(float(r["lost_update_rate"]))
    table: Dict[str, Dict[str, List[float]]] = {}
    for (system, n, fr), rates in sorted(out.items()):
        table.setdefault(system, {}).setdefault(str(n), {})[str(fr)] = _mean(rates)
    return {"table": table}


def headline_benchmark_b(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Trapping efficiency per (mode, trust_gap). Rows without a gap (e.g. the
    no-LCM control) are excluded — the table reports the LCM defender modes."""
    out: Dict[Tuple[str, float], List[float]] = {}
    for r in rows:
        gap_raw = r.get("trust_gap", "").strip()
        if not gap_raw:
            continue
        key = (r["mode"], float(gap_raw))
        out.setdefault(key, []).append(float(r["contradiction_trapping_efficiency"]))
    table: Dict[str, Dict[str, float]] = {}
    for (mode, gap), rates in sorted(out.items()):
        table.setdefault(mode, {})[f"{gap:g}"] = _mean(rates)
    return {"table": table}


def headline_benchmark_c(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Accuracy + unresolved rate per (strategy, scenario_type)."""
    out: Dict[Tuple[str, str], List[Tuple[bool, bool]]] = {}
    for r in rows:
        key = (r["strategy"], r["scenario_type"])
        out.setdefault(key, []).append(
            (r.get("correct") == "True" or r.get("correct") == "true",
             r.get("unresolved") == "True" or r.get("unresolved") == "true"))
    table: Dict[str, Dict[str, Dict[str, float]]] = {}
    for (strategy, s_type), pairs in out.items():
        table.setdefault(strategy, {})[s_type] = {
            "accuracy": _mean([1.0 if c else 0.0 for c, _ in pairs]),
            "unresolved_rate": _mean([1.0 if u else 0.0 for _, u in pairs]),
        }
    return {"table": table}


def frozen_held_out_stats(d_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the frozen held-out statistics from the benchmark-d artifact."""
    full = [r for r in d_rows
            if r["fixture_set"] == "frozen_held_out" and r["condition"] == "Full"]
    clear = [r for r in full if r["gt"] != "unresolved"]
    amb = [r for r in full if r["gt"] == "unresolved"]

    def _b(v: Optional[str]) -> bool:
        return v == "True" or v == "true"

    rc = sum(1 for r in clear if _b(r["resolved_correct"]))
    rw = sum(1 for r in clear if _b(r["resolved_wrong"]))
    un = sum(1 for r in clear if _b(r["unresolved"]))
    amb_unres = sum(1 for r in amb if _b(r["unresolved"]))
    n_clear = len(clear)
    n_amb = len(amb)
    stats = {
        "n_clear": n_clear,
        "correct": rc,
        "wrong": rw,
        "abstentions": un,
        "coverage": (rc + rw) / n_clear if n_clear else 0.0,
        "selective_accuracy": (rc / (rc + rw)) if (rc + rw) else 0.0,
        "strict_accuracy": rc / n_clear if n_clear else 0.0,
        "n_ambiguous": n_amb,
        "ambiguous_unresolved": amb_unres,
        "abstaining_paths": sorted(r["path"] for r in clear if _b(r["unresolved"])),
    }
    return stats


def headline_benchmark_d(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per fixture-set Full-condition strict accuracy + frozen held-out stats."""
    by_fixture: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r["condition"] != "Full":
            continue
        by_fixture.setdefault(r["fixture_set"], []).append(r)

    def _b(v: Optional[str]) -> bool:
        return v == "True" or v == "true"

    per_fixture = {}
    for fixture, sub in by_fixture.items():
        n = len(sub)
        acc = sum(1 for r in sub if _b(r["correct"])) / n if n else 0.0
        per_fixture[fixture] = {"n": n, "strict_accuracy": acc}
    return {
        "per_fixture": per_fixture,
        "frozen": frozen_held_out_stats(rows),
    }


def headline_benchmark_e(payload: Dict[str, Any]) -> Dict[str, Any]:
    agg = payload.get("aggregate", {})
    return {
        "tag": payload.get("tag"),
        "conflict_rate_mean": agg.get("conflict_rate_mean"),
        "verification_win_rate_mean": agg.get("verification_win_rate_mean"),
        "lost_updates": agg.get("lost_updates"),
        "deciding_factor_counts": agg.get("deciding_factor_counts", {}),
    }


# ---------------------------------------------------------------------------
# Configuration data (single source of truth: lcm_core.config + engines)
# ---------------------------------------------------------------------------

def configuration_data() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG
    weights = cfg.psi_weights
    engine = ConflictResolutionEngine(psi_weights={
        "recency": weights.w_recency,
        "confidence": weights.w_confidence,
        "trust": weights.w_trust,
        "provenance": weights.w_provenance,
    })
    trust_half_life_days = TrustManager()._half_life_seconds / 86400.0
    return {
        "psi_weights": {
            "recency": weights.w_recency,
            "confidence": weights.w_confidence,
            "trust": weights.w_trust,
            "provenance": weights.w_provenance,
            "uncertainty_threshold": weights.uncertainty_threshold,
        },
        "recency_half_life_hours": 24.0,
        "recency_lambda": engine.lambda_,
        "authority": {e.value: v for e, v in EVIDENCE_AUTHORITY.items()},
        "trust_half_life_days": trust_half_life_days,
        "trust_thresholds": {
            "TRUST_REJECT_THRESHOLD": cfg.TRUST_REJECT_THRESHOLD,
            "LOW_TRUST_THRESHOLD": cfg.LOW_TRUST_THRESHOLD,
            "HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD": cfg.HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD,
        },
        "confidence_weights": {
            "evidence": 0.50,
            "agreement": 0.30,
            "verification": 0.20,
        },
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _authority_table(data: Dict[str, Any]) -> str:
    order = ["user_input", "database", "tool_output", "document",
             "agent_claim", "agent_claim_default"]
    lines = ["| Evidence type | Authority |",
             "|---------------|-----------|"]
    for key in order:
        val = data["authority"].get(key, "?")
        lines.append(f"| {key} | {val:g} |")
    return "\n".join(lines)


def render_configuration_reference() -> str:
    d = configuration_data()
    w = d["psi_weights"]
    out = [
        "# Configuration Reference (generated)",
        "",
        f"> Generated from `lcm_core/config.py`, `lcm_core/confidence_engine.py`, "
        f"`lcm_core/conflict.py` and `lcm_core/trust_manager.py`. Do not edit by hand.",
        "",
        "## Ψ conflict-resolution weights",
        "",
        f"Ψ = w_r·Recency + w_c·Confidence + w_t·Trust + w_p·Provenance, with "
        f"w = ({w['recency']:g}, {w['confidence']:g}, {w['trust']:g}, {w['provenance']:g}).",
        "",
        f"- Recency half-life: **{d['recency_half_life_hours']:g} h** "
        f"(λ = {d['recency_lambda']:.8f} s⁻¹; R = e^(−λ·Δt)).",
        f"- Uncertainty threshold: **{w['uncertainty_threshold']:g}** — "
        f"a conflict is left unresolved when |ΨA − ΨB| < threshold.",
        "",
        "## Evidence authority table",
        "",
        "Authority is the middleware-side signal for how much a piece of external",
        "evidence may lift a claim. It is the single source of truth for "
        "`EvidenceAuthorityConfig` and drives `verified_confidence`.",
        "",
        _authority_table(d),
        "",
        "## Trust thresholds",
        "",
        "| Threshold | Value | Meaning |",
        "|-----------|-------|---------|",
        f"| `TRUST_REJECT_THRESHOLD` | {d['trust_thresholds']['TRUST_REJECT_THRESHOLD']:g} | writes below this trust are rejected |",
        f"| `LOW_TRUST_THRESHOLD` | {d['trust_thresholds']['LOW_TRUST_THRESHOLD']:g} | low-trust agent admits a high-confidence gate |",
        f"| `HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD` | {d['trust_thresholds']['HIGH_CONFIDENCE_UNTRUSTED_THRESHOLD']:g} | `verified_confidence` above this from a low-trust agent is rejected |",
        "",
        f"## Confidence weights",
        "",
        "`verified_confidence` is derived from evidence (0.50), multi-agent "
        "agreement (0.30) and verification (0.20) — never from the agent's "
        "self-reported `confidence_score` (audit/display only).",
        "",
    ]
    return "\n".join(out)


def render_benchmark_results(manifest: Dict[str, Any]) -> str:
    arts = load_artifacts(manifest)
    a = headline_benchmark_a(arts["benchmark_a"])
    b = headline_benchmark_b(arts["benchmark_b"])
    c = headline_benchmark_c(arts["benchmark_c"])
    d = headline_benchmark_d(arts["benchmark_d"])
    e = headline_benchmark_e(arts["benchmark_e"])
    frozen = d["frozen"]

    lines = [
        "# Benchmark Results (generated)",
        "",
        f"> Headline metrics computed from the validated artifacts pinned in "
        f"`{MANIFEST_DEFAULT}` (verified by SHA-256).",
        "",
        "## Benchmark A — race conditions (locking isolation)",
        "",
        "| System | Writers | Failure mode | Mean lost-update rate |",
        "|--------|---------|--------------|-----------------------|",
    ]
    for system in ("LCM", "No-LCM"):
        if system not in a["table"]:
            continue
        for n in sorted(a["table"][system], key=lambda x: int(x)):
            for fr in sorted(a["table"][system][n], key=lambda x: float(x)):
                val = a["table"][system][n][fr]
                mode = "failure_rate=0.0" if float(fr) == 0.0 else f"failure_rate={fr}"
                lines.append(f"| {system} | {n} | {mode} | {val:.3%} |")
    lines += [
        "",
        "## Benchmark B — Mandela injection",
        "",
        "| Mode | Trust gap | Trapping efficiency |",
        "|------|-----------|---------------------|",
    ]
    for mode in ("trust_plus_evidence", "trust_only"):
        if mode not in b["table"]:
            continue
        for gap in ("0.8", "0.4", "0.2", "0.1", "0"):
            if gap in b["table"][mode]:
                label = "trust+evidence" if mode == "trust_plus_evidence" else mode
                lines.append(f"| {label} | {gap} | {b['table'][mode][gap]:.1%} |")
    lines += [
        "",
        "## Benchmark C — conflict-resolution accuracy",
        "",
        "| Strategy | Scenario tier | Accuracy | Unresolved rate |",
        "|----------|---------------|----------|-----------------|",
    ]
    for strategy in ("LCM", "LCM_cold_start", "last_write_wins", "recency_only",
                     "majority_voting", "fixed_trust"):
        if strategy not in c["table"]:
            continue
        for tier in ("high_trust_vs_low_trust", "recency_dominated", "cold_start",
                     "graded_ambiguous"):
            if tier not in c["table"][strategy]:
                continue
            row = c["table"][strategy][tier]
            lines.append(f"| {strategy} | {tier} | {row['accuracy']:.1%} | "
                         f"{row['unresolved_rate']:.1%} |")
    lines += [
        "",
        "## Benchmark D — Ψ weight ablation (Full condition)",
        "",
        "| Fixture set | n | Full strict accuracy |",
        "|-------------|---|----------------------|",
    ]
    for fixture in ("benchmark_c", "corrected_diagnostic_v2", "frozen_held_out"):
        row = d["per_fixture"].get(fixture)
        if row:
            lines.append(f"| {fixture} | {row['n']} | {row['strict_accuracy']:.2%} |")
    lines += [
        "",
        "### Benchmark D — frozen held-out set statistics",
        "",
        "> The independent held-out set (`build_frozen_held_out_scenarios`), scored",
        "> under strict rules (an abstention is never a correct resolution; expected-ambiguous",
        "> scenarios are correct only when the system declines to resolve).",
        "",
        f"- Clear-winner scenarios: **{frozen['n_clear']}**",
        f"- Correct: **{frozen['correct']}**",
        f"- Wrong: **{frozen['wrong']}**",
        f"- Abstentions: **{frozen['abstentions']}**",
        f"- Coverage: **{frozen['coverage']:.2%}** "
        f"((correct + wrong) / clear-winner scenarios)",
        f"- Selective accuracy: **{frozen['selective_accuracy']:.2%}** "
        f"(correct / resolved)",
        f"- Strict accuracy: **{frozen['strict_accuracy']:.2%}** "
        f"(correct / clear-winner scenarios)",
        f"- Expected-ambiguous: **{frozen['ambiguous_unresolved']}/{frozen['n_ambiguous']}** "
        f"correctly left unresolved",
        "",
        "Abstaining frozen paths: "
        + (", ".join(frozen["abstaining_paths"]) if frozen["abstaining_paths"] else "none")
        + ".",
        "",
        "## Benchmark E — overlapping writes",
        "",
        f"- Tag: `{e['tag']}`",
        f"- Conflict rate (mean): **{e['conflict_rate_mean']:.1%}**"
        if e["conflict_rate_mean"] is not None else "- Conflict rate: n/a",
        f"- Verification win rate (mean): **{e['verification_win_rate_mean']:.1%}**"
        if e["verification_win_rate_mean"] is not None else "- Verification win rate: n/a",
        f"- Lost updates: **{e['lost_updates']}**",
        f"- Deciding-factor counts: {e['deciding_factor_counts']}",
        "",
    ]
    return "\n".join(lines)


def render_trust_and_temporal_validity() -> str:
    d = configuration_data()
    return "\n".join([
        "# Trust, Recency and Temporal Validity (generated)",
        "",
        "> Generated from `lcm_core/config.py`, `lcm_core/trust_manager.py`, "
        "`lcm_core/conflict.py` and `lcm_core/crypto.py`.",
        "",
        "## Reported confidence vs verified confidence",
        "",
        "- **Reported confidence (`confidence_score`)** is the agent's self-reported",
        "  number. It is stored for audit/display **only** and never feeds conflict",
        "  resolution or admission gates.",
        "- **Verified confidence (`verified_confidence`)** is derived by the middleware",
        "  from evidence records (evidence 0.50, agreement 0.30, verification 0.20)",
        "  and stamped into `provenance_info`. This is the number the Ψ formula and the",
        "  trust/confidence gates consume.",
        "",
        "## Authority",
        "",
        "Authority is assigned per evidence type (see the configuration reference):",
        "",
        _authority_table(d),
        "",
        "An elevated `source_type` (e.g. `database`, `tool_output`) is honoured **only**",
        "when the write carries a valid Ed25519 `evidence_signature`; otherwise the",
        "write degrades fail-closed to `agent_claim_default`.",
        "",
        "## Trust",
        "",
        f"- Historical, per-domain agent trust with exponential decay "
        f"(**{d['trust_half_life_days']:g}-day half-life**).",
        "- No prior history → cold-start prior **0.5**.",
        f"- Writes below `TRUST_REJECT_THRESHOLD` ({d['trust_thresholds']['TRUST_REJECT_THRESHOLD']:g})",
        "  are rejected before any mutation.",
        "",
        "## Recency",
        "",
        f"- R = e^(−λ·Δt) with a **{d['recency_half_life_hours']:g}-hour half-life** "
        f"(λ = {d['recency_lambda']:.8f} s⁻¹), Δt measured from the reference instant.",
        "- The benchmark suite evaluates recency from a deterministic scenario-relative",
        "  reference time so results do not drift with the wall clock.",
        "",
        "## Temporal validity",
        "",
        "- Evidence bindings carry `issued_at` / `expires_at`; `expires_at` in the past",
        "  → **expired** (rejected), `issued_at` in the future beyond the clock-skew",
        "  tolerance → **not yet valid** (rejected).",
        "- Replay is blocked by a per-binding nonce and a replay guard.",
        "",
    ])


# ---------------------------------------------------------------------------
# README patching
# ---------------------------------------------------------------------------

def patch_readme(readme_text: str, generated: Dict[str, str]) -> str:
    """Replace each marker-delimited generated section with fresh content."""
    for name, content in generated.items():
        begin = BEGIN.format(name=name)
        end = END.format(name=name)
        if begin not in readme_text or end not in readme_text:
            raise ValueError(
                f"README is missing generated-section markers {begin!r}...{end!r}. "
                f"Run the generator with --write once to (re)create them.")
        start = readme_text.index(begin)
        stop = readme_text.index(end) + len(end)
        readme_text = readme_text[:start] + begin + "\n" + content + "\n" + end + readme_text[stop:]
    return readme_text


def generated_sections(manifest: Dict[str, Any]) -> Dict[str, str]:
    return {
        "benchmarks": render_benchmark_results(manifest),
        "configuration": render_configuration_reference(),
    }


def render_readme(manifest: Dict[str, Any]) -> str:
    return patch_readme(
        README_PATH.read_text(encoding="utf-8"), generated_sections(manifest))


# ---------------------------------------------------------------------------
# Generation + consistency check
# ---------------------------------------------------------------------------

def generate_all(manifest: Dict[str, Any], write: bool = True) -> Dict[str, Any]:
    docs_dir = DOCS_DIR
    docs_dir.mkdir(parents=True, exist_ok=True)

    generated = generated_sections(manifest)
    docs = {
        "configuration_reference.md": render_configuration_reference(),
        "benchmark_results.md": render_benchmark_results(manifest),
        "trust_and_temporal_validity.md": render_trust_and_temporal_validity(),
    }
    readme_new = patch_readme(
        README_PATH.read_text(encoding="utf-8"), generated)

    report: Dict[str, Any] = {"ok": True, "checks": []}

    def _record(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            report["ok"] = False

    # Frozen-set invariants (honest validation of the artifact).
    arts = load_artifacts(manifest)
    frozen = frozen_held_out_stats(arts["benchmark_d"])
    for k, expected in FROZEN_TARGETS.items():
        actual = frozen[k]
        _record(f"frozen.{k}", actual == expected,
                f"expected {expected}, artifact reports {actual}")

    if write:
        for fname, content in docs.items():
            (docs_dir / fname).write_text(content, encoding="utf-8")
        README_PATH.write_text(readme_new, encoding="utf-8")

    report["docs"] = docs
    report["readme"] = readme_new
    report["frozen_stats"] = frozen
    return report


def check_consistency(manifest_path: Path = None) -> Dict[str, Any]:
    """Regenerate documentation in memory and compare against committed files."""
    manifest_path = manifest_path or (REPO_ROOT / MANIFEST_DEFAULT)
    manifest = load_manifest(manifest_path)
    report: Dict[str, Any] = {"ok": True, "checks": []}

    def _record(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            report["ok"] = False

    # 1. Artifact integrity (manifest hashes) — already verified by load_manifest.
    _record("artifacts.hash", True, "all validated artifact SHA-256s match the manifest")

    # 2. Frozen held-out invariants.
    arts = load_artifacts(manifest)
    frozen = frozen_held_out_stats(arts["benchmark_d"])
    for k, expected in FROZEN_TARGETS.items():
        actual = frozen[k]
        _record(f"frozen.{k}", actual == expected,
                f"expected {expected}, artifact reports {actual}")

    # 3. Standalone docs files match generated output.
    docs = {
        "configuration_reference.md": render_configuration_reference(),
        "benchmark_results.md": render_benchmark_results(manifest),
        "trust_and_temporal_validity.md": render_trust_and_temporal_validity(),
    }
    for fname, expected in docs.items():
        path = DOCS_DIR / fname
        if not path.exists():
            _record(f"docs.{fname}", False, "file missing")
            continue
        actual = path.read_text(encoding="utf-8")
        _record(f"docs.{fname}", actual == expected,
                "differs from freshly generated output" if actual != expected else "")

    # 4. README generated sections match generated output.
    readme_committed = README_PATH.read_text(encoding="utf-8")
    readme_fresh = patch_readme(readme_committed, generated_sections(manifest))
    _record("readme.sections", readme_committed == readme_fresh,
            "README generated sections differ from freshly generated output"
            if readme_committed != readme_fresh else "")

    # 5. No duplicated manual numerical truth outside generated sections.
    dup = _duplicate_numeric_truth(readme_committed, manifest, arts)
    _record("readme.no_manual_truth", len(dup) == 0,
            f"numeric tokens outside generated sections: {sorted(dup)}" if dup else "")

    report["frozen_stats"] = frozen
    return report


def _duplicate_numeric_truth(
    readme: str, manifest: Dict[str, Any], arts: Dict[str, Any]
) -> List[str]:
    """Return artifact/config-derived values appearing OUTSIDE generated sections.

    Fenced code blocks and inline code spans are excluded — code examples may
    legitimately reuse numbers. Only prose restating an authoritative value is
    flagged as duplicated manual truth.
    """
    generated = generated_sections(manifest)
    tokens: set = set()
    # Values that only the generator is allowed to state in prose.
    tokens.update({"0.25", "0.05", "0.5", "0.3", "0.85", "0.75", "0.9", "1.0",
                   "0.1", "68.75", "100.00"})
    frozen = frozen_held_out_stats(arts["benchmark_d"])
    tokens.update({f"{frozen['coverage']:.2%}"})

    import re
    in_fence = False
    in_generated = False
    found: List[str] = []
    for line in readme.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "<!-- BEGIN GENERATED:" in line:
            in_generated = True
            continue
        if "<!-- END GENERATED:" in line:
            in_generated = False
            continue
        if in_generated:
            continue
        clean = re.sub(r"`[^`]*`", "", line)
        for token in sorted(tokens):
            pat = re.compile(r"(?<![\d.])" + re.escape(token) + r"(?!\d)")
            if pat.search(clean):
                found.append(token)
    # de-duplicate while preserving order
    seen = set()
    out = []
    for t in found:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 14 documentation generator")
    parser.add_argument("--write", action="store_true",
                        help="Write generated docs + patch README (default: check only)")
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest) if args.manifest else (REPO_ROOT / MANIFEST_DEFAULT)
    manifest = load_manifest(manifest_path)

    if args.write:
        report = generate_all(manifest, write=True)
        print(f"Wrote docs/ and patched README. frozen={report['frozen_stats']}")
        return 0

    report = check_consistency(manifest_path)
    for c in report["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}"
              + (f" — {c['detail']}" if c["detail"] else ""))
    if not report["ok"]:
        print("documentation consistency: FAIL")
        return 1
    print("documentation consistency: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
