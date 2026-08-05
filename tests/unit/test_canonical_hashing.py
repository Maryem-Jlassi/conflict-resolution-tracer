"""
Unit tests — Canonical hashing (Phase 7).

The previous ``repr()``-based content/assertion hashes were not canonical: dict
insertion order and Python runtime repr quirks could change the digest for
semantically identical claims. Phase 7 replaces them with an RFC 8785-style
canonical encoding that is order-independent, platform-stable, and still
tamper-evident.
"""

import hashlib
from datetime import datetime

import pytest

from lcm_core.canonical import canonical_bytes, canonical_json, canonical_sha256
from lcm_core.provenance import _compute_content_hash, validate_and_stamp
from lcm_core.crypto import _compute_assertion_hash
from agents.lcm_adapter import MemoryWrite

NOW = datetime(2026, 7, 14, 10, 0, 0)


class TestCanonicalEncoding:
    def test_order_independent(self):
        a = canonical_json({"a": 1, "b": 2, "c": 3})
        b = canonical_json({"c": 3, "a": 1, "b": 2})
        assert a == b

    def test_deterministic(self):
        for _ in range(5):
            assert canonical_json({"x": [1, {"y": "z"}], "k": "v"}) == \
                canonical_json({"k": "v", "x": [1, {"y": "z"}]})

    def test_nested_structures(self):
        payload = {"meta": {"deep": {"a": 1, "b": [1, 2, {"c": None}]}}, "v": "s"}
        assert canonical_json(payload) == canonical_json(payload)

    def test_int_float_distinction(self):
        """1 vs 1.0 is a tamper and must hash differently."""
        assert canonical_sha256({"v": 1}) != canonical_sha256({"v": 1.0})

    def test_bool_int_distinction(self):
        assert canonical_sha256({"v": True}) != canonical_sha256({"v": 1})

    def test_nan_and_infinity_are_reproducible(self):
        import math
        out1 = canonical_json({"v": float("nan")})
        out2 = canonical_json({"v": float("inf")})
        out3 = canonical_json({"v": float("-inf")})
        assert isinstance(out1, str) and isinstance(out2, str) and isinstance(out3, str)
        assert canonical_json({"v": float("nan")}) == out1

    def test_bytes_utf8(self):
        assert canonical_bytes({"k": "v"}) == canonical_json({"k": "v"}).encode("utf-8")

    def test_change_any_value_changes_hash(self):
        base = {"agent": "a", "payload": {"k": "v"}}
        for key, value in [("payload", {"k": "w"}), ("agent", "b")]:
            variant = dict(base)
            variant[key] = value
            assert canonical_sha256(variant) != canonical_sha256(base)


class TestProvenanceContentHash:
    def test_order_independent_content_hash(self):
        a = _compute_content_hash("agent_a", NOW, {"k1": "v1", "k2": "v2"})
        b = _compute_content_hash("agent_a", NOW, {"k2": "v2", "k1": "v1"})
        assert a == b

    def test_tamper_changes_content_hash(self):
        h1 = _compute_content_hash("agent_a", NOW, {"k": "v"})
        h2 = _compute_content_hash("agent_a", NOW, {"k": "V"})
        assert h1 != h2
        h3 = _compute_content_hash("agent_b", NOW, {"k": "v"})
        assert h1 != h3

    def test_stamped_content_hash_deterministic_across_order(self):
        def stamp(payload):
            return validate_and_stamp({
                "agent_id": "a", "session_id": "s", "timestamp": NOW,
                "confidence_score": 0.5, "assertion_payload": payload,
            })
        r1 = stamp({"k1": "v1", "k2": "v2"})
        r2 = stamp({"k2": "v2", "k1": "v1"})
        assert r1.content_hash == r2.content_hash


class TestAssertionHash:
    def test_order_independent(self):
        a = _compute_assertion_hash("agent_a", NOW.isoformat(), {"a": 1, "b": 2})
        b = _compute_assertion_hash("agent_a", NOW.isoformat(), {"b": 2, "a": 1})
        assert a == b

    def test_any_field_change_changes_hash(self):
        base = _compute_assertion_hash("agent_a", NOW.isoformat(), {"k": "v"})
        assert base != _compute_assertion_hash("agent_a", NOW.isoformat(), {"k": "V"})
        assert base != _compute_assertion_hash("agent_b", NOW.isoformat(), {"k": "v"})


class TestAdapterHashes:
    def test_adapter_content_hash_is_sha256(self):
        from agents.lcm_adapter import _content_hash, _assertion_hash
        w = MemoryWrite(agent_id="db", key="age", value=30,
                        source_type="database", timestamp=NOW)
        ch = _content_hash(w)
        ah = _assertion_hash(w)
        assert len(ch) == 64 and len(ah) == 64
        assert ch == hashlib.sha256(canonical_bytes(w.value)).hexdigest()
        assert ah == canonical_sha256({w.key: w.value})

    def test_adapter_hash_order_irrelevant_for_single_key(self):
        from agents.lcm_adapter import _assertion_hash
        w1 = MemoryWrite(agent_id="db", key="age", value={"x": 1, "y": 2},
                         source_type="database", timestamp=NOW)
        w2 = MemoryWrite(agent_id="db", key="age", value={"y": 2, "x": 1},
                         source_type="database", timestamp=NOW)
        assert _assertion_hash(w1) == _assertion_hash(w2)
