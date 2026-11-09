"""Optical density, natural logarithm. OD = ln(reference / I). Not mOD, not log10."""

from __future__ import annotations

import math


def _pos_finite(v: object, what: str) -> float:
    if v is None or isinstance(v, (bool, str)) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number, got {v!r}")
    f = float(v)
    if not math.isfinite(f) or f <= 0:
        raise ValueError(f"{what} must be finite and > 0, got {v!r}")
    return f


def optical_density(intensity: float, reference: float) -> float:
    return math.log(_pos_finite(reference, "reference") / _pos_finite(intensity, "intensity"))
