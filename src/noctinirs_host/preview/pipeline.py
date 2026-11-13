"""RAW ENGINEERING -> PREVIEW chain: slot ON/OFF -> I -> baseline -> OD -> (SSR) -> MBLL.

PREVIEW only. A channel is INVALID (NaN, drawn as a gap, never 0) only for: missing/partial data,
no ON or OFF samples, saturation, an ambient jump, I <= 0 or not finite, or a large leak.
Weaker problems are WARNINGS (LOW_SIGNAL, LEAK_WARN): the value is still shown and flagged.
When the short-separation regressor is not ready or its short channel is invalid, the long
channel is shown without correction and labelled UNCORRECTED, not hidden.

Mapping (slot, AIN, source-detector, distance) comes from the Geometry object. While that geometry
is DRAFT the output says so (``geometry_status``). Thresholds: see quality.py (host-chosen).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field, replace

from ..geometry import Geometry
from ..wire.optical import RawPacket
from . import quality as Q
from .filters import PreviewFilters, CausalFilter, FilterConfig, design
from .baseline import MedianBaseline
from .mbll import mbll_delta
from .od import optical_density
from .params import WAVELENGTHS, PreviewConfig, resolve_dpf
from .shortsep import RollingRegressor
from .signal_quality import (ACHROMATIC, ChannelMonitor, ChannelQuality, MotionDetector, TIER_NA,
                             tier_for)
from .slots import SlotAssembler, SlotMeasurement

NAN = float("nan")
VALID, INVALID, BASELINE, SSR_WARMUP = "VALID", "INVALID", "BASELINE", "SSR_WARMUP"
SSR_OFF, SSR_APPLIED, SSR_UNCORRECTED = "off", "applied", "UNCORRECTED"
LOW_SIGNAL, LEAK_WARN = "LOW_SIGNAL", "LEAK_WARN"


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
    warnings: tuple = ()
    ssr_state: str = SSR_OFF      # "off" | "applied" | "UNCORRECTED"
    ssr_reason: str = ""
    sigma735: float = NAN
    sigma850: float = NAN
    filter_warming: bool = False
    # Quality indicators (flag-only; see signal_quality.py). None = not assessable.
    sci: float | None = None
    cardiac_db: float | None = None
    r_hbo_hbr: float | None = None
    tier: str = TIER_NA
    motion_z: float | None = None

    @property
    def has_hb(self) -> bool:
        return self.state == VALID and not math.isnan(self.d_hbo)

    @property
    def snr735(self) -> float:
        return self.i735 / self.sigma735 if self.sigma735 and self.sigma735 > 0 else NAN

    @property
    def snr850(self) -> float:
        return self.i850 / self.sigma850 if self.sigma850 and self.sigma850 > 0 else NAN


@dataclass(frozen=True)
class FrameResult:
    t_s: float
    points: dict
    complete: bool
    gap_marker: str = ""  # non-empty: a deliberate gap (disconnect, link loss)
    motion: dict = field(default_factory=dict)   # hemisphere ("L"/"R") -> True while a motion marker is active


@dataclass
class _Ain:
    """Assessment of one AIN in one slot occurrence."""
    step: float | None = None
    sigma: float = 0.0
    sigma_known: bool = False
    invalid: str = ""
    low_signal: bool = False
    leak_warn: bool = False
    leak_invalid: bool = False


def _reason_code(reason: str) -> str:
    """First reason code of "735: SATURATED; 850: SATURATED" or "MBLL: ..." (for counting)."""
    first = reason.split(";")[0].strip()
    if first.startswith("MBLL"):
        return "MBLL"
    head, _, tail = first.partition(": ")
    return (tail if head.isdigit() else first).split(" ")[0]


def _invalid(ch, reason, i735=NAN, i850=NAN):
    return ChannelPoint(ch.name, ch.kind, INVALID, reason, i735, i850)


class PreviewPipeline:
    def __init__(self, geometry: Geometry, config: PreviewConfig | None = None,
                 nominal_fs_hz: float = 10.0, *, calibration_headroom: float | None = None) -> None:
        self.geometry = geometry
        self._calibration_safety = tuple(geometry.calibration_safety_targets(slot) for slot in range(20))
        self.fs = float(nominal_fs_hz)
        self.calibration_headroom = calibration_headroom
        self.headroom_exceeded = False
        self.assembler = SlotAssembler()
        self._cycle: dict[int, SlotMeasurement] = {}
        self._last_slot = -1
        self._t0_us: int | None = None
        self._last_host_us: int | None = None
        self.pending: list = []
        self.baseline_resets: list = []
        self.config_log: list = []
        self.frames = 0
        # Counted after the final decision of each channel (after SSR and MBLL).
        self.invalid_counts: dict = {}
        self.invalid_reasons: dict = {}
        self.warning_counts: dict = {}
        self.ignored_pd_clipped: Counter = Counter()   # (slot, ain) -> occurrences at/over the headroom (M5)
        self.ssr_log: dict = {}
        self.uncorrected_counts: dict = {}
        self._stats: dict = {}
        self._loss = dict(packets=0, samples_seen=0, samples_missing=0, sample_jumps=0,
                          msg_missing=0, gap_before_packets=0)
        self._next_sample: int | None = None
        self._last_msg: int | None = None
        self._set_config(config or PreviewConfig(), 0.0, initial=True)
        self._build_quality()

    # ---- configuration -------------------------------------------------
    def _set_config(self, config: PreviewConfig, t_s: float, *, initial=False) -> None:
        config.validate()
        config = replace(config, filters=config.filters.cardiac_safe(self.fs))
        old = None if initial else self.config
        self.filters = PreviewFilters(config.filters, self.fs)
        self.config = config
        self.assembler.off_window = config.off_window
        self.dpf = resolve_dpf(config.dpf)
        if initial or (old.baseline_s, old.min_baseline_samples) != (
                config.baseline_s, config.min_baseline_samples):
            self._reset_baselines(t_s, "initial" if initial else "baseline setting changed")
        if initial or (old.ssr_mode, old.ssr_method, old.ridge_lambda, old.ssr_window_s,
                       old.ssr_min_samples, old.ssr_correction_lowpass_hz) != (config.ssr_mode, config.ssr_method,
                                                config.ridge_lambda, config.ssr_window_s,
                                                config.ssr_min_samples, config.ssr_correction_lowpass_hz):
            self._build_regressors()
        if not initial and (old.quality != config.quality or old.dpf != config.dpf or old.extinction != config.extinction):
            self._build_quality()
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
        self.filters.reset()
        self._reset_baselines(getattr(self, "_last_t", 0.0), reason)
        self._build_regressors()

    def _pd_side(self, ch) -> str:
        return next(d.side for d in self.geometry.detectors if d.pd == ch.pd)

    def _build_quality(self) -> None:
        cfg = self.config.quality
        self._monitors = {}
        for ch in self.geometry.channels:
            self._monitors[ch.name] = ChannelMonitor(self.fs, cfg, self._hb_fn(ch))
        sides = {"L": [], "R": []}
        for ch in self.geometry.channels:
            sides[self._pd_side(ch)].append(ch.name)
        self._motion = MotionDetector({k: v for k, v in sides.items() if v}, cfg)
        self._quality: dict = {}

    def _hb_fn(self, ch):
        def fn(a, b):
            hb = mbll_delta(a, b, ch.sd_mm, self.dpf, self.config.extinction)
            return hb.d_hbo_uM, hb.d_hbr_uM
        return fn

    def _short_for(self, ch) -> list:
        """Short channels (names) for a long channel: same hemisphere as the long channel's PD
        (left PD -> left short, right PD -> right short), 5 mm only. Not chosen by source side."""
        if self.config.ssr_mode != "5mm":
            return []
        side = self._pd_side(ch)
        return [c.name for c in self.geometry.channels
                if c.kind == "short" and abs(c.sd_mm - 5.0) < 0.5 and self._pd_side(c) == side]

    def _build_regressors(self) -> None:
        c = self.config
        self._regs: dict = {}
        self._ssr_correction = {}
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
                    len(shorts), window, c.ssr_min_samples, c.ssr_method, c.ridge_lambda,
                    window_s=c.ssr_window_s)

    # ---- input ---------------------------------------------------------
    def _count_loss(self, packet: RawPacket, msg_sequence: int | None) -> None:
        loss = self._loss
        loss["packets"] += 1
        loss["samples_seen"] += packet.sample_count
        loss["gap_before_packets"] += 1 if packet.gap_before else 0
        if self._next_sample is not None:
            missing = (packet.first_sample_sequence - self._next_sample) & 0xFFFFFFFF
            if 0 < missing < 0x80000000:  # a larger value is a reordered or repeated packet
                loss["samples_missing"] += missing
                loss["sample_jumps"] += 1
        self._next_sample = (packet.first_sample_sequence + packet.sample_count) & 0xFFFFFFFF
        if msg_sequence is not None:
            if self._last_msg is not None:
                gap = (msg_sequence - self._last_msg - 1) & 0xFFFFFFFF
                if 0 < gap < 0x80000000:
                    loss["msg_missing"] += gap
            self._last_msg = msg_sequence

    def loss_stats(self) -> dict:
        """Packet and sample loss seen by the host. ``gap_before`` flags come from the device
        and stay 0 when the loss happens on the network, so the host counts jumps itself.
        ``msg_missing`` is approximate: replies share the device message counter."""
        loss = dict(self._loss)
        total = loss["samples_seen"] + loss["samples_missing"]
        loss["samples_missing_percent"] = 100.0 * loss["samples_missing"] / total if total else 0.0
        return loss

    def feed(self, packet: RawPacket, host_us: int, msg_sequence: int | None = None) -> list:
        if self._t0_us is None:
            self._t0_us = host_us
        self._last_host_us = host_us
        self._count_loss(packet, msg_sequence)
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
        self.filters.reset()
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

    # ---- assessment of one slot occurrence -----------------------------------------
    def _assess_slot(self, m: SlotMeasurement) -> list:
        """Per-AIN assessment, then history update. Contralateral AINs are assessed too: they are
        the negative control for the leak decision."""
        res = [_Ain() for _ in range(4)]
        for a in range(4):
            r, st = res[a], self._stats.setdefault((m.slot, a), Q.SlotAinStats())
            if m.on_n < 1 or m.off_n < 1:
                r.invalid = "NO_ON_OFF"
                continue
            on, off = m.on_mean(a), m.off_mean(a)
            ambient = st.ambient_high(off)
            over_headroom = self.calibration_headroom is not None and (
                m.maximum[a] >= self.calibration_headroom or m.minimum[a] <= -self.calibration_headroom)
            self.headroom_exceeded |= over_headroom and a in self._calibration_safety[m.slot]
            if over_headroom and a not in self._calibration_safety[m.slot]:
                self.ignored_pd_clipped[(m.slot, a)] += 1   # M5: recorded, never changes the verdict
            if (m.saturated[a] or on >= Q.NEAR_RAIL_FRACTION * Q.ADC_RAIL
                    or (m.flat_on(a) and on >= Q.FLAT_TOP_MIN_FRACTION * Q.ADC_RAIL)):
                r.invalid = "SATURATED"
            elif over_headroom:
                r.invalid = "CLIP_DETECTED"
            elif off >= Q.NEAR_RAIL_FRACTION * Q.ADC_RAIL or ambient:
                r.invalid = "AMBIENT_HIGH"
            step = on - off
            if r.invalid:
                st.update(None, off, ambient)
                continue
            if not math.isfinite(step) or step <= 0:
                r.invalid = "NO_LED_STEP"      # ln is undefined: the only reason to drop a weak value
                r.step = step if math.isfinite(step) else None
                st.update(r.step, off, False)
                continue
            r.step = step
            st.update(step, off, False)
            within = m.within_sigma(a)
            between, known = st.step_sigma()
            r.sigma, r.sigma_known = max(within, between), known
            r.low_signal = step < Q.LOW_SIGNAL_SIGMA * r.sigma
        # leak: each AIN of the source's own side is judged against the opposite-side steps
        for a in range(4):
            if self.geometry.is_contralateral(m.slot, a) or res[a].step is None or res[a].invalid:
                continue
            for c in range(4):
                if not self.geometry.is_contralateral(m.slot, c):
                    continue
                rc = res[c]
                if rc.step is None or rc.step <= 0 or m.saturated[c] or rc.invalid:
                    continue
                if rc.step > Q.LEAK_WARN_SIGMA * rc.sigma:
                    res[a].leak_warn = True
                limit = max(Q.LEAK_INVALID_SIGMA * rc.sigma, Q.LEAK_INVALID_REL * res[a].step)
                if rc.sigma_known and rc.step > limit:
                    res[a].leak_invalid = True
        return res

    def _intensity(self, m: SlotMeasurement | None, assess: list | None, ain: int):
        """(I, reason, warnings, sigma) for one wavelength slot of a channel."""
        if m is None:
            return None, "NO_SLOT", (), NAN
        if m.partial or m.gap:
            return None, "GAP", (), NAN
        a = assess[ain]
        if a.invalid:
            return None, a.invalid, (), NAN
        if a.leak_invalid:
            return None, "LEAK_SUSPECT", (), NAN
        warns = tuple(w for w, on in ((LOW_SIGNAL, a.low_signal), (LEAK_WARN, a.leak_warn)) if on)
        return a.step, "", warns, a.sigma

    # ---- evaluation ----------------------------------------------------
    def _process(self, cycle: dict) -> FrameResult:
        first = min(cycle.values(), key=lambda m: m.first_seq)
        t = (first.host_us - (first.host_us if self._t0_us is None else self._t0_us)) / 1e6
        self._last_t = t
        self.frames += 1
        geo = self.geometry
        usable = {slot: self._assess_slot(m) for slot, m in cycle.items() if not (m.partial or m.gap)}
        stage: dict = {}  # name -> (state, reason, i735, i850, od735, od850, warnings, sigma735, sigma850)
        for ch in geo.channels:
            src = next(s for s in geo.sources if s.id == ch.source)
            vals, reasons, warns, sig = {}, [], [], {}
            for wl in WAVELENGTHS:
                slot = src.slots[wl]
                v, why, w, s = self._intensity(cycle.get(slot), usable.get(slot), ch.ain)
                vals[wl], sig[wl] = v, s
                warns.extend(w)
                if why:
                    reasons.append(f"{wl}: {why}")
            warns = tuple(dict.fromkeys(warns))
            if reasons:
                stage[ch.name] = (INVALID, "; ".join(reasons), NAN, NAN, NAN, NAN, (), NAN, NAN)
                continue
            for wl in WAVELENGTHS:
                self._baselines[(ch.name, wl)].push(t, vals[wl])
            b = [self._baselines[(ch.name, wl)] for wl in WAVELENGTHS]
            if not all(x.ready for x in b):
                stage[ch.name] = (BASELINE, f"baseline {b[0].collected} samples", vals["735"],
                                  vals["850"], NAN, NAN, warns, sig["735"], sig["850"])
                continue
            stage[ch.name] = (VALID, "", vals["735"], vals["850"],
                              optical_density(vals["735"], b[0].value),
                              optical_density(vals["850"], b[1].value), warns, sig["735"], sig["850"])
        self._assess_quality(t, stage)
        points = {ch.name: self._finish(ch, stage, t) for ch in geo.channels}
        self._count_final(points)
        complete = len(cycle) == 20
        # Keep filter history across incomplete cycles. Missing output remains NaN.
        points = self.filters.apply(points, t_s=t)
        return FrameResult(t, points, complete, motion=dict(self._motion.active))

    def _assess_quality(self, t: float, stage: dict) -> None:
        """Contact/cardiac/achromatic indicators and hemisphere motion markers (flags only)."""
        self._quality = {}
        z = {}
        for ch in self.geometry.channels:
            st = stage[ch.name]
            if st[0] != VALID:
                continue
            snr = [i / s if s and s > 0 else NAN for i, s in ((st[2], st[7]), (st[3], st[8]))]
            q = self._monitors[ch.name].push(t, st[4], st[5], snr[0], snr[1])
            finite = all(math.isfinite(x) for x in snr)
            self._quality[ch.name] = (q, min(snr) if finite else None, snr[0], snr[1])
            z[ch.name] = q.motion_z
        self._motion.update(t, z)

    def _short_gate(self, long_ch, shorts, stage) -> list:
        """[(short name, reason)] for shorts that must not be used as regressor. Empty = all usable."""
        cfg, bad = self.config, []
        for name in shorts:
            st = stage[name]
            q, snr, *_ = self._quality.get(name, (None, None))
            side = self._pd_side(next(c for c in self.geometry.channels if c.name == name))
            if st[0] != VALID:
                why = _reason_code(st[1]) if st[0] == INVALID else st[0]
            elif LOW_SIGNAL in st[6]:
                why = LOW_SIGNAL
            elif LEAK_WARN in st[6]:
                why = LEAK_WARN
            elif snr is None or snr < cfg.ssr_min_snr:
                why = "SNR_FRAME_LOW"
            elif cfg.ssr_gate_achromatic and q is not None and ACHROMATIC in q.flags:
                why = ACHROMATIC
            elif cfg.ssr_gate_motion and self._motion.active.get(side):
                why = "MOTION"
            else:
                continue
            bad.append((name, why))
        return bad

    def _count_final(self, points: dict) -> None:
        """Counts after SSR and MBLL, so every reason that hides or flags a channel is included."""
        for name, p in points.items():
            if p.state == INVALID:
                self.invalid_counts[name] = self.invalid_counts.get(name, 0) + 1
                self.invalid_reasons.setdefault(name, Counter())[_reason_code(p.reason)] += 1
            elif p.state == VALID:
                for w in p.warnings:
                    self.warning_counts.setdefault(name, Counter())[w] += 1
                if p.ssr_state == SSR_UNCORRECTED:
                    self.uncorrected_counts[name] = self.uncorrected_counts.get(name, 0) + 1

    def _finish(self, ch, stage: dict, t: float = 0.0) -> ChannelPoint:
        state, reason, i1, i2, o1, o2, warns, s1, s2 = stage[ch.name]
        common = dict(warnings=warns, sigma735=s1, sigma850=s2)
        if state != VALID:
            return ChannelPoint(ch.name, ch.kind, state, reason, i1, i2, **common)
        od = {"735": o1, "850": o2}
        applied, beta, ssr_state, ssr_reason = False, (), SSR_OFF, ""
        if self.config.ssr_mode != "off" and ch.kind == "long":
            shorts = self._reg_inputs.get(ch.name)
            # A short that fails the quality gate is not used; the long channel stays uncorrected.
            bad = self._short_gate(ch, shorts, stage) if shorts else []
            log = self.ssr_log.setdefault(ch.name, dict(shorts=list(shorts or ()), applied=0,
                                                        not_applied=Counter(), last_beta={}))
            if not shorts:
                ssr_state, ssr_reason = SSR_UNCORRECTED, "SSR tidak diterapkan: tidak ada short 5 mm di hemisfer ini"
                log["not_applied"]["NO_SHORT_IN_HEMISPHERE"] += 1
            elif bad:
                ssr_state = SSR_UNCORRECTED
                ssr_reason = "SSR tidak diterapkan: short " + ", ".join(f"{s} {why}" for s, why in bad)
                for _, why in bad:
                    log["not_applied"][why] += 1
            else:
                new = {}
                for wl in WAVELENGTHS:
                    x = tuple(stage[s][4 if wl == "735" else 5] for s in shorts)
                    reg = self._regs[(ch.name, wl)]
                    corrected = reg.push(od[wl], x, t)
                    if corrected is None:
                        new[wl] = None
                    else:
                        # Subtract only slow correction; never subtract the pulse band.
                        key = (ch.name, wl)
                        if key not in self._ssr_correction:
                            self._ssr_correction[key] = CausalFilter(design(FilterConfig(lowpass=True, low_cutoff=self.config.ssr_correction_lowpass_hz), self.fs))
                        new[wl] = od[wl] - self._ssr_correction[key].push(od[wl] - corrected)
                    if wl == "735":
                        beta = tuple(reg.last_beta or ())
                if new["735"] is None or new["850"] is None:
                    ssr_state, ssr_reason = SSR_UNCORRECTED, "SSR tidak diterapkan: regresi short sedang warming up"
                    log["not_applied"]["WARMING_UP"] += 1
                else:
                    od, applied, ssr_state = new, True, SSR_APPLIED
                    log["applied"] += 1
                    log["last_beta"] = {wl: list(self._regs[(ch.name, wl)].last_beta or ()) for wl in WAVELENGTHS}
        try:
            hb = mbll_delta(od["735"], od["850"], ch.sd_mm, self.dpf, self.config.extinction)
        except ValueError as exc:
            return ChannelPoint(ch.name, ch.kind, INVALID, f"MBLL: {exc}", i1, i2, o1, o2, **common)
        qinfo = self._quality.get(ch.name)
        extra = {}
        if qinfo is not None:
            q, snr_min = qinfo[0], qinfo[1]
            extra = dict(sci=q.sci, cardiac_db=q.cardiac_db, r_hbo_hbr=q.r_hbo_hbr, motion_z=q.motion_z,
                         tier=tier_for(snr_min, q, self.config.quality))
            if ACHROMATIC in q.flags:   # warning only: the channel is shown, never removed automatically
                common = dict(common, warnings=tuple(dict.fromkeys((*common["warnings"], ACHROMATIC))))
        return ChannelPoint(ch.name, ch.kind, VALID, "", i1, i2, od["735"], od["850"],
                            hb.d_hbo_uM, hb.d_hbr_uM, hb.d_hbt_uM, applied, beta,
                            ssr_state=ssr_state, ssr_reason=ssr_reason, **common, **extra)

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
            "invalid_reason_counts": {k: dict(v) for k, v in self.invalid_reasons.items()},
            "warning_counts": {k: dict(v) for k, v in self.warning_counts.items()},
            "uncorrected_frame_counts": dict(self.uncorrected_counts),
            "loss": self.loss_stats(),
            "quality_constants": dict(Q.CONSTANTS),
            "signal_quality": {"config": self.config.quality.to_metadata(),
                               "motion": self._motion.summary(),
                               "names": {"SNR_frame": "I/sigma per 10 Hz frame (ON-OFF counts)",
                                         "SNR_cal": "calibration step / spectral sigma (calibration only)"}},
            "short_separation": {k: dict(shorts=v["shorts"], applied_frames=v["applied"],
                                         not_applied_reasons=dict(v["not_applied"]), last_beta=v["last_beta"])
                                 for k, v in sorted(self.ssr_log.items())},
            "ignored_pd_clipped": {f"slot{slot}_ain{ain}": n for (slot, ain), n in sorted(self.ignored_pd_clipped.items())},
            "noise_sigma_counts": {f"slot{slot}_ain{ain}": round(st.last_sigma, 3)
                                   for (slot, ain), st in sorted(self._stats.items())
                                   if st.last_sigma is not None},
            "limits": [
                "ON/OFF come from reported scheduled phase classes (ON_STABLE=4, OFF_STABLE=7); "
                "the device does not verify LED state in RAW",
                "quality thresholds are host-chosen constants from one bench recording; the noise "
                "sigma is estimated online per slot and AIN",
                "wavelength labels 735/850, extinction, DPF and distances are unverified",
            ],
        }
