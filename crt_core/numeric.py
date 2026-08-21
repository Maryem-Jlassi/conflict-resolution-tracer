"""Canonical fixed-precision arithmetic for CRT conflict decisions."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Mapping

CANONICAL_PLACES = 12
CANONICAL_QUANTUM = Decimal("0.000000000001")
CANONICAL_ROUNDING = ROUND_HALF_EVEN


def decimal_value(value) -> Decimal:
    return Decimal(str(value))


def quantize_score(value) -> Decimal:
    return decimal_value(value).quantize(CANONICAL_QUANTUM, rounding=CANONICAL_ROUNDING)


def serialize_score(value) -> str:
    return format(quantize_score(value), f".{CANONICAL_PLACES}f")


def weighted_total(components: Mapping[str, object], weights: Mapping[str, object]) -> Decimal:
    total=Decimal("0")
    for name in components.keys():
        total += quantize_score(components[name]) * quantize_score(weights[name])
    return quantize_score(total)


def score_margin(existing, incoming) -> Decimal:
    return quantize_score(abs(quantize_score(incoming)-quantize_score(existing)))


def classify_scores(existing, incoming, threshold) -> str:
    margin=score_margin(existing,incoming); boundary=quantize_score(threshold)
    if boundary < 0: raise ValueError("uncertainty_threshold must be non-negative")
    if margin < boundary: return "unresolved"
    return "incoming" if quantize_score(incoming)>quantize_score(existing) else "existing"


def numeric_specification() -> dict:
    return {"component_decimal_places":CANONICAL_PLACES,"weighted_total_decimal_places":CANONICAL_PLACES,
        "margin_decimal_places":CANONICAL_PLACES,"rounding_mode":"ROUND_HALF_EVEN",
        "quantization":"components before weighting; weighted total after sum; margin before comparison",
        "threshold_operator":"margin < threshold","exact_equality":"resolve higher score",
        "exact_tie_positive_threshold":"unresolved","exact_tie_zero_threshold":"retain incumbent"}
