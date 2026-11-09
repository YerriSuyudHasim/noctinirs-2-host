"""RAW ENGINEERING -> PREVIEW chain: slot ON/OFF -> I -> baseline -> OD -> (SSR) -> MBLL.

PREVIEW only. Channels that fail a check are INVALID and carry NaN (a gap), never 0.
Mapping (slot, AIN, source-detector, distance) comes from the Geometry object. While that
geometry is DRAFT the output says so (``geometry_status``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..geometry import Geometry
from ..wire.optical import RawPacket
from .baseline import MedianBaseline
from .mbll import mbll_delta
from .od import optical_density
from .params import WAVELENGTHS, PreviewConfig, resolve_dpf
from .shortsep import RollingRegressor
from .slots import SlotAssembler, SlotMeasurement

NAN = float("nan")
VALID, INVALID, BASELINE, SSR_WARMUP = "VALID", "INVALID", "BASELINE", "SSR_WARMUP"


@dataclass(frozen=True)
class ChannelPoint:
    name: str
    kind: str
    state: str
    reason: str = ""
    i735: float = NAN
    i850: float = NAN
    od735: float = NAN
    od850: float = NAN
    d_hbo: float = NAN
    d_hbr: float = NAN
    d_hbt: float = NAN
    ssr_applied: bool = False
    ssr_beta: tuple = ()

    @property
    def has_hb(self) -> bool:
        return self.state == VALID and not math.isnan(self.d_hbo)


@dataclass(frozen=True)
class FrameResult:
    t_s: float
    points: dict
    complete: bool
    gap_marker: str = ""  # non-empty: a deliberate gap (disconnect, link loss)


def _invalid(ch, reason, i735=NAN, i850=NAN):
    return ChannelPoint(ch.name, ch.kind, INVALID, reason, i735, i850)


class PreviewPipeline:
    def __init__(self, geometry: Geometry, config: PreviewConfig | None = None,
                 nominal_fs_hz: float = 10.0) -> None:
        self.geometry = geometry
        self.fs = float(nominal_fs_hz)
        self.assembler = SlotAssembler()
        self._cycle: dict[int, SlotMeasurement] = {}
        self._last_slot = -1
        self._t0_us: int | None = None
        self._last_host_us: int | None = None
        self.pending: list = []
        self.baseline_resets: list = []
        self.config_log: list = []
        self.frames = 0
        self.invalid_counts: dict = {}
        self._set_config(config or PreviewConfig(), 0.0, initial=True)

    # ---- configuration -------------------------------------------------
    def _set_config(self, config: PreviewConfig, t_s: float, *, initial=False) -> None:
        config.validate()
        old = None if initial else self.config
        self.config = config
        self.dpf = resolve_dpf(config.dpf)
        if initial or (old.baseline_s, old.min_baseline_samples) != (
                config.baseline_s, config.min_baseline_samples):
            self._reset_baselines(t_s, "initial" if initial else "baseline setting changed")
        if initial or (old.ssr_mode, old.ssr_method, old.ridge_lambda, old.ssr_window_s,
                       old.ssr_min_samples) != (config.ssr_mode, config.ssr_method,
                                                config.ridge_lambda, config.ssr_window_s,
                                                config.ssr_min_samples):
            self._build_regressors()
        self.config_log.append({"t_s": t_s, "config": config.to_metadata()})

    def configure(self, config: PreviewConfig) -> None:
        """Apply live settings. DPF/extinction act on the next frame; baseline or SSR
        changes reset what they touch. Every change is kept for provenance."""
        self._set_config(config, self._last_t if hasattr(self, "_last_t") else 0.0)

    def _reset_baselines(self, t_s: float, reason: str) -> None:
        c = self.config
        self._baselines = {(ch.name, wl): MedianBaseline(c.baseline_s, c.min_baseline_samples)
                           for ch in self.geometry.channels for wl in WAVELENGTHS}
        self.baseline_resets.append({"t_s": t_s, "reason": reason})

    def reset_baseline(self, reason: str = "operator reset") -> None:
        self._reset_baselines(getattr(self, "_last_t", 0.0), reason)
        self._build_regressors()

    def _short_for(self, ch) -> list:
        """Short channels (names) of the same hemisphere as the long channel's source."""
        side = {s.id: s.side for s in self.geometry.sources}[ch.source]
        want = {"5mm": [5.0], "8mm": [8.0], "both": [5.0, 8.0]}.get(self.config.ssr_mode, [])
        out = []
        for mm in want:
            for c in self.geometry.channels:
                if (c.kind == "short" and abs(c.sd_mm - mm) < 0.5
                        and {s.id: s.side for s in self.geometry.sources}[c.source] == side):
                    out.append(c.name)
        return out

    def _build_regressors(self) -> None:
        c = self.config
        self._regs: dict = {}
        self._reg_inputs: dict = {}
        if c.ssr_mode == "off":
            return
        window = max(c.ssr_min_samples, round(c.ssr_window_s * self.fs))
        for ch in self.geometry.channels:
            if ch.kind != "long":
                continue
            shorts = self._short_for(ch)
            if not shorts:
                continue
            self._reg_inputs[ch.name] = shorts
            for wl in WAVELENGTHS:
                self._regs[(ch.name, wl)] = RollingRegressor(
                    len(shorts), window, c.ssr_min_samples, c.ssr_method, c.ridge_lambda)

    # ---- input ---------------------------------------------------------
    def feed(self, packet: RawPacket, host_us: int) -> list:
        if self._t0_us is None:
            self._t0_us = host_us
        self._last_host_us = host_us
        out = []
        for m in self.assembler.feed(packet, host_us):
            out.extend(self._add_measurement(m))
        return out

    def note_gap(self, reason: str) -> FrameResult | None:
        """Disconnect or link loss: close pending data and return an explicit gap marker.

        Frames completed while closing are kept in ``self.pending`` for the caller."""
        out = []
        for m in self.assembler.lose_continuity():
            out.extend(self._add_measurement(m))
        out.extend(self._close_cycle())
        self.pending = out
        if self._t0_us is None or self._last_host_us is None:
            return None
        t = (self._last_host_us - self._t0_us) / 1e6 + 1.0 / self.fs
        return FrameResult(t, {ch.name: _invalid(ch, "DISCONNECT") for ch in self.geometry.channels},
                           False, reason)

    def flush(self) -> list:
        out = []
        for m in self.assembler.flush():
            out.extend(self._add_measurement(m))
        out.extend(self._close_cycle())
        return out

    def _add_measurement(self, m: SlotMeasurement) -> list:
        out = []
        if self._cycle and (m.slot <= self._last_slot or m.epoch != next(iter(self._cycle.values())).epoch):
            out.extend(self._close_cycle())
        self._cycle[m.slot] = m
        self._last_slot = m.slot
        return out

    def _close_cycle(self) -> list:
        if not self._cycle:
            return []
        cycle, self._cycle, self._last_slot = self._cycle, {}, -1
        return [self._process(cycle)]

    # ---- evaluation ----------------------------------------------------
    def _leak(self, m: SlotMeasurement) -> bool:
        """Negative control: a contralateral detector must show no ON-OFF step."""
        c = self.config
        for ain in range(4):
            if self.geometry.is_contralateral(m.slot, ain) and m.on_n and m.off_n:
                if m.on_mean(ain) - m.off_mean(ain) >= c.min_led_step_counts:
                    return True
        return False

    def _intensity(self, m: SlotMeasurement | None, ain: int):
        c = self.config
        if m is None:
            return None, "NO_SLOT"
        if m.partial or m.gap:
            return None, "GAP"
        if m.on_n < c.min_phase_samples or m.off_n < c.min_phase_samples:
            return None, "NO_ON_OFF"
        if m.saturated[ain]:
            return None, "SATURATED"
        if self._leak(m):
            return None, "LEAK_SUSPECT"
        i = m.on_mean(ain) - m.off_mean(ain)
        if not math.isfinite(i):
            return None, "NONFINITE"
        if i < c.min_led_step_counts:
            return None, "NO_LED_STEP"
        return i, ""

    def _process(self, cycle: dict) -> FrameResult:
        first = min(cycle.values(), key=lambda m: m.first_seq)
        t = (first.host_us - (first.host_us if self._t0_us is None else self._t0_us)) / 1e6
        self._last_t = t
        self.frames += 1
        geo = self.geometry
        stage: dict = {}  # name -> (state, reason, i735, i850, od735, od850)
        for ch in geo.channels:
            src = next(s for s in geo.sources if s.id == ch.source)
            vals, reasons = {}, []
            for wl in WAVELENGTHS:
                v, why = self._intensity(cycle.get(src.slots[wl]), ch.ain)
                vals[wl] = v
                if why:
                    reasons.append(f"{wl}: {why}")
            if reasons:
                self.invalid_counts[ch.name] = self.invalid_counts.get(ch.name, 0) + 1
                stage[ch.name] = (INVALID, "; ".join(reasons), NAN, NAN, NAN, NAN)
                continue
            for wl in WAVELENGTHS:
                self._baselines[(ch.name, wl)].push(t, vals[wl])
            b = [self._baselines[(ch.name, wl)] for wl in WAVELENGTHS]
            if not all(x.ready for x in b):
                stage[ch.name] = (BASELINE, f"baseline {b[0].collected} samples",
                                  vals["735"], vals["850"], NAN, NAN)
                continue
            stage[ch.name] = (VALID, "", vals["735"], vals["850"],
                              optical_density(vals["735"], b[0].value),
                              optical_density(vals["850"], b[1].value))
        points = {ch.name: self._finish(ch, stage) for ch in geo.channels}
        return FrameResult(t, points, len(cycle) == 20)

    def _finish(self, ch, stage: dict) -> ChannelPoint:
        state, reason, i1, i2, o1, o2 = stage[ch.name]
        if state != VALID:
            return ChannelPoint(ch.name, ch.kind, state, reason, i1, i2)
        od = {"735": o1, "850": o2}
        applied, beta = False, ()
        if self.config.ssr_mode != "off" and ch.kind == "long":
            shorts = self._reg_inputs.get(ch.name)
            if not shorts:
                return ChannelPoint(ch.name, ch.kind, INVALID, "NO_SHORT_CHANNEL", i1, i2)
            if any(stage[s][0] != VALID for s in shorts):
                return ChannelPoint(ch.name, ch.kind, INVALID, "NO_SHORT_REGRESSOR", i1, i2)
            new = {}
            for wl in WAVELENGTHS:
                x = tuple(stage[s][4 if wl == "735" else 5] for s in shorts)
                reg = self._regs[(ch.name, wl)]
                new[wl] = reg.push(od[wl], x)
                if wl == "735":
                    beta = tuple(reg.last_beta or ())
            if new["735"] is None or new["850"] is None:
                return ChannelPoint(ch.name, ch.kind, SSR_WARMUP, "short-separation warming up",
                                    i1, i2, o1, o2)
            od, applied = new, True
        try:
            hb = mbll_delta(od["735"], od["850"], ch.sd_mm, self.dpf, self.config.extinction)
        except ValueError as exc:
            return ChannelPoint(ch.name, ch.kind, INVALID, f"MBLL: {exc}", i1, i2, o1, o2)
        return ChannelPoint(ch.name, ch.kind, VALID, "", i1, i2, od["735"], od["850"],
                            hb.d_hbo_uM, hb.d_hbr_uM, hb.d_hbt_uM, applied, beta)

    # ---- provenance ----------------------------------------------------
    def provenance(self) -> dict:
        geo = self.geometry
        return {
            "label": "PREVIEW — belum tervalidasi, bukan data sains",
            "raw_file_unchanged": True,
            "geometry": {"sha256": geo.sha256, "geometry_map_id": geo.geometry_map_id,
                         "status": geo.status, "draft": geo.is_draft},
            "nominal_fs_hz": self.fs,
            "config_changes": self.config_log,
            "baseline_resets": self.baseline_resets,
            "frames_processed": self.frames,
            "invalid_frame_counts": dict(self.invalid_counts),
            "limits": [
                "ON/OFF come from reported scheduled phase classes (ON_STABLE=4, OFF_STABLE=7); "
                "the device does not verify LED state in RAW",
                "LED-step and leak thresholds are provisional host-side values, not measured",
                "wavelength labels 735/850, extinction, DPF and distances are unverified",
            ],
        }
