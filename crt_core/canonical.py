"""
Canonical hashing utilities (Phase 7).

``repr()``-based hashing is not canonical: float/int/unicode repr can differ
across Python versions or platforms, and nested ordering is fragile. This
module provides a deterministic canonical encoding (RFC 8785-style JSON
canonicalization: byte-sorted keys, no insignificant whitespace, deterministic
float handling) used for evidence assertion hashes and memory content hashes.

Two packets that are semantically identical but were assembled in a different
key order hash to the SAME value; two genuinely different packets never do.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _canonicalize(value: Any) -> Any:
    """Recursively normalize a Python value for deterministic JSON output."""
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    return value


def canonical_json(value: Any) -> str:
    """
    RFC 8785-style canonical JSON string for a Python value.

    * Object keys are sorted (byte order).
    * No insignificant whitespace (compact separators).
    * ``int`` vs ``float`` is preserved (``1`` vs ``1.0``) so a type change is
      a tamper.
    * NaN / Infinity are encoded as the strings ``"NaN"``/``"Infinity"`` so the
      output is always reproducible JSON.
    """
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 bytes of the canonical JSON encoding."""
    return canonical_json(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """SHA-256 hex digest of the canonical encoding."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
