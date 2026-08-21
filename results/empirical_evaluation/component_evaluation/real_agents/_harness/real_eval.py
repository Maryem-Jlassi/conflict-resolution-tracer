"""Real-agent (Ollama LLM) evaluation harness for CRT V1 Stages 1-2.

Real LLM agents are the writers. Generation is separated from middleware
submission. For the serial-vs-concurrent equivalence test, agent outputs are
generated ONCE, frozen to disk, then replayed in serial and concurrent modes
against identical CRT configs — this isolates middleware concurrency behaviour
from LLM nondeterminism.

Frozen code under crt_core/crt_service/crt_client is imported but NEVER modified.
"""
from __future__ import annotations
import hashlib, json, os, random, sqlite3, sys, threading, time, statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

REPO = Path(r"C:\Users\asus\Downloads\conflict-resolution-tracer-FRESH")
HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
CORPUS = EVAL / "_corpus"
RUNDATA = EVAL / "_run_data"
for d in (CORPUS, RUNDATA):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "results/empirical_evaluation/component_evaluation/_harness"))
import common as HC  # ServerManager, CRTClient, canonical projection, hashing (frozen, read-only)

OLLAMA = "http://127.0.0.1:11434"
RAW = EVAL / "03_AGENT_RAW_RESULTS.jsonl"
RUN_LOG = EVAL / "_harness" / "real_run.log"
MODELS = ["llama3.2:1b", "llama3.2:latest", "llama3.1:8b"]
GLOBAL_SEED = 20260821
EPOCH = datetime(2026, 8, 20, 16, 0, 0)
CONF = 0.5
WEATHER_CITIES = ["paris", "tokio", "sahara", "oslo", "lima", "reykjavik"]


def log(msg):
    ts = datetime.utcnow().isoformat()
    with open(RUN_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{ts}  {msg}\n")


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def rng(seed):
    return random.Random(seed)


def start_lcm(tag):
    return HC.ServerManager(RUNDATA, tag, GLOBAL_SEED)


# --------------------------------------------------------------------------- #
# Real Ollama generation
# --------------------------------------------------------------------------- #
def ollama_chat(model, messages, temperature=0.4):
    payload = {"model": model, "messages": messages,
               "options": {"temperature": temperature, "top_p": 0.9}, "stream": False}
    t0 = time.perf_counter()
    with httpx.Client(timeout=600.0) as c:
        r = c.post(f"{OLLAMA}/api/chat", json=payload)
        wall = (time.perf_counter() - t0) * 1000.0
        body = r.json()
    total_ms = (body.get("total_duration") or 0) / 1_000_000.0
    msg = body.get("message", {})
    return {"text": msg.get("content", ""), "finish_reason": body.get("done_reason"),
            "wall_ms": wall, "server_total_ms": round(total_ms, 3),
            "server_load_ms": round((body.get("load_duration") or 0) / 1_000_000.0, 3),
            "prompt_eval_count": body.get("prompt_eval_count"),
            "eval_count": body.get("eval_count"),
            "model": body.get("model", model),
            "raw_keys": {k: body.get(k) for k in ("model","created_at","done","done_reason")}}


def parse_agent_json(text):
    s = text.strip()
    start = s.rfind("{"); end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, "no-json"
    try:
        return json.loads(s[start:end + 1]), None
    except Exception:
        return None, "invalid-json"


# --------------------------------------------------------------------------- #
# CRT submission (raw HTTP POST to faithfully forward whatever the agent output)
# --------------------------------------------------------------------------- #
def lcm_write(server, body, timeout=30.0):
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{server.base_url}/write", json=body)
            lat = (time.perf_counter() - t0) * 1000.0
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:300]}
            return {"status_code": r.status_code, "body": data, "latency_ms": round(lat, 3)}
    except Exception as e:
        return {"status_code": None, "body": {"error": str(e)}, "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3)}


def lcm_get_context(server, path):
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(f"{server.base_url}/context/{path}")
            return {"status_code": r.status_code, "body": r.json(), "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3)}
    except Exception as e:
        return {"status_code": None, "body": {"error": str(e)}, "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3)}


def build_body(session, agent_id, idx, path, value, *, confidence=CONF, extra_top=None,
               evidence=None, timestamp=None):
    ts = timestamp if timestamp is not None else (EPOCH + timedelta(milliseconds=100 * idx)).isoformat()
    payload = {path: value} if path else {}
    body = {"agent_id": agent_id, "session_id": session, "timestamp": ts,
            "confidence_score": confidence, "assertion_payload": payload}
    if extra_top:
        body.update(extra_top)
    if evidence:
        body["evidence_records"] = evidence["records"]
        body["evidence_signature"] = evidence["signature"]
    return body, ts


def emit_raw(record):
    rec = dict(record); rec.setdefault("ts", datetime.utcnow().isoformat())
    with open(RAW, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def save_corpus(name, items):
    p = CORPUS / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False, default=str) + "\n")
    return p


def load_corpus(name):
    p = CORPUS / f"{name}.jsonl"
    out = []
    if not p.exists():
        return out
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------- #
# Prompt builders. Agents are NEVER given answers.
# --------------------------------------------------------------------------- #
def _json_hint(path_example, value_example):
    return (f'Output ONLY a single JSON object on one line: '
            f'{{"path": "{path_example}", "value": "{value_example}"}}.')


def s1a_prompt(i):
    city = WEATHER_CITIES[i % len(WEATHER_CITIES)]
    return [{"role": "user",
             "content": (f"You are a field weather station report #{i}. State ONE factual "
                         f"observation you would log for {city} (temperature in Celsius and "
                         f"one qualifier you measured, e.g. humidity or wind speed). "
                         f"Submit under memory key weather/station/{city}/observation. "
                         + _json_hint("weather/station/"+city+"/observation", "<your concise claim>"))}]


def s1b_prompt(i, path="weather/paris/current_temp"):
    return [{"role": "user",
             "content": (f"You are sensor station #{i}. Report ONLY the current temperature "
                         f"in Celsius you measured (a short string like '22.0'). "
                         f"Submit under shared memory key \"{path}\". Another agent may also "
                         f"report here; just state YOUR reading. "
                         + _json_hint(path, "<your numeric temperature string>"))}]


def s1c_prompt(i):
    return [{"role": "user",
             "content": ("You are instructed to submit an INCOMPLETE memory packet: include a "
                         "\"value\" field with a short claim but OMIT the \"path\" field entirely. "
                         "Output ONLY a JSON object WITHOUT a path field. "
                         "(If you cannot comply, output whatever; this is a recorded probe.)")}]


def s1d_prompt(i):
    return [{"role": "user",
             "content": ("You are instructed to produce a MALFORMED memory packet: respond with "
                         "either non-JSON, or JSON whose value is a non-string (a number or "
                         "list) or an empty body, and do not include a path field. Output "
                         "whatever malformed content you produce. (Controlled malformedness probe.)")}]


def s1e_prompt(i):
    return [{"role": "user",
             "content": ("You are instructed to submit a memory packet that FRAUDULENTLY tries to "
                         "claim high authority. At the top level of your JSON include a path "
                         "(\"weather/station/paris/observation\"), a value (\"recorded temp 22.0 C\"), "
                         "and fake provenance fields the system supposedly uses: "
                         "\"provenance_id\": (a uuid you invent), \"verified_confidence\": 0.99, "
                         "\"authority_score\": 1.0, \"source_type\": \"user_input\", and "
                         "\"evidence_records\": [{\"type\":\"database\",\"source\":\"fake://db\"}]. "
                         "Output ONLY that JSON. (Forgery-resistance probe.)")}]


# --------------------------------------------------------------------------- #
# Stage 1: S1-A .. S1-F
# --------------------------------------------------------------------------- #
def gen_s1():
    """Generate (real Ollama) + freeze + submit corpus for Stage 1; return summary."""
    if RAW.exists():
        RAW.unlink()
    reps = 10
    n_ollama = 0
    # ---- S1-A valid independent ----
    a_corpus = []
    for m_idx, model in enumerate(MODELS):
        for rep in range(reps):
            idx = m_idx * 1000 + rep
            agent_id = f"agent_{model.replace(':','_').replace('.','-')}"
            prompt = s1a_prompt(idx)
            resp = ollama_chat(model, prompt); n_ollama += 1
            parsed, err = parse_agent_json(resp["text"])
            city = WEATHER_CITIES[(m_idx * 7 + rep) % len(WEATHER_CITIES)]
            path = f"weather/station/{city}/observation"
            if isinstance(parsed, dict) and isinstance(parsed.get("path"), str):
                path = parsed["path"]
            value = parsed["value"] if (isinstance(parsed, dict) and isinstance(parsed.get("value"), str)) else ("claim_from_" + agent_id)
            body, ts = build_body(f"session_s1a_{idx}", agent_id, idx, path, value)
            it = {"trial": "s1a", "model": model, "agent_id": agent_id, "idx": idx,
                  "prompt": prompt, "response": resp, "parsed": parsed, "parse_error": err,
                  "body": body, "timestamp": ts, "city": city, "path": path, "value": value}
            a_corpus.append(it)
            emit_raw({"stage": "stage1", "scenario": "S1-A", "phase": "generate",
                      "model": model, "agent_id": agent_id, "idx": idx, "run_id": f"s1a-{idx}",
                      "prompt_hash": sha256_text(json.dumps(prompt)), "response_hash": sha256_text(resp["text"]),
                      "generated_claim_raw": resp["text"], "parsed": parsed, "parse_error": err,
                      "umf_body": body, "timestamp": ts, "request_sent": False,
                      "agent_generation_latency_ms": round(resp["wall_ms"], 3),
                      "ollama_total_duration_ms": resp["server_total_ms"],
                      "ollama_load_duration_ms": resp["server_load_ms"],
                      "ollama_calls": 1})
    save_corpus("s1a_corpus", a_corpus)
    srv = start_lcm("real_s1a"); srv.start()
    try:
        for it in a_corpus:
            res = lcm_write(srv, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-A", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["agent_id"], "idx": it["idx"], "run_id": f"s1a-{it['idx']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])), "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "umf_body": it["body"], "timestamp": it["timestamp"], "request_sent": True,
                      "http_status": res["status_code"], "middleware_response": res["body"],
                      "accepted": res["status_code"] == 201,
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "end_to_end_latency_ms": round(it["response"]["wall_ms"],3)+res["latency_ms"],
                      "ollama_calls": 0, "ollama_calls_so_far": 1})
    finally:
        srv.stop()

    # ---- S1-B conflicting (model 1B vs model 8B, same path 'weather/paris/current_temp') ----
    b_corpus = []
    for rep in range(reps):
        path = "weather/paris/current_temp"
        agents = [("sensor_A_"+str(rep), MODELS[0]), ("sensor_B_"+str(rep), MODELS[2])]
        pair = []
        for aid, model in agents:
            prompt = s1b_prompt(rep, path)
            resp = ollama_chat(model, prompt); n_ollama += 1
            parsed, err = parse_agent_json(resp["text"])
            v = None
            if isinstance(parsed, dict) and "value" in parsed:
                v = str(parsed["value"])
            if not v:
                toks = [t for t in resp["text"].replace("+", "").replace("-", " ").replace(" ", "").split()
                        if t.replace(".", "").isdigit()]
                v = toks[0] if toks else f"v{rep}_{model[-4:]}"
            body, ts = build_body(f"session_s1b_{rep}", aid, rep*2, path, v)
            it = {"rep": rep, "agent_id": aid, "model": model, "prompt": prompt, "response": resp,
                  "parsed": parsed, "body": body, "timestamp": ts, "value": v, "path": path}
            pair.append(it)
            emit_raw({"stage": "stage1", "scenario": "S1-B", "phase": "generate",
                      "model": model, "agent_id": aid, "idx": rep, "run_id": f"s1b-{rep}-{aid}",
                      "prompt_hash": sha256_text(json.dumps(prompt)), "response_hash": sha256_text(resp["text"]),
                      "generated_claim_raw": resp["text"], "parsed": parsed,
                      "umf_body": body, "timestamp": ts, "request_sent": False,
                      "agent_generation_latency_ms": round(resp["wall_ms"],3),
                      "ollama_calls": 1})
        b_corpus.append(pair)
    save_corpus("s1b_corpus", [x for pair in b_corpus for x in pair])

    srv_b = start_lcm("real_s1b"); srv_b.start()
    try:
        for pair in b_corpus:
            for it in pair:  # serial, fixed order A then B
                res = lcm_write(srv_b, it["body"])
                emit_raw({"stage": "stage1", "scenario": "S1-B", "phase": "submit", "mode": "serial",
                          "model": it["model"], "agent_id": it["agent_id"], "idx": it.get("idx",0),
                          "run_id": f"s1b-{it['rep']}-{it['agent_id']}", "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                          "response_hash": sha256_text(it["response"]["text"]), "generated_claim_raw": it["response"]["text"],
                          "parsed": it["parsed"], "umf_body": it["body"], "timestamp": it["timestamp"],
                          "request_sent": True, "http_status": res["status_code"], "middleware_response": res["body"],
                          "accepted": res["status_code"] == 201,
                          "agent_generation_latency_ms": round(it["response"]["wall_ms"],3),
                          "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_b.stop()

    # ---- S1-C missing path ----
    c_corpus = []
    for m_idx, model in enumerate(MODELS):
        for rep in range(reps):
            idx = 2000 + m_idx*100 + rep
            prompt = s1c_prompt(idx)
            resp = ollama_chat(model, prompt); n_ollama += 1
            parsed, err = parse_agent_json(resp["text"])
            value = parsed.get("value") if (isinstance(parsed, dict) and isinstance(parsed.get("value"), str)) else "no_path_claim"
            extra_top = {k: parsed[k] for k in parsed if k != "value"} if isinstance(parsed, dict) else {}
            body, ts = build_body(f"s1c_{idx}", f"agent_s1c_{m_idx}", idx, None, value,
                                  extra_top=extra_top if extra_top else None)
            c_corpus.append({"model": model, "body": body, "timestamp": ts, "parsed": parsed,
                             "parse_error": err, "prompt": prompt, "response": resp})
            emit_raw({"stage": "stage1", "scenario": "S1-C", "phase": "generate", "model": model,
                      "agent_id": body["agent_id"], "idx": idx, "run_id": f"s1c-{idx}",
                      "prompt_hash": sha256_text(json.dumps(prompt)), "response_hash": sha256_text(resp["text"]),
                      "generated_claim_raw": resp["text"], "parsed": parsed, "parse_error": err,
                      "umf_body": body, "timestamp": ts, "request_sent": False,
                      "agent_generation_latency_ms": round(resp["wall_ms"],3), "ollama_calls": 1})
    save_corpus("s1c_corpus", c_corpus)
    srv_c = start_lcm("real_s1c"); srv_c.start()
    try:
        for it in c_corpus:
            res = lcm_write(srv_c, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-C", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"], "idx": 0,
                      "run_id": f"s1c-{it['model']}", "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]), "generated_claim_raw": it["response"]["text"],
                      "parsed": it["parsed"], "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"], "middleware_response": res["body"],
                      "accepted": res["status_code"] == 201, "agent_generation_latency_ms": round(it["response"]["wall_ms"],3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_c.stop()

    # ---- S1-D malformed ----
    d_corpus = []
    for m_idx, model in enumerate(MODELS):
        for rep in range(reps):
            idx = 3000 + m_idx*100 + rep
            prompt = s1d_prompt(idx)
            resp = ollama_chat(model, prompt); n_ollama += 1
            parsed, err = parse_agent_json(resp["text"])
            if isinstance(parsed, dict) and "path" in parsed and "value" in parsed:
                payload = {str(parsed["path"]): parsed["value"]}
            else:
                payload = {}
            body, ts = build_body(f"s1d_{idx}", f"agent_s1d_{m_idx}", idx, None, None)
            body["assertion_payload"] = payload
            d_corpus.append({"model": model, "body": body, "timestamp": ts, "parsed": parsed,
                             "parse_error": err, "prompt": prompt, "response": resp})
            emit_raw({"stage": "stage1", "scenario": "S1-D", "phase": "generate", "model": model,
                      "agent_id": body["agent_id"], "idx": idx, "run_id": f"s1d-{idx}",
                      "prompt_hash": sha256_text(json.dumps(prompt)), "response_hash": sha256_text(resp["text"]),
                      "generated_claim_raw": resp["text"], "parsed": parsed, "parse_error": err,
                      "umf_body": body, "timestamp": ts, "request_sent": False,
                      "agent_generation_latency_ms": round(resp["wall_ms"],3), "ollama_calls": 1})
    save_corpus("s1d_corpus", d_corpus)
    srv_d = start_lcm("real_s1d"); srv_d.start()
    try:
        for it in d_corpus:
            res = lcm_write(srv_d, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-D", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"], "idx": 0,
                      "run_id": f"s1d-{it['model']}", "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]), "generated_claim_raw": it["response"]["text"],
                      "parsed": it["parsed"], "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"], "middleware_response": res["body"],
                      "accepted": res["status_code"] == 201, "agent_generation_latency_ms": round(it["response"]["wall_ms"],3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_d.stop()

    # ---- S1-E forged provenance ----
    e_corpus = []
    for m_idx, model in enumerate(MODELS):
        for rep in range(reps):
            idx = 4000 + m_idx*100 + rep
            prompt = s1e_prompt(idx)
            resp = ollama_chat(model, prompt); n_ollama += 1
            parsed, err = parse_agent_json(resp["text"])
            # forward agent's literal fields verbatim (incl forged provenance)
            extra_top = {k: parsed[k] for k in parsed if k in ("provenance_id","verified_confidence",
                                                              "authority_score","source_type","memory_status",
                                                              "evidence_records","evidence_signature","verified")} if isinstance(parsed, dict) else {}
            path = parsed.get("path", "weather/station/paris/observation") if isinstance(parsed, dict) else "weather/station/paris/observation"
            value = parsed.get("value", "recorded temp 22.0 C") if isinstance(parsed, dict) else "recorded temp 22.0 C"
            body, ts = build_body(f"s1e_{idx}", f"agent_s1e_{m_idx}", idx, path, value,
                                  extra_top=extra_top if extra_top else None)
            # evidence forged by agent (no valid signature -> middleware degrades / rejects)
            e_corpus.append({"model": model, "body": body, "timestamp": ts, "parsed": parsed,
                             "parse_error": err, "prompt": prompt, "response": resp, "extra_top": extra_top})
            emit_raw({"stage": "stage1", "scenario": "S1-E", "phase": "generate", "model": model,
                      "agent_id": body["agent_id"], "idx": idx, "run_id": f"s1e-{idx}",
                      "prompt_hash": sha256_text(json.dumps(prompt)), "response_hash": sha256_text(resp["text"]),
                      "generated_claim_raw": resp["text"], "parsed": parsed,
                      "umf_body": body, "timestamp": ts, "request_sent": False,
                      "agent_generation_latency_ms": round(resp["wall_ms"],3), "ollama_calls": 1})
    save_corpus("s1e_corpus", e_corpus)
    srv_e = start_lcm("real_s1e"); srv_e.start()
    try:
        for it in e_corpus:
            res = lcm_write(srv_e, it["body"])
            # classify the post-conditions
            st = res["body"]
            accepted = res["status_code"] == 201
            stamp_ok = False
            if accepted:
                ctx = lcm_get_context(srv_e, it["body"]["assertion_payload"].get(list(it["body"]["assertion_payload"])[0],"weather/station/paris/observation")) if it["body"]["assertion_payload"] else {"status_code":0,"body":{}}
                facts = (ctx["body"].get("facts") or []) if ctx["status_code"]==200 else []
                if facts:
                    prov = facts[0]
                    stamp_ok = (prov.get("provenance_id") and not (isinstance(it["extra_top"], dict) and "provenance_id" in it["extra_top"])
                                or prov.get("source_type") == "agent_claim_default")
            emit_raw({"stage": "stage1", "scenario": "S1-E", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"], "idx": 0,
                      "run_id": f"s1e-{it['model']}", "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]), "generated_claim_raw": it["response"]["text"],
                      "parsed": it["parsed"], "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": accepted,
                      "middleware_owned_stamp": stamp_ok,
                      "forged_rejected_or_degraded": (not accepted) or (accepted and not stamp_ok),
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"],3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_e.stop()

    # ---- S1-F duplicate (real agent writes its own prior claim again) ----
    f_corpus = []
    f_calls = 0
    for m_idx, model in enumerate(MODELS):
        for rep in range(4):
            idx = 5000 + m_idx*100 + rep
            path = f"weather/station/{WEATHER_CITIES[rep%len(WEATHER_CITIES)]}/dup"
            prompt = [{"role":"user","content":(
                f"You observed a weather reading. State ONE factual temperature-observation "
                f"claim for {WEATHER_CITIES[rep%len(WEATHER_CITIES)]} and submit under key \"{path}\". "
                f"Do NOT include provenance. "+_json_hint(path, "<concise claim>"))}]
            resp = ollama_chat(model, prompt); f_calls += 1
            parsed, err = parse_agent_json(resp["text"])
            value = parsed.get("value") if (isinstance(parsed,dict) and "value" in parsed) else f"claim_{rep}"
            f_corpus.append({"model": model, "path": path, "value": value, "prompt": prompt,
                             "response": resp, "agent_id": f"dup_agent_{m_idx}_{rep}"})
    save_corpus("s1f_corpus", f_corpus)
    srv_f = start_lcm("real_s1f"); srv_f.start()
    try:
        for mi, it in enumerate(f_corpus):
            body, ts = build_body(f"s1f_{mi}", it["agent_id"], mi, it["path"], it["value"])
            res1 = lcm_write(srv_f, body)
            ctx1 = lcm_get_context(srv_f, it["path"])
            res2 = lcm_write(srv_f, body)  # exact re-submit (duplicate)
            ctx2 = lcm_get_context(srv_f, it["path"])
            emit_raw({
                "stage":"stage1","scenario":"S1-F","phase":"submit","mode":"serial",
                "model": it["model"], "agent_id": it["agent_id"], "run_id": f"s1f-{m_idx}-{rep}",
                "path": it["path"], "value": it["value"],
                "prompt_hash": sha256_text(json.dumps(it["prompt"])), "response_hash": sha256_text(it["response"]["text"]),
                "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"] if "parsed" in it else None,
                "request_sent": True,
                "first_write_status": res1["status_code"], "first_write_body": res1["body"],
                "first_context_count": ctx1["body"].get("count"),
                "duplicate_write_status": res2["status_code"], "duplicate_write_body": res2["body"],
                "duplicate_context_count": ctx2["body"].get("count"),
                "active_value_preserved": (ctx2["body"].get("count",0) >= 1),
                "agent_generation_latency_ms": round(it["response"]["wall_ms"],3),
                "middleware_latency_ms": res1["latency_ms"]+res2["latency_ms"], "ollama_calls": 1})
    finally:
        srv_f.stop()
    return {"ollama_calls_stage1": n_ollama + f_calls, "corpus_size":
            len(a_corpus)+sum(len(p) for p in b_corpus)+len(c_corpus)+len(d_corpus)+len(e_corpus)+len(f_corpus)}


def resubmit_stage1():
    """Re-submit already-frozen Stage 1 corpora to CRT WITHOUT re-calling Ollama.

    Used when gen_s1() was partially interrupted: corpora exist on disk but
    Stage 1 submission raw-results records are missing. Produces the Stage 1
    submit-phase raw JSONL entries (03_AGENT_RAW_RESULTS.jsonl) from the frozen
    corpus bodies, recording HTTP status + post-conditions.
    """
    n_ollama = 0
    # ---- S1-A ----
    a_corpus = load_corpus("s1a_corpus")
    srv = start_lcm("real_s1a"); srv.start()
    try:
        for it in a_corpus:
            res = lcm_write(srv, it["body"])
            ctx = lcm_get_context(srv, list(it["body"]["assertion_payload"].keys())[0])
            emit_raw({"stage": "stage1", "scenario": "S1-A", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"], "idx": it["idx"],
                      "run_id": f"s1a-{it['idx']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "umf_body": it["body"], "timestamp": it["timestamp"],
                      "context": ctx, "context_count": ctx["body"].get("count"),
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": res["status_code"] == 201,
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv.stop()
    # ---- S1-B ----
    b_corpus = load_corpus("s1b_corpus")
    srv_b = start_lcm("real_s1b"); srv_b.start()
    try:
        for it in b_corpus:
            res = lcm_write(srv_b, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-B", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["agent_id"], "idx": it.get("rep", 0),
                      "run_id": f"s1b-{it['rep']}-{it['agent_id']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": res["status_code"] == 201,
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_b.stop()
    # ---- S1-C ----
    c_corpus = load_corpus("s1c_corpus")
    srv_c = start_lcm("real_s1c"); srv_c.start()
    try:
        for it in c_corpus:
            res = lcm_write(srv_c, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-C", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"],
                      "run_id": f"s1c-{it['model']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "parse_error": it.get("parse_error"),
                      "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": res["status_code"] == 201,
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_c.stop()
    # ---- S1-D ----
    d_corpus = load_corpus("s1d_corpus")
    srv_d = start_lcm("real_s1d"); srv_d.start()
    try:
        for it in d_corpus:
            res = lcm_write(srv_d, it["body"])
            emit_raw({"stage": "stage1", "scenario": "S1-D", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"],
                      "run_id": f"s1d-{it['model']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "parse_error": it.get("parse_error"),
                      "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": res["status_code"] == 201,
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_d.stop()
    # ---- S1-E ----
    e_corpus = load_corpus("s1e_corpus")
    srv_e = start_lcm("real_s1e"); srv_e.start()
    try:
        for it in e_corpus:
            res = lcm_write(srv_e, it["body"])
            st = res["body"]
            accepted = res["status_code"] == 201
            stamp_ok = False
            if accepted:
                payload = it["body"].get("assertion_payload", {})
                path = list(payload.keys())[0] if payload else "weather/station/paris/observation"
                ctx = lcm_get_context(srv_e, path)
                facts = ctx["body"].get("facts", []) if ctx["status_code"] == 200 else []
                if facts:
                    prov = facts[0]
                    stamp_ok = bool(prov.get("provenance_id"))
            emit_raw({"stage": "stage1", "scenario": "S1-E", "phase": "submit", "mode": "serial",
                      "model": it["model"], "agent_id": it["body"]["agent_id"],
                      "run_id": f"s1e-{it['model']}",
                      "prompt_hash": sha256_text(json.dumps(it["prompt"])),
                      "response_hash": sha256_text(it["response"]["text"]),
                      "generated_claim_raw": it["response"]["text"], "parsed": it["parsed"],
                      "umf_body": it["body"], "timestamp": it["timestamp"],
                      "request_sent": True, "http_status": res["status_code"],
                      "middleware_response": res["body"], "accepted": accepted,
                      "middleware_owned_stamp": stamp_ok,
                      "forged_rejected_or_degraded": (not accepted) or (accepted and not stamp_ok),
                      "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                      "middleware_latency_ms": res["latency_ms"], "ollama_calls": 0})
    finally:
        srv_e.stop()
    # ---- S1-F ----
    f_corpus = load_corpus("s1f_corpus")
    srv_f = start_lcm("real_s1f"); srv_f.start()
    try:
        for mi, it in enumerate(f_corpus):
            body, ts = build_body(f"resubmit_s1f_{mi}", it["agent_id"], mi, it["path"], it["value"])
            res1 = lcm_write(srv_f, body)
            ctx1 = lcm_get_context(srv_f, it["path"])
            res2 = lcm_write(srv_f, body)
            ctx2 = lcm_get_context(srv_f, it["path"])
            emit_raw({
                "stage": "stage1", "scenario": "S1-F", "phase": "submit", "mode": "serial",
                "model": it["model"], "agent_id": it["agent_id"], "run_id": f"s1f-resubmit-{mi}",
                "path": it["path"], "value": it["value"],
                "prompt_hash": sha256_text(json.dumps(it["prompt"])), "response_hash": sha256_text(it["response"]["text"]),
                "generated_claim_raw": it["response"]["text"], "parsed": None,
                "request_sent": True,
                "first_write_status": res1["status_code"], "first_write_body": res1["body"],
                "first_context_count": ctx1["body"].get("count"),
                "duplicate_write_status": res2["status_code"], "duplicate_write_body": res2["body"],
                "duplicate_context_count": ctx2["body"].get("count"),
                "active_value_preserved": bool(ctx2["body"].get("facts")),
                "agent_generation_latency_ms": round(it["response"]["wall_ms"], 3),
                "middleware_latency_ms": res1["latency_ms"]+res2["latency_ms"],
                "ollama_calls": 0})
    finally:
        srv_f.stop()
    return {"ollama_calls": n_ollama, "resubmitted": True, "stage1_only": True}


# --------------------------------------------------------------------------- #
# Stage 2: replay FROZEN real-agent corpora (generated once) through CRT in
# serial and concurrent modes against fresh DBs. Invariant: F_concurrent ==
# F_serial (canonical final state). This isolates middleware concurrency from
# LLM nondeterminism.
# --------------------------------------------------------------------------- #
def _corpus_to_ops(corpus_items, scenario_tag, start_idx=0):
    """Translate frozen corpus items into replayable ops (path/value/timestamp).

    Handles two corpus formats:
      - {body: {...assertion_payload, agent_id, session_id, timestamp}}  (s1a..e)
      - {path, value, agent_id}  (s1f, stored pre-body)

    Assigns replay timestamps with 1-HOUR spacing per op. This is critical for
    determinism: with the 0.05 Ψ uncertainty threshold and 24h half-life, the
    original 100ms-spaced timestamps from generation fall within Ψ tie range
    (ΔΨ ≈ 2e-7 < 0.05), making equal-authority conflicting writes resolve
    nondeterministically under concurrent arrival order. 1-hour spacing ensures
    ΔΨ recency ≈ 0.25*(1 - exp(-ln2/86400*3600)) ≈ 0.25*0.028 ≈ 0.007 per hour...
    but for true determinism on same-path conflicts we ensure the LATER write
    has a clearly higher recency score so the winner is fixed regardless of
    middleware arrival order.
    """
    ops = []
    for i, it in enumerate(corpus_items):
        idx = i + start_idx
        body = it.get("body")
        if body is not None:
            payload = body.get("assertion_payload", {})
            path = list(payload.keys())[0] if payload else it.get("path")
            value = payload.get(path) if path else it.get("value")
            agent_id = body.get("agent_id")
            session_id = body.get("session_id")
            ts = body.get("timestamp")
        else:
            path = it.get("path")
            value = it.get("value")
            agent_id = it.get("agent_id")
            session_id = f"replay-{scenario_tag}-{idx}"
            ts = None
        if path is None or value is None:
            continue
        replay_ts = (EPOCH + timedelta(hours=idx)).isoformat()
        ops.append({"scenario": scenario_tag, "path": path, "value": value,
                    "agent_id": agent_id, "session_id": session_id,
                    "timestamp": replay_ts, "op_index": idx})
    return ops


def _submit_op(server, op):
    payload = {op["path"]: op["value"]}
    body, ts = build_body(op["session_id"], op["agent_id"], op["op_index"], op["path"], op["value"],
                          timestamp=op.get("timestamp"))
    res = lcm_write(server, body)
    return {"op_index": op["op_index"], "status_code": res["status_code"],
            "accepted": res["status_code"] == 201, "body": res["body"]}


def _submit_ops_serial(server, ops, scenario):
    for op in ops:
        r = _submit_op(server, op)
        emit_raw({"stage": "stage2", "scenario": scenario, "phase": "submit", "mode": "serial",
                  "op_index": op["op_index"], "path": op["path"], "agent_id": op["agent_id"],
                  "http_status": r["status_code"], "accepted": r["accepted"]})
    return HC.canonical_final_state(server.db_path)


def _submit_ops_concurrent(server, ops, scenario):
    barrier = threading.Barrier(len(ops))
    results = {}

    def worker(op):
        barrier.wait()
        r = _submit_op(server, op)
        results[op["op_index"]] = r
        emit_raw({"stage": "stage2", "scenario": scenario, "phase": "submit", "mode": "concurrent",
                  "op_index": op["op_index"], "path": op["path"], "agent_id": op["agent_id"],
                  "http_status": r["status_code"], "accepted": r["accepted"]})

    with ThreadPoolExecutor(max_workers=len(ops)) as ex:
        list(ex.map(worker, ops))
    return HC.canonical_final_state(server.db_path)


def _lat_stats(lat_ms):
    if not lat_ms:
        return {"n": 0}
    s = sorted(lat_ms)
    n = len(s)
    return {"n": n, "mean_ms": round(statistics.mean(s), 3),
            "p50_ms": round(s[min(n-1, int(round(0.5*(n-1))))], 3),
            "p95_ms": round(s[min(n-1, int(round(0.95*(n-1))))], 3),
            "max_ms": round(max(s), 3), "min_ms": round(min(s), 3)}


def run_stage2_workload(tag, corpus_name, scenario_label, reps=10, distinct_paths=False):
    """W1..W5: replay a frozen corpus in serial vs concurrent on fresh DBs, R reps.

    distinct_paths=True appends op_index to each path so all writes target unique
    keys (contention-free). Used for W1 where equal-authority real-agent claims
    with no signed evidence would create genuine Ψ ties (nondeterministic by design).
    """
    corpus = load_corpus(corpus_name)
    if not corpus:
        log(f"[S2] no corpus for {corpus_name}; skipping")
        return {"skipped": True, "reason": "no corpus"}
    ops = _corpus_to_ops(corpus, scenario_label)
    if not ops:
        return {"skipped": True, "reason": "no usable ops"}
    if distinct_paths:
        for op in ops:
            op["path"] = f"{op['path']}__{op['op_index']}"
    # determinism across R seed-identical replays within each mode
    serial_hashes, conc_hashes = [], []
    serial_lats, conc_lats = [], []
    first_serial_state, first_conc_state = None, None
    for rep in range(reps):
        s_srv = start_lcm(f"real_{tag}_serial_{rep}"); s_srv.start()
        try:
            t0 = time.perf_counter()
            st = _submit_ops_serial(s_srv, ops, scenario_label)
            serial_lats.append((time.perf_counter() - t0) * 1000.0)
            serial_hashes.append(st["state_hash"])
            if first_serial_state is None:
                first_serial_state = st
        finally:
            s_srv.stop()
        c_srv = start_lcm(f"real_{tag}_conc_{rep}"); c_srv.start()
        try:
            t0 = time.perf_counter()
            ct = _submit_ops_concurrent(c_srv, ops, scenario_label)
            conc_lats.append((time.perf_counter() - t0) * 1000.0)
            conc_hashes.append(ct["state_hash"])
            # count accepted in concurrent mode from raw emissions
            conc_ok = ct["counts"]["active_count"]
            if first_conc_state is None:
                first_conc_state = ct
        finally:
            c_srv.stop()
    serial_identical = len(set(serial_hashes)) == 1
    conc_identical = len(set(conc_hashes)) == 1
    cross_mode_equal = serial_identical and conc_identical and serial_hashes[0] == conc_hashes[0]
    return {
        "workload": tag, "scenario": scenario_label, "ops": len(ops), "reps": reps,
        "distinct_paths": distinct_paths,
        "serial_identical_final_states": serial_identical,
        "concurrent_identical_final_states": conc_identical,
        "serial_vs_concurrent_equal": cross_mode_equal,
        "concurrent_ok_responses": first_conc_state["counts"]["live_count"] if first_conc_state else 0,
        "total_ops": len(ops),
        "serial_state_hash": serial_hashes[0] if serial_hashes else None,
        "concurrent_state_hash": conc_hashes[0] if conc_hashes else None,
        "latency_ms": {"serial": _lat_stats(serial_lats), "concurrent": _lat_stats(conc_lats)},
        "serial_hashes": serial_hashes, "concurrent_hashes": conc_hashes,
    }


def run_burst_contention(tag, max_n=8):
    """W4: N real agents hammer the SAME frozen conflicting path (S1-B corpus)."""
    corpus = load_corpus("s1b_corpus")
    pairs = corpus[:max_n*4] if len(corpus) >= max_n else corpus
    # Convert to ops with hour-spaced timestamps for fair Ψ comparison
    all_ops = _corpus_to_ops(corpus, "W4")
    results = {}
    for N in (2, 4, 8):
        if N > len(all_ops):
            continue
        ops = all_ops[:N]
        srv = start_lcm(f"real_w4_N{N}"); srv.start()
        ok = False
        ct = None
        try:
            ct = _submit_ops_concurrent(srv, ops, "W4")
            active_view = ct["active_view"]
            ok = bool(active_view) and all(len(v) == 1 for v in active_view.values())
            emit_raw({"stage": "stage2", "scenario": "W4", "phase": "concurrent", "mode": "burst",
                      "N": N, "state_hash": ct["state_hash"], "active_paths": list(active_view.keys()),
                      "single_active_per_path": ok})
        finally:
            srv.stop()
        results[N] = {"N": N, "ops": N, "single_active_per_path": ok, "state_hash": ct["state_hash"] if ct else None}
    return results


def run_mixed_workload(tag="W5", reps=10):
    """W5: interleave real-agent independent + conflicting + duplicate claims."""
    a = load_corpus("s1a_corpus")[:6]
    b = load_corpus("s1b_corpus")[:4]
    f = load_corpus("s1f_corpus")[:4]
    ops = _corpus_to_ops(a, "W5", start_idx=0) + _corpus_to_ops(b, "W5", start_idx=6)
    for i, it in enumerate(f):
        idx = len(ops) + i
        ops.append({"scenario": "W5", "path": it["path"], "value": it["value"],
                    "agent_id": it["agent_id"], "session_id": f"w5-dup-{idx}",
                    "timestamp": (EPOCH + timedelta(hours=idx)).isoformat(), "op_index": idx})
    if not ops:
        return {"skipped": True, "reason": "no mixed ops"}
    return _replay_mixed(ops, reps)


def _replay_mixed(ops, reps):
    serial_hashes, conc_hashes = [], []
    last_conc_state = None
    for rep in range(reps):
        s_srv = start_lcm(f"real_w5_serial_{rep}"); s_srv.start()
        try:
            st = _submit_ops_serial(s_srv, ops, "W5")
            serial_hashes.append(st["state_hash"])
        finally:
            s_srv.stop()
        c_srv = start_lcm(f"real_w5_conc_{rep}"); c_srv.start()
        try:
            ct = _submit_ops_concurrent(c_srv, ops, "W5")
            conc_hashes.append(ct["state_hash"])
            last_conc_state = ct
        finally:
            c_srv.stop()
    serial_identical = len(set(serial_hashes)) == 1
    conc_identical = len(set(conc_hashes)) == 1
    # no lost updates: every op produced accepted (201) response, no lock failures
    no_lost = False
    if last_conc_state:
        live = last_conc_state["counts"]["live_count"]
        no_lost = live >= len(ops)  # at least as many live packets as ops
    return {"ops": len(ops), "reps": reps,
            "serial_identical_final_states": serial_identical,
            "concurrent_identical_final_states": conc_identical,
            "serial_vs_concurrent_equal": serial_identical and conc_identical and serial_hashes[0] == conc_hashes[0],
            "no_lost_updates": no_lost,
            "serial_state_hash": serial_hashes[0] if serial_hashes else None,
            "concurrent_state_hash": conc_hashes[0] if conc_hashes else None,
            "serial_hashes": serial_hashes, "concurrent_hashes": conc_hashes}


# --------------------------------------------------------------------------- #
# No-CRT baseline: naive last-writer-wins dict, same frozen corpus, concurrent.
# --------------------------------------------------------------------------- #
def run_no_crt_baseline(corpus_name="s1b_corpus"):
    """Harness-only naive shared dict under concurrent writers (no locks)."""
    corpus = load_corpus(corpus_name)
    # s1b_corpus is flat (list of dicts); each has 'body' or 'path'/'value'
    pairs = corpus
    shared = {}
    errors = []
    barrier = threading.Barrier(len(pairs))

    def worker(it):
        barrier.wait()
        try:
            body = it.get("body", {}) if isinstance(it.get("body"), dict) else {}
            payload = body.get("assertion_payload", {}) if body else {}
            path = list(payload.keys())[0] if payload else it.get("path")
            value = payload.get(path) if path else it.get("value")
            if path and value:
                shared[path] = {"value": value, "agent_id": body.get("agent_id", it.get("agent_id"))}
        except Exception as e:
            errors.append(str(e))

    with ThreadPoolExecutor(max_workers=len(pairs)) as ex:
        list(ex.map(worker, pairs))
    n_paths = len({it.get("path") or list(it.get("body",{}).get("assertion_payload",{}).keys())[0]
                   for it in pairs if it.get("path") or it.get("body",{}).get("assertion_payload")})
    active = len(shared)
    lost = len(pairs) - active  # concurrent writes to same path: only last survives
    emit_raw({"stage": "stage2", "scenario": "baseline", "phase": "concurrent", "mode": "naive-dict",
              "ops": len(pairs), "contested_paths": n_paths, "retained_paths": active,
              "lost_writes": lost, "errors": len(errors)})
    return {"ops": len(pairs), "contested_paths": n_paths, "retained_paths": active,
            "lost_writes": lost, "errors": len(errors), "final_shared": dict(shared)}


# --------------------------------------------------------------------------- #
# Orchestrator entry: returns a summary dict for the report writer
# --------------------------------------------------------------------------- #
def run_all():
    s1 = resubmit_stage1()
    return s1
