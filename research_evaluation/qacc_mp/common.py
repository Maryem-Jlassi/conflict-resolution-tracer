"""Shared configuration and deterministic helpers for the QACC multi-provider run.

Everything here is provider-agnostic. The resolver-scored fields
(authority_score / confidence) are computed ONLY from ``source_type``,
never from provider identity.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_PATH = (
    REPO_ROOT
    / "qacc"
    / "raw"
    / "fff5a4cd4fbfb51fadea92a3aaf3226875d0bce5"
    / "repository"
    / "data"
    / "ConflictQA_Dataset.json"
)

COMPONENT_QACC_DIR = (
    REPO_ROOT / "results" / "empirical_evaluation" / "component_evaluation" / "qacc"
)
OUTPUT_DIR = Path(
    os.environ.get("QACC_OUTPUT_DIR", str(COMPONENT_QACC_DIR / "_frozen_assertions_500_initial"))
)
# Single-provider reference run from the prior task. We NEVER write here; it is
# only read (if present) for the extension report's comparison section.
SINGLE_PROVIDER_DIR = COMPONENT_QACC_DIR / "_frozen_assertions_500"


def _env_int(name: str, default: int) -> int:
    import os
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Task parameters (logged for reproducibility)
# ---------------------------------------------------------------------------
N_CASES = 500
SEED_CASES = 20260820      # deterministic 500-case selection
SEED_ASSIGN = 20260821     # deterministic source->provider assignment
SEED_AGREE = 20260822      # deterministic 30-source agreement subsample
N_AGREEMENT = 30           # Option B sub-check size (90 extra calls)
AGGREGATION_METHOD = "round_robin_stratified"
# Optional smoke override: QACC_MP_LIMIT=n runs only a bounded prefix of the
# selected cases (used for end-to-end validation, never for the full run).
SMOKE_LIMIT = _env_int("QACC_MP_LIMIT", 0)

# ---------------------------------------------------------------------------
# Providers (Step 1 configuration)
# ---------------------------------------------------------------------------
PROVIDERS: dict = {
    "ollama": {
        "provider": "ollama",
        "model": "llama3.2:latest",
        "endpoint": "http://localhost:11434",
        "temperature": 0.0,
        "api_key_env": None,
        "digest": None,          # pinned at call time via /api/tags
    },
    "openai": {
        "provider": "openai",
        "model": "gpt-4o-mini-2024-07-18",   # exact pinned version
        "api_key_env": "OPEN_AI_KEY",        # as present in repo .env
        "temperature": 0.0,
        "endpoint": None,
    },
    "groq": {
        "provider": "groq",
        "model": "qwen/qwen3.6-27b",
        "api_key_env": "grok_api_key",
        "temperature": 0.0,
        "endpoint": "https://api.groq.com/openai/v1",
    },
}
ASSIGNMENT_ORDER = ["ollama", "openai", "groq"]

# ---------------------------------------------------------------------------
# Source-reading output contract -- IDENTICAL across all providers.
# Matches smoke manifest ``qacc-smoke-private-selection/1.0`` source schema.
# ---------------------------------------------------------------------------
SOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "support_status": {"type": "string", "enum": ["supported", "unsupported"]},
        "answer_candidate": {"type": ["string", "null"]},
        "evidence_excerpt": {"type": ["string", "null"]},
    },
    "required": ["support_status", "answer_candidate", "evidence_excerpt"],
}

SOURCE_PROMPT_TEMPLATE = (
    "You are a source-reading agent. Use only the supplied context.\n"
    "Question: {question}\n"
    "Displayed source: {source}\n"
    "Context: {context}\n"
    "Return JSON matching the schema. If the context does not support a "
    "concise answer, set support_status to \"unsupported\" and answer_candidate "
    "to null. Do not use outside knowledge.\n"
    "Output exactly one JSON object, no prose, matching this schema:\n"
    "{schema}"
)
# ---------------------------------------------------------------------------
# Source-type authority (canonical, from crt_core/confidence_engine.py) and a
# deterministic domain -> source_type classifier. authority_score is a pure
# function of the SOURCE DOMAIN, never of the extracting provider.
# ---------------------------------------------------------------------------
SOURCE_TYPE_AUTHORITY = {
    "user_input": 1.0,
    "database": 0.9,
    "tool_output": 0.85,
    "document": 0.75,
    "agent_claim": 0.3,
}
DEFAULT_SOURCE_TYPE = "document"

_TOOL_DOMAINS = ("senate.gov", "data.gov", "api.", "registry.", "githubusercontent.com")
_USER_DOMAINS = ("twitter.com", "x.com", "facebook.com", "instagram.com")
_AGENT_CLAIM_HINTS = (
    "twitter.com", "x.com", "reddit.com", "quora.com", "quizlet.com",
    "stackexchange.com", "blogspot.com", "wordpress.com",
)


def classify_source_type(source: str) -> str:
    """Deterministic source_type for a QACC retrieved-context source.

    QACC sources are web documents (reference/news/social/forum).  We map a
    small set of clearly user-generated social/forum domains to ``agent_claim``
    (lower authority) so the resolver's C-component can differentiate evidence
    quality across sources - recording, not manufacturing, heterogeneity.
    Everything else defaults to ``document``.  Provider identity is NEVER an
    input to this function.
    """
    s = (source or "").lower().strip()
    if any(k in s for k in _TOOL_DOMAINS):
        return "tool_output"
    if any(k in s for k in _USER_DOMAINS):
        return "agent_claim"
    if any(k in s for k in _AGENT_CLAIM_HINTS):
        return "agent_claim"
    return DEFAULT_SOURCE_TYPE


def source_authority(source: str) -> float:
    """Provider-blind authority from source_type mapping."""
    return SOURCE_TYPE_AUTHORITY[classify_source_type(source)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def normalize_text(s: str) -> str:
    """Canonical ConflictQA normalization (articles/punc/space/case)."""
    if not s:
        return ""
    s = re.sub(r"\b(a|an|the)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"[\W_]+", " ", s)
    return " ".join(s.lower().split())


def answer_correct(candidate: str, gold: list) -> bool:
    """Exact OR mutual-substring match of a candidate against normalized gold."""
    if not candidate or not gold:
        return False
    cand = normalize_text(candidate)
    if not cand:
        return False
    norm = {normalize_text(g) for g in gold if g}
    if cand in norm:
        return True
    return any(norm_g and (cand in norm_g or norm_g in cand) for norm_g in norm)


def qacc_gold(case: dict) -> list:
    """Reference answers for a case: every non-null labeled answer string."""
    gold = []
    for key in ("firstAnswer", "secondAnswer", "thirdAnswer", "fourthAnswer",
                "correctAnswer"):
        v = case.get(key)
        if isinstance(v, str) and v and v.lower() not in ("nan", "none", "null"):
            gold.append(v)
    return gold


def load_dataset() -> list:
    with open(DATASET_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def select_cases(dataset: list, n: int = N_CASES) -> list:
    """Deterministic 500-case selection from the QACC test split."""
    test = [c for c in dataset if c.get("split") == "test"]
    test = sorted(test, key=lambda c: int(c["annotation_task_id"]))
    rng = random.Random(SEED_CASES)
    chosen = rng.sample(test, min(n, len(test)))
    return sorted(chosen, key=lambda c: int(c["annotation_task_id"]))


def assign_providers(cases: list) -> dict:
    """Seeded round-robin-stratified source->provider assignment (Option A).

    One provider per SOURCE.  Every (case, source) slot is placed in a seeded
    permutation that repeats the balanced provider multiset, so each provider
    receives an equal (within rounding) number of sources globally and there
    is no fixed in-case positional correlation with a provider.
    """
    slots = []
    for ci, c in enumerate(cases):
        for si in range(len(c.get("contexts", []))):
            slots.append((ci, si))

    n = len(slots)
    base, rem = divmod(n, len(ASSIGNMENT_ORDER))
    seq = []
    for p in ASSIGNMENT_ORDER:
        seq.extend([p] * base)
    for k in range(rem):
        seq.append(ASSIGNMENT_ORDER[k])

    rng = random.Random(SEED_ASSIGN)
    order = list(range(n))
    rng.shuffle(order)

    assign = {}
    for slot, prov in zip([slots[i] for i in order], seq):
        assign[slot] = prov
    return assign