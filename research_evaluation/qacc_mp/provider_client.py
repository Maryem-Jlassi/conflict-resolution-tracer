"""Unified provider client for the QACC multi-provider source extraction.

Hard rules (anti-mock / audit):
  * No mocked or fabricated LLM output anywhere.
  * The JSON output contract is IDENTICAL across providers (common.SOURCE_SCHEMA).
  * Every call is fail-closed on model identity: the responded model metadata
    must match the pinned requested model, else the (case, source) is recorded
    as a FAILED extraction and is NEVER silently retried with a different
    provider.
  * Ollama digests are pinned before AND after each call (no model drift).
  * Attestation fields (request_hash, raw_response_hash, latency_ms,
    token/eval counts, timestamps) are recorded per call.
"""
from __future__ import annotations

import json
import os
import re
import time

from dotenv import load_dotenv

from . import common

load_dotenv(common.REPO_ROOT / ".env", override=False)


def _ollama_tags(endpoint: str, model: str, timeout: int = 15):
    import requests
    r = requests.get(f"{endpoint}/api/tags", timeout=timeout)
    r.raise_for_status()
    for m in r.json().get("models", []):
        if m.get("name") == model:
            return m
    return None


def _extract_json(text: str):
    """Parse provider response into the source schema object (or None)."""
    if not text:
        return None
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    start = t.find("{")
    if start != -1:
        try:
            return json.loads(t[start: t.rfind("}") + 1])
        except Exception:
            return None
    return None


def _validate_schema(obj):
    """True only if obj is a well-formed source-reading object."""
    if not isinstance(obj, dict):
        return False
    if obj.get("support_status") not in ("supported", "unsupported"):
        return False
    if not isinstance(obj.get("answer_candidate"), (str, type(None))):
        return False
    if not isinstance(obj.get("evidence_excerpt"), (str, type(None))):
        return False
    return True


def _gen_ollama(cfg):
    import requests
    model = cfg["model"]
    endpoint = cfg["endpoint"]
    pre = _ollama_tags(endpoint, model)
    if pre is None:
        raise RuntimeError(f"ollama model '{model}' not present in /api/tags before call")
    pre_digest = pre.get("digest")
    req = {
        "model": model,
        "prompt": cfg["prompt"],
        "stream": False,
        "options": {"temperature": cfg["temperature"], "num_predict": 240},
        "format": "json",
    }
    started = time.perf_counter()
    r = requests.post(f"{endpoint}/api/generate", json=req, timeout=90)
    dt_ms = (time.perf_counter() - started) * 1000.0
    r.raise_for_status()
    data = r.json()
    post = _ollama_tags(endpoint, model)
    post_digest = post.get("digest") if post else None
    if pre_digest != post_digest:
        raise RuntimeError(
            f"ollama model '{model}' digest drifted pre={pre_digest} post={post_digest}"
        )
    text = data.get("response", "")
    dur = data.get("total_duration")
    return {
        "text": text,
        "model_returned": data.get("model"),
        "latency_ms": (dur / 1e6) if dur else dt_ms,
        "input_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "digest_pre": pre_digest,
        "digest_post": post_digest,
        "metadata": {"eval_count": data.get("eval_count")},
    }


_MAX_RETRIES = 5  # bounded 429/connection backoff (cap; never unbounded)


def _groq_key_pool():
    """Collect every groq API key present in env (grok_api_key, grok2..N).

    Round-robin across all keys so concurrent bursts are spread over multiple
    keys/quotas instead of hammering one key's per-minute window.  Returns a
    list of (key) usable keys; empty if none configured.
    """
    varnames = sorted(
        v for v in os.environ if v.lower().startswith("grok") and v.lower().endswith("api_key")
    )
    return [os.environ[v] for v in varnames if os.environ.get(v)]


import threading as _threading
_groq_pool = _groq_key_pool()
_groq_pool_lock = _threading.Lock()
_groq_pool_idx = 0


def _next_groq_key():
    global _groq_pool_idx
    with _groq_pool_lock:
        k = _groq_pool[_groq_pool_idx % len(_groq_pool)]
        _groq_pool_idx += 1
        return k


def _call_with_retry(fn):
    """Call fn() retrying on transient 429/RateLimit/connection errors with backoff.

    Bounded (<= _MAX_RETRIES attempts) and provider-agnostic: fn() must raise on
    any fatal or non-rate-limit error.  This only re-calls the SAME provider; it
    never mocks output or silently substitutes a different provider (those remain
    hard fail-closed rules in extract_source).
    """
    import time as _t
    attempt = 0
    last_err = None
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - we inspect the error type below
            attempt += 1
            err_s = f"{type(e).__name__}: {e}"
            retryable = (
                "429" in err_s or "RateLimit" in err_s
                or "rate limit" in err_s.lower() or "Connection" in err_s
                or "Timeout" in err_s or isinstance(e, TimeoutError)
            )
            if not retryable or attempt > _MAX_RETRIES:
                raise
            last_err = e
            delay = min(2.0 * (2 ** (attempt - 1)), 60.0)  # 2,4,8,16,32... capped 60s
            _t.sleep(delay)
    # unreachable


def _gen_openai(cfg):
    from openai import OpenAI
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise RuntimeError(f"missing env {cfg['api_key_env']}")
    client = OpenAI(api_key=api_key)
    started = time.perf_counter()

    def _do():
        return client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": cfg["prompt"]}],
            temperature=cfg["temperature"],
            max_tokens=256,
        )

    resp = _call_with_retry(_do)
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = resp.usage
    return {
        "text": resp.choices[0].message.content or "",
        "model_returned": resp.model,
        "latency_ms": latency_ms,
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "digest_pre": None,
        "digest_post": None,
        "metadata": {"finish_reason": resp.choices[0].finish_reason},
    }


def _gen_groq(cfg):
    from groq import Groq
    if _groq_pool:
        api_key = _next_groq_key()
    else:
        api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise RuntimeError(f"missing env {cfg['api_key_env']}")
    client = Groq(api_key=api_key)
    started = time.perf_counter()

    def _do():
        return client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": cfg["prompt"]}],
            temperature=cfg["temperature"],
            max_tokens=256,
        )

    resp = _call_with_retry(_do)
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = resp.usage
    return {
        "text": resp.choices[0].message.content or "",
        "model_returned": resp.model,
        "latency_ms": latency_ms,
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "digest_pre": None,
        "digest_post": None,
        "metadata": {"finish_reason": resp.choices[0].finish_reason},
    }


_GENERATORS = {
    "ollama": _gen_ollama,
    "openai": _gen_openai,
    "groq": _gen_groq,
}
def extract_source(provider: str, case: dict, si: int, source: str):
    """Run ONE source-agent extraction for (provider, case, source).

    Returns a full attestation record (dict).  On any provider/identity/parse
    failure the record is returned with success=False and NO claim fabrication.
    """
    cfg = dict(common.PROVIDERS[provider])
    schema_text = json.dumps(common.SOURCE_SCHEMA, indent=2)
    question = case.get("question", "")
    context = case.get("contexts", [])[si] if si < len(case.get("contexts", [])) else ""
    prompt = common.SOURCE_PROMPT_TEMPLATE.format(
        question=question, source=source, context=context, schema=schema_text
    )
    cfg["prompt"] = prompt

    case_id = int(case["annotation_task_id"])
    request_hash = common.sha256_text(
        prompt + "|" + provider + "|" + common.PROVIDERS[provider]["model"]
    )
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    record = {
        "provider": provider,
        "model": common.PROVIDERS[provider]["model"],
        "case_id": case_id,
        "source_id": si,
        "source": source,
        "question": question,
        "request_hash": request_hash,
        "timestamp": now_iso,
        "success": False,
        "error": None,
        "model_mismatch": None,
        "raw_response": None,
        "raw_response_hash": None,
        "parse_status": None,
        "support_status": None,
        "answer_candidate": None,
        "evidence_excerpt": None,
        "source_type": common.classify_source_type(source),
        "authority_score": common.source_authority(source),
        # Confidence is NOT agent-reported in this design (see report): the
        # resolver's C-component is authority-derived and provider-blind.
        "self_reported_confidence": None,
        "confidence_used": False,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "digest_pre": None,
        "digest_post": None,
        "metadata": {},
    }

    try:
        gen = _GENERATORS[provider](cfg)
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        return record

    raw = gen.get("text", "")
    record["raw_response"] = raw
    record["raw_response_hash"] = common.sha256_text(raw)
    record["latency_ms"] = round(float(gen.get("latency_ms", 0.0)), 3)
    record["input_tokens"] = gen.get("input_tokens")
    record["output_tokens"] = gen.get("output_tokens")
    record["digest_pre"] = gen.get("digest_pre")
    record["digest_post"] = gen.get("digest_post")
    record["metadata"] = gen.get("metadata", {})

    # ---- fail-closed model-identity check -------------------------------
    returned = gen.get("model_returned")
    requested = common.PROVIDERS[provider]["model"]
    if returned is None or returned != requested:
        record["model_mismatch"] = f"requested={requested} returned={returned}"
        record["error"] = f"MODEL_IDENTITY_MISMATCH requested={requested} returned={returned}"
        return record

    # ---- parse (identical contract) ------------------------------------
    obj = _extract_json(raw)
    if not _validate_schema(obj):
        record["parse_status"] = "unparseable"
        record["error"] = "FAILED_JSON_PARSE (schema-mismatch or unparseable)"
        return record
    record["parse_status"] = "ok"
    record["success"] = True
    record["support_status"] = obj["support_status"]
    record["answer_candidate"] = obj["answer_candidate"]
    record["evidence_excerpt"] = obj["evidence_excerpt"]
    return record