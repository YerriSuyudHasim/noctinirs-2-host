"""Injected preview parameters. Every value set carries its source and status."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .filters import FilterConfig
from .dpf import scholkmann_wolf_dpf
from .quality import CONSTANTS
from .signal_quality import QualityConfig

UNVERIFIED = "unverified"
WAVELENGTHS = ("735", "850")  # nominal labels only; real LED wavelengths are not verified (D7)
# Board 1 (Owner decision 2026-11-13): the "8 mm" PD is ~3 mm from L1/L6 and its foam is peeled, so it
# carries leak light. It is no longer offered as a regressor. Long channels may still use both PDs.
SSR_MODES = ("off", "5mm")
SSR_METHODS = ("auto", "ols", "ridge")


def _pos(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    f = float(value)
    if not math.isfinite(f) or f <= 0:
        raise ValueError(f"{what} must be finite and > 0, got {value!r}")
    return f


@dataclass(frozen=True)
class ExtinctionTable:
    """Decadic molar extinction, M^-1 cm^-1, per nominal wavelength label."""

    eps: dict  # {"735": {"HbO": x, "HbR": y}, "850": {...}}
    source: str
    status: str = UNVERIFIED

    def __post_init__(self) -> None:
        for wl in WAVELENGTHS:
            row = self.eps.get(wl)
            if not isinstance(row, dict) or set(row) != {"HbO", "HbR"}:
                raise ValueError(f"extinction table needs HbO/HbR for {wl}")
            for k, v in row.items():
                _pos(v, f"eps[{wl}][{k}]")

    def to_metadata(self) -> dict:
        return {"eps_decadic_M-1cm-1": {w: dict(r) for w, r in self.eps.items()},
                "source": self.source, "status": self.status}


# Values carried over from the legacy UI as a PREVIEW default only (study F4).
# Secondary source, nominal wavelengths: unverified, not authoritative.
LEGACY_PREVIEW_EXTINCTION = ExtinctionTable(
    {"735": {"HbO": 420.0, "HbR": 1510.0}, "850": {"HbO": 1060.0, "HbR": 800.0}},
    "legacy UI default (Moaveni 1970 via OMLC, secondary; 735 nm interpolated)")


@dataclass(frozen=True)
class DpfSetting:
    mode: str = "manual"  # "manual" | "age"
    manual: dict = field(default_factory=lambda: {"735": 6.0, "850": 6.0})
    age_years: float | None = None
    source: str = "legacy UI default 6/6 (manual)"
    status: str = UNVERIFIED


@dataclass(frozen=True)
class ResolvedDpf:
    values: dict
    mode: str
    warnings: tuple
    source: str
    status: str


def resolve_dpf(setting: DpfSetting) -> ResolvedDpf:
    if setting.mode == "manual":
        vals = {wl: _pos(setting.manual.get(wl), f"DPF {wl}") for wl in WAVELENGTHS}
        return ResolvedDpf(vals, "manual", (), setting.source, setting.status)
    if setting.mode != "age":
        raise ValueError("DPF mode must be 'manual' or 'age'")
    if setting.age_years is None:
        raise ValueError("age_years is required for age-based DPF")
    vals, warns = {}, []
    for wl in WAVELENGTHS:
        v, w = scholkmann_wolf_dpf(float(wl), setting.age_years)
        vals[wl] = v
        warns.extend(w)
    return ResolvedDpf(vals, "age", tuple(dict.fromkeys(warns)),
                       "Scholkmann & Wolf 2013, J Biomed Opt 18(10):105004, Eq. 7 "
                       "(nominal wavelengths; device applicability unverified)", UNVERIFIED)


@dataclass(frozen=True)
class PreviewConfig:
    extinction: ExtinctionTable = LEGACY_PREVIEW_EXTINCTION
    dpf: DpfSetting = field(default_factory=DpfSetting)
    off_window: str = "reported"
    baseline_s: float = 10.0
    min_baseline_samples: int = 3
    ssr_mode: str = "off"
    ssr_method: str = "auto"
    ridge_lambda: float = 1e-3
    ssr_window_s: float = 30.0
    ssr_min_samples: int = 30
    # Short-channel quality gate (a failing short is not used; the long channel is marked "SSR tidak diterapkan").
    ssr_min_snr: float = 100.0            # SNR_frame of the short, both wavelengths (provisional)
    ssr_gate_achromatic: bool = True
    ssr_gate_motion: bool = True
    ssr_correction_lowpass_hz: float = 0.3   # M1: only this slow part of the regression is subtracted
    quality: QualityConfig = field(default_factory=QualityConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)

    def validate(self) -> None:
        if self.off_window not in ("reported", "29-38"):
            raise ValueError("Unknown OFF window preset")
        if self.ssr_mode not in SSR_MODES:
            raise ValueError(f"ssr_mode must be one of {SSR_MODES}")
        if self.ssr_method not in SSR_METHODS:
            raise ValueError(f"ssr_method must be one of {SSR_METHODS}")
        _pos(self.baseline_s, "baseline_s")
        _pos(self.ssr_min_snr, "ssr_min_snr")
        _pos(self.ssr_correction_lowpass_hz, "ssr_correction_lowpass_hz")
        _pos(self.ssr_window_s, "ssr_window_s")
        if self.ridge_lambda < 0 or not math.isfinite(self.ridge_lambda):
            raise ValueError("ridge_lambda must be finite and >= 0")
        if self.min_baseline_samples < 1 or self.ssr_min_samples < 3:
            raise ValueError("sample minimums are too small")
        resolve_dpf(self.dpf)

    def to_metadata(self) -> dict:
        r = resolve_dpf(self.dpf)
        return {
            "label": "PREVIEW — belum tervalidasi, bukan data sains",
            "extinction": self.extinction.to_metadata(),
            "dpf": {"mode": r.mode, "values": dict(r.values), "age_years": self.dpf.age_years,
                    "source": r.source, "status": r.status, "warnings": list(r.warnings)},
            "baseline": {"estimator": "median", "duration_s": self.baseline_s,
                         "min_samples": self.min_baseline_samples},
            "short_separation": {"mode": self.ssr_mode, "method": self.ssr_method,
                                 "ridge_lambda": self.ridge_lambda, "window_s": self.ssr_window_s,
                                 "min_samples": self.ssr_min_samples, "domain": "OD", "causal": True,
                                 "window": "time-based (seconds); gaps shorten the history",
                                 "pairing": "per PD hemisphere (left PD -> left short, right PD -> right short), 5 mm PD only",
                                 "correction_lowpass_hz": self.ssr_correction_lowpass_hz, "cardiac_protection": True,
                                 "gate": {"min_snr_frame": self.ssr_min_snr, "achromatic": self.ssr_gate_achromatic,
                                          "motion": self.ssr_gate_motion,
                                          "also": "invalid, LOW_SIGNAL, LEAK_WARN, clipped/saturated"}},
            "signal_quality": self.quality.to_metadata(),
            "display_filters": self.filters.metadata(),
            "off_window": self.off_window,
            "quality": dict(CONSTANTS),
            "od": "ln(baseline/I), I = mean(ON_STABLE) - mean(OFF_STABLE) of the same slot",
            "output": "change relative to baseline (delta), micromolar",
        }
