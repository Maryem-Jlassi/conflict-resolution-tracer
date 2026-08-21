"""
pheme_provenance_common.py
Shared loader + frozen formula implementations for the PHEME/RumourEval
provenance evaluation (results/empirical_evaluation/provenance/evaluation).

Frozen mechanisms (PHEME_FROZEN_PROTOCOL.json) implemented here unchanged:
  - Psi = (R + C + T) / 3, w_R = w_C = w_T = 1/3
  - theta = 0.05
  - C = 0.50 * evidence_score + 0.25
      evidence_score = 0.25*f_log_followers + 0.30*f_url_presence
                     + 0.15*f_media_presence + 0.15*f_text_length_norm
                     + 0.15*f_log_engagement
      f_log_followers   = min(1.0, log10(followers_count + 10)/7.0)
      f_url_presence    = min(1.0, n_urls / 2.0)
      f_media_presence  = min(1.0, n_media / 1.0)
      f_text_length_norm= min(1.0, len(text) / 200.0)
      f_log_engagement  = min(1.0, log10(retweet_count + favorite_count + 2) / log10(100000))
  - R = exp(-lambda * (ref - created_at)), lambda = ln(2)/(7*86400)
  - T: prior-outcome trust (correct/total over strictly earlier labelled
      asserting tweets), cold-start prior 0.5.

NOTHING in this module is tuned against evaluation output. data_dir must be
underscore-prefixed cache (never a numbered artifact).
"""
import os
import re
import json
import math
import time
import hashlib
import datetime

LAMBDA = math.log(2.0) / (7.0 * 86400.0)
HALF_LIFE_SEC = 7 * 86400
THETA = 0.05
WS = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)

C_W_FOLLOWERS, C_W_URLS, C_W_MEDIA, C_W_TEXT, C_W_ENG = 0.25, 0.30, 0.15, 0.15, 0.15

STANCE_TO_VALUE = {"support": "true", "deny": "false"}
VALUE_TO_STANCE = {"true": "support", "false": "deny"}

EVENTS = [
    "charliehebdo", "ferguson", "ebola-essien", "germanwings-crash", "gurlitt",
    "ottawashooting", "prince-toronto", "putinmissing", "sydneysiege",
]

_RFC2822 = re.compile(r"[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) \+0000 (\d{4})")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def parse_rfc2822(s):
    m = _RFC2822.match(s or "")
    if not m:
        return None
    try:
        return datetime.datetime(int(m.group(6)), MONTHS[m.group(1)], int(m.group(2)),
                                 int(m.group(3)), int(m.group(4)), int(m.group(5)))
    except Exception:
        return None


def iso_from_dt(dt):
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def json_load(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def f_log_followers(followers):
    followers = followers if isinstance(followers, (int, float)) else 0
    return min(1.0, math.log10(followers + 10) / 7.0)


def f_url_presence(n_urls):
    n_urls = n_urls if isinstance(n_urls, (int, float)) else 0
    return min(1.0, n_urls / 2.0)


def f_media_presence(n_media):
    n_media = n_media if isinstance(n_media, (int, float)) else 0
    return min(1.0, n_media / 1.0)


def f_text_length_norm(text):
    if not isinstance(text, str):
        text = ""
    return min(1.0, len(text) / 200.0)


def f_log_engagement(rc, fc):
    rc = rc if isinstance(rc, (int, float)) else 0
    fc = fc if isinstance(fc, (int, float)) else 0
    return min(1.0, math.log10(rc + fc + 2) / math.log10(100000))


def evidence_score(tw):
    n_urls = len((tw.get("entities") or {}).get("urls", []))
    n_media = len((tw.get("entities") or {}).get("media", []))
    user = tw.get("user") or {}
    followers = user.get("followers_count", 0)
    rc = tw.get("retweet_count", 0)
    fc = tw.get("favorite_count", 0)
    text = tw.get("text", "")
    return (C_W_FOLLOWERS * f_log_followers(followers)
            + C_W_URLS * f_url_presence(n_urls)
            + C_W_MEDIA * f_media_presence(n_media)
            + C_W_TEXT * f_text_length_norm(text)
            + C_W_ENG * f_log_engagement(rc, fc))


def confidence(tw):
    return 0.50 * evidence_score(tw) + 0.25


def recency(created_dt, ref_dt):
    if created_dt is None or ref_dt is None:
        return 0.0
    dt = (ref_dt - created_dt).total_seconds()
    if dt < 0:
        dt = 0.0
    return math.exp(-LAMBDA * dt)


def trust_from_counts(correct, total):
    if total <= 0:
        return 0.5
    return correct / total


def psi(rc, c, trust):
    return (rc + c + trust) / 3.0


def parse_tree_children(node):
    """structure.json nested dict -> {child_id: {children}, ...} given parent dict."""
    children = {}
    for k, v in node.items():
        k = str(k)
        if isinstance(v, dict):
            children[k] = parse_tree_children(v)
        else:
            children[k] = {}
    return children


def walk_tree(node, parent_id, depth, parent_map, child_map):
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        kid = str(k)
        if parent_id is not None:
            parent_map[kid] = parent_id
        child_map.setdefault(kid, [])
        if parent_id is not None:
            child_map.setdefault(parent_id, []).append(kid)
        walk_tree(v, kid, depth + 1, parent_map, child_map)


class PHEMEData(object):
    """Loads the extracted PHEME archive + RumourEval labels into a tweet index."""

    def __init__(self, pheme_extracted_dir):
        self.root = pheme_extracted_dir
        self.threads = {}      # thread_id -> dict(annotation fields, event, truth flags)
        self.tweets = {}       # tweet_id -> record dict
        self.parent_map = {}   # tweet_id -> parent tweet_id (from structure.json)
        self.child_map = {}    # tweet_id -> [child tweet ids]
        self.root_to_thread = {}  # source tweet id -> thread id
        self.load()

    # -- loading ---------------------------------------------------------
    def load(self):
        base = os.path.join(self.root, "all-rnr-annotated-threads")
        t0 = time.time()
        for evname in EVENTS:
            evdir = os.path.join(base, evname + "-all-rnr-threads")
            if not os.path.isdir(evdir):
                continue
            for section in ("rumours", "non-rumours"):
                sdir = os.path.join(evdir, section)
                if not os.path.isdir(sdir):
                    continue
                for tid_entry in os.listdir(sdir):
                    if tid_entry.startswith(".") or tid_entry.endswith(".json"):
                        continue
                    tpath = os.path.join(sdir, tid_entry)
                    if not os.path.isdir(tpath):
                        continue
                    self.load_thread(evname, section, tid_entry, tpath)
        self.t_load = time.time() - t0

    def load_thread(self, event, section, tid, tpath):
        ann = None
        apath = os.path.join(tpath, "annotation.json")
        if os.path.exists(apath):
            try:
                ann = json_load(apath)
            except Exception:
                ann = None
        rec = {
            "thread_id": tid,
            "event": event,
            "section": section,
            "is_rumour": section == "rumours",
            "annotation": ann or {},
            "source_ids": [],
            "reaction_ids": [],
        }
        sd = os.path.join(tpath, "source-tweets")
        if os.path.isdir(sd):
            for f in os.listdir(sd):
                if f.startswith(".") or not f.endswith(".json"):
                    continue
                tw = json_load(os.path.join(sd, f))
                tid_s = str(tw.get("id_str") or tw.get("id"))
                self.add_tweet(tid_s, tw, event, tid, role="source")
                rec["source_ids"].append(tid_s)
                self.root_to_thread[tid_s] = tid
        rd = os.path.join(tpath, "reactions")
        if os.path.isdir(rd):
            for f in os.listdir(rd):
                if f.startswith(".") or not f.endswith(".json"):
                    continue
                tw = json_load(os.path.join(rd, f))
                tid_s = str(tw.get("id_str") or tw.get("id"))
                self.add_tweet(tid_s, tw, event, tid, role="reaction")
                rec["reaction_ids"].append(tid_s)
        # structure.json edges
        spath = os.path.join(tpath, "structure.json")
        if os.path.exists(spath):
            try:
                tree = json_load(spath)
            except Exception:
                tree = {}
            for root_id, subtree in tree.items():
                walk_tree(subtree, str(root_id), 1, self.parent_map, self.child_map)
        self.threads[tid] = rec

    def add_tweet(self, tid_s, tw, event, thread_id, role):
        dt = parse_rfc2822(tw.get("created_at"))
        rec = {
            "id": tid_s,
            "thread_id": thread_id,
            "event": event,
            "role": role,
            "user_id": str((tw.get("user") or {}).get("id_str") or (tw.get("user") or {}).get("id") or ""),
            "followers": (tw.get("user") or {}).get("followers_count", 0),
            "retweet_count": tw.get("retweet_count", 0),
            "favorite_count": tw.get("favorite_count", 0),
            "n_urls": len((tw.get("entities") or {}).get("urls", [])),
            "n_media": len((tw.get("entities") or {}).get("media", [])),
            "text": tw.get("text", ""),
            "created": dt,
            "created_iso": iso_from_dt(dt),
            "in_reply_to_status_id_str": tw.get("in_reply_to_status_id_str"),
            "in_reply_to_user_id_str": tw.get("in_reply_to_user_id_str"),
            "evidence": evidence_score(tw),
            "confidence": confidence(tw),
        }
        self.tweets[tid_s] = rec

    # -- convenience -----------------------------------------------------
    def thread_path(self, thread_id):
        t = self.threads.get(thread_id)
        if not t:
            return None
        return os.path.join(
            self.root, "all-rnr-annotated-threads",
            t["event"] + "-all-rnr-threads",
            t["section"], thread_id)

    def annotation(self, thread_id):
        t = self.threads.get(thread_id)
        return (t or {}).get("annotation") or {}


def load_rumoureval_labels(label_files):
    """label_files: list of paths to {tweet_id: stance}. Returns dict."""
    labels = {}
    for p in label_files:
        if p is None or not os.path.exists(p):
            continue
        d = json_load(p)
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, str):
                    labels[str(k)] = v
    return labels


def stream_sha256_hex(data_bytes):
    return hashlib.sha256(data_bytes).hexdigest()