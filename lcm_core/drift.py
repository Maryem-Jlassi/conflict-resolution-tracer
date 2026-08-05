"""
Drift Detector — Semantic Path Drift Detection and Normalization.

LLMs frequently generate minor variations of memory keys:
  - patient.blood_type
  - patient.blood.type
  - patient/blood/type
  - patient_blood_type

This module provides path canonicalization and drift detection to resolve
conflicts across semantically equivalent memory paths.

NOTE: This module is EXPERIMENTAL and RESEARCH-ONLY. It is not on the WritePipeline
hot path and should not be used in production without further validation. It is
primarily demonstrated in Benchmark H.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class DriftDetector:
    """
    Detects and resolves semantic path drift in memory assertion keys.
    """

    def normalize_path(self, path: str) -> str:
        """
        Canonicalizes a path string into a unified dot-delimited key.

        Splits on dots, slashes, underscores, and hyphens to extract core tokens,
        then re-joins them with dot separators in lowercase.
        """
        if not path:
            return ""
        clean = path.strip().lower()
        tokens = [t for t in re.split(r"[./_\\-]+", clean) if t]
        return ".".join(tokens)

    def are_equivalent(self, path1: str, path2: str) -> bool:
        """Return True if two path strings are semantically equivalent under drift."""
        return self.normalize_path(path1) == self.normalize_path(path2)

    def extract_canonical_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a copy of assertion_payload with canonicalized path keys.
        """
        if not isinstance(payload, dict):
            return {"raw_claim": payload}
        normalized = {}
        for key, val in payload.items():
            norm_key = self.normalize_path(key)
            normalized[norm_key] = val
        return normalized
