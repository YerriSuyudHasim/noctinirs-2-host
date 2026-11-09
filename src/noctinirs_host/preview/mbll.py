"""Two-wavelength MBLL, change relative to baseline. Parameters are injected."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .params import ExtinctionTable, ResolvedDpf

LN10 = math.log(10.0)


@dataclass(frozen=True)
class HbDelta:
    d_hbo_uM: float
    d_hbr_uM: float

    @property
    def d_hbt_uM(self) -> float:
        return self.d_hbo_uM + self.d_hbr_uM


def _finite(v: object, what: str) -> float:
    if v is None or isinstance(v, (bool, str)) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a finite number, got {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{what} must be finite, got {v!r}")
    return f


def mbll_delta(od735: float, od850: float, distance_mm: float, dpf: ResolvedDpf,
               ext: ExtinctionTable) -> HbDelta:
    """Solve OD_l = ln10 * (eHbO_l*dHbO + eHbR_l*dHbR) * d * DPF_l for dHbO, dHbR (uM)."""
    y1, y2 = _finite(od735, "od735"), _finite(od850, "od850")
    d_cm = _finite(distance_mm, "distance_mm")
    if d_cm <= 0:
        raise ValueError("distance_mm must be > 0")
    d_cm /= 10.0
    p1, p2 = d_cm * dpf.values["735"], d_cm * dpf.values["850"]
    a, b = ext.eps["735"]["HbO"] * LN10 * p1, ext.eps["735"]["HbR"] * LN10 * p1
    c, d = ext.eps["850"]["HbO"] * LN10 * p2, ext.eps["850"]["HbR"] * LN10 * p2
    det = a * d - b * c
    if not math.isfinite(det) or abs(det) < 1e-18:
        raise ValueError("extinction matrix is singular")
    return HbDelta((y1 * d - b * y2) / det * 1e6, (a * y2 - y1 * c) / det * 1e6)
