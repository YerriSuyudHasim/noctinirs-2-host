"""Differential pathlength factor, Scholkmann & Wolf 2013 (Eq. 7)."""

from __future__ import annotations

import math

FIT_RANGE_NM = (690.0, 832.0)
RECOMMENDED_AGE = (0.0, 70.0)


def scholkmann_wolf_dpf(wavelength_nm: float, age_years: float) -> tuple[float, tuple]:
    """Return (DPF, warnings). Raises on non-finite or out-of-domain input."""
    for v, what in ((wavelength_nm, "wavelength"), (age_years, "age")):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{what} must be a finite number")
    if wavelength_nm <= 0:
        raise ValueError("wavelength must be > 0")
    if age_years < 0:
        raise ValueError("age must be >= 0")
    dpf = (223.3 + 0.05624 * age_years ** 0.8493 - 5.723e-7 * wavelength_nm ** 3
           + 0.001245 * wavelength_nm ** 2 - 0.9025 * wavelength_nm)
    warns = []
    if not FIT_RANGE_NM[0] <= wavelength_nm <= FIT_RANGE_NM[1]:
        warns.append(f"{wavelength_nm:g} nm is outside the published fit range "
                     f"{FIT_RANGE_NM[0]:g}-{FIT_RANGE_NM[1]:g} nm (extrapolation, not validated)")
    if age_years > RECOMMENDED_AGE[1]:
        warns.append(f"age {age_years:g} is outside the recommended 0-70 years")
    return dpf, tuple(warns)
