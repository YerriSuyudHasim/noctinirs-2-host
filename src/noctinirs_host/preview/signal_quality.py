"""Per-channel contact/cardiac/achromatic indicators and per-hemisphere motion markers (PREVIEW only).

Pure Python, causal, flag-only: nothing here changes RAW, OD or Hb values. Missing data stays missing
(windows are time-based, gaps are never interpolated). All thresholds are host-chosen and PROVISIONAL
(one subject, one session on 13 Nov 2026); they are configurable and saved with the session provenance.

Names (kept apart on purpose, see docs/preview-quality-metrics.md):
  SNR_frame  I / sigma per 10 Hz frame, ON-OFF counts (ChannelPoint.snr735 / snr850).
  CV_pct     100 / SNR_frame.
  SCI        zero-lag correlation of the two band-passed OD signals (cardiac band).
  cardiac_db 10 log10( variance 0.5-2.5 Hz / variance 3.0-4.5 Hz ) of the mean OD: a peak-to-floor
             proxy, NOT the QT-NIRS "peak spectral power" number.
  r_cardiac_hbo_hbr  correlation of dHbO and dHbR in the cardiac band. Close to +1 means the two
             wavelengths move identically (achromatic): a shunt or other non-haemodynamic path; warning only.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass

from .filters import CausalFilter, FilterConfig, design

ACHROMATIC = "ACHROMATIC_SUSPECT"
MOTION = "MOTION"
TIER_GOOD, TIER_NOISY, TIER_BAD, TIER_NA = "good", "noisy", "bad", "not_assessable"
CARDIAC_CLEAR, CARDIAC_WEAK, CARDIAC_FAINT = "clear", "weak", "faint"


def cardiac_class(db: float | None, cfg: "QualityConfig") -> str | None:
    """clear / weak / faint from cardiac_db; None while not assessable. Display only, never removes data."""
    if db is None or not math.isfinite(db):
        return None
    return CARDIAC_CLEAR if db > cfg.cardiac_db_min else CARDIAC_WEAK if db >= cfg.cardiac_db_weak else CARDIAC_FAINT


@dataclass(frozen=True)
class QualityConfig:
    """Thresholds. status: provisional (single subject). Configurable in code/JSON; no UI yet."""
    sci_min: float = 0.75              # value commonly quoted for SCI in the literature (perlu dicek)
    # cardiac_db (10 log10 of cardiac-band / 3-4.5 Hz variance of the mean OD, see the module docstring) is NOT
    # the QT-NIRS peak spectral power. On records 4-6 (13 Nov 2026) channels with a visible pulse have a median of
    # about 6-7 dB, so the earlier 10 dB gate was too high. Provisional single-subject values, see
    # docs/preview-quality-metrics.md: > cardiac_db_min "clear", cardiac_db_weak..cardiac_db_min "weak",
    # below cardiac_db_weak "faint" (hard to see).
    cardiac_db_min: float = 6.0
    cardiac_db_weak: float = 3.0
    achromatic_r_min: float = 0.8      # r_cardiac_hbo_hbr; record_4 CH8 0.99, healthy long channels <= 0.04
    snr_good: float = 100.0            # SNR_frame; host-chosen from record_3/4 (healthy 150-650)
    snr_noisy: float = 20.0            # below: bad (equals the calibration target of 20)
    cardiac_window_s: float = 10.0
    achromatic_window_s: float = 30.0
    min_coverage: float = 0.6          # fraction of expected frames in a window needed for a verdict
    motion_z: float = 6.0
    motion_min_snr: float = 20.0
    motion_lag_s: float = 15.0
    motion_scale_s: float = 60.0
    # Warm-up (tahap 2.1): a verdict needs motion_dev_min_s of trailing level and motion_scale_min_s of quiet
    # history. Below motion_scale_full_s the scale is inflated by sqrt(full/n), so the early verdicts are
    # more conservative instead of absent. The scale never drops below motion_noise_floor_k / SNR_frame.
    motion_dev_min_s: float = 5.0
    motion_scale_min_s: float = 10.0
    motion_scale_full_s: float = 30.0
    motion_noise_floor_k: float = 1.5
    motion_side_fraction: float = 0.3
    motion_min_channels: int = 2
    motion_persist: int = 5
    motion_merge_s: float = 2.0
    # Frames without enough eligible channels (burst loss, invalid frames) neither confirm nor cancel a
    # candidate marker for motion_hold_s; a marker that starts after a blind interval longer than
    # motion_gap_bridge_s starts at the last evaluated frame (the blind interval is marked, not corrected).
    motion_hold_s: float = 1.5
    motion_gap_bridge_s: float = 1.0

    def to_metadata(self) -> dict:
        return dict(asdict(self), status="provisional_single_subject_2026-11-13")


class _Pearson:
    """Time-windowed Pearson r with running sums (O(1) per push, exact rebuild on eviction drift)."""

    def __init__(self, span_s: float) -> None:
        self.span, self.q = span_s, deque()
        self.n = self.sa = self.sb = self.saa = self.sbb = self.sab = 0.0

    def push(self, t: float, a: float, b: float) -> None:
        self.q.append((t, a, b))
        self.n += 1; self.sa += a; self.sb += b
        self.saa += a * a; self.sbb += b * b; self.sab += a * b
        while self.q and t - self.q[0][0] > self.span:
            _, x, y = self.q.popleft()
            self.n -= 1; self.sa -= x; self.sb -= y
            self.saa -= x * x; self.sbb -= y * y; self.sab -= x * y
        if len(self.q) % 512 == 0:
            self._rebuild()

    def _rebuild(self) -> None:
        self.n = float(len(self.q))
        self.sa = sum(x for _, x, _ in self.q); self.sb = sum(y for _, _, y in self.q)
        self.saa = sum(x * x for _, x, _ in self.q); self.sbb = sum(y * y for _, _, y in self.q)
        self.sab = sum(x * y for _, x, y in self.q)

    def clear(self) -> None:
        self.q.clear(); self.n = self.sa = self.sb = self.saa = self.sbb = self.sab = 0.0

    def r(self, min_n: int) -> float | None:
        if self.n < min_n:
            return None
        va = self.saa - self.sa * self.sa / self.n
        vb = self.sbb - self.sb * self.sb / self.n
        if va <= 1e-18 or vb <= 1e-18:
            return None
        return max(-1.0, min(1.0, (self.sab - self.sa * self.sb / self.n) / math.sqrt(va * vb)))


class _Power:
    """Time-windowed sums of squares of two signals (cardiac band and noise band)."""

    def __init__(self, span_s: float) -> None:
        self.span, self.q, self.c, self.n_ = span_s, deque(), 0.0, 0.0

    def push(self, t: float, c: float, n: float) -> None:
        self.q.append((t, c * c, n * n)); self.c += c * c; self.n_ += n * n
        while self.q and t - self.q[0][0] > self.span:
            _, x, y = self.q.popleft(); self.c -= x; self.n_ -= y
        if len(self.q) % 512 == 0:
            self.c = sum(x for _, x, _ in self.q); self.n_ = sum(y for _, _, y in self.q)

    def clear(self) -> None:
        self.q.clear(); self.c = self.n_ = 0.0

    def db(self, min_n: int) -> float | None:
        if len(self.q) < min_n or self.c <= 0 or self.n_ <= 1e-18:
            return None
        return 10.0 * math.log10(self.c / self.n_)


@dataclass(frozen=True)
class ChannelQuality:
    sci: float | None = None
    cardiac_db: float | None = None
    r_hbo_hbr: float | None = None
    r_od: float | None = None
    flags: tuple = ()
    tier: str = TIER_NA
    contact_ok: bool | None = None
    motion_z: float | None = None
    cardiac_class: str | None = None


class ChannelMonitor:
    """Causal quality state of one channel. ``push`` is called once per VALID frame; ``gap`` on a hole."""

    def __init__(self, fs: float, config: QualityConfig, hb_fn=None) -> None:
        self.fs, self.cfg, self.hb_fn = fs, config, hb_fn
        self.cardiac_ok = fs > 6.0
        if self.cardiac_ok:
            self._bp = design(FilterConfig(bandpass=True, band_low=.5, band_high=2.5), fs)
            self._nz = design(FilterConfig(bandpass=True, band_low=3.0, band_high=min(4.5, fs / 2 - .3)), fs)
        self._last_t = None
        self.reset()

    def reset(self) -> None:
        if self.cardiac_ok:
            self._f = [CausalFilter(self._bp), CausalFilter(self._bp), CausalFilter(self._nz), CausalFilter(self._nz)]
        self._sci = _Pearson(self.cfg.cardiac_window_s)
        self._hb = _Pearson(self.cfg.achromatic_window_s)
        self._od = _Pearson(self.cfg.achromatic_window_s)
        self._pw = _Power(self.cfg.cardiac_window_s)
        self._warm = 0
        self._dev = deque(); self._odhist = ([], [])
        self._odq = (deque(), deque())
        self._scale = deque()
        self._dscale = deque()
        self._flagged_recent = False

    def gap(self, t: float) -> None:
        """A hole longer than 2 s restarts the band-pass state; the windows keep what they have."""
        if self._last_t is not None and t - self._last_t > 2.0 and self.cardiac_ok:
            for f in self._f:
                f.reset()
            self._warm = 0

    def _median(self, q) -> float:
        v = sorted(q)
        return v[len(v) // 2] if len(v) % 2 else .5 * (v[len(v) // 2 - 1] + v[len(v) // 2])

    def push(self, t: float, od735: float, od850: float, snr735: float, snr850: float) -> ChannelQuality:
        cfg = self.cfg
        self.gap(t)
        self._last_t = t
        z = self._motion(t, od735, od850, snr735, snr850)
        if not self.cardiac_ok:
            return ChannelQuality(flags=("CARDIAC_NOT_ASSESSABLE",), motion_z=z)
        a, b = self._f[0].push(od735), self._f[1].push(od850)
        na, nb = self._f[2].push(od735), self._f[3].push(od850)
        self._warm += 1
        if self._warm < round(3 * self.fs) or not all(map(math.isfinite, (a, b, na, nb))):
            return ChannelQuality(motion_z=z)
        self._sci.push(t, a, b)
        self._pw.push(t, .5 * (a + b), .5 * (na + nb))
        if self.hb_fn is not None:
            try:
                hbo, hbr = self.hb_fn(a, b)
                self._hb.push(t, hbo, hbr)
            except ValueError:
                pass
        self._od.push(t, a, b)
        need_c = round(cfg.min_coverage * cfg.cardiac_window_s * self.fs)
        need_a = round(cfg.min_coverage * cfg.achromatic_window_s * self.fs)
        sci, db = self._sci.r(need_c), self._pw.db(need_c)
        r_hb, r_od = self._hb.r(need_a), self._od.r(need_a)
        flags = ()
        if r_hb is not None and r_hb >= cfg.achromatic_r_min:
            flags = (ACHROMATIC,)
        contact = None if sci is None or db is None else (sci >= cfg.sci_min or db > cfg.cardiac_db_min)
        return ChannelQuality(sci, db, r_hb, r_od, flags, TIER_NA, contact, z, cardiac_class(db, cfg))

    # ---- motion: deviation from the trailing median, scaled by its own robust spread -------------
    def _scaled(self, store, t, value, floor=0.0) -> float | None:
        """value / robust spread (1.4826 MAD) of the quiet trailing history; only quiet values teach the scale.

        Warm-up: from motion_scale_min_s of history on, the spread is inflated by sqrt(full/n) until
        motion_scale_full_s is reached, and is never below ``floor`` (the frame noise in OD units)."""
        cfg, z = self.cfg, None
        n = len(store)
        if n >= round(cfg.motion_scale_min_s * self.fs):
            vals = sorted(v for _, v in store)
            med = vals[len(vals) // 2]
            mad = 1.4826 * sorted(abs(v - med) for v in vals)[len(vals) // 2]
            full = max(1.0, cfg.motion_scale_full_s * self.fs)
            scale = max(mad * max(1.0, math.sqrt(full / n)), floor)
            if scale > 0:
                z = value / scale
        if z is None or z < cfg.motion_z:
            store.append((t, value))
        while store and t - store[0][0] > cfg.motion_scale_s:
            store.popleft()
        return z

    def _motion(self, t, od735, od850, snr735, snr850) -> float | None:
        """max(z of deviation from the trailing median, z of the frame-to-frame OD change).

        The first catches a displaced/lifted probe (level shift), the second a fast jerk. Both are
        scaled by the channel's own quiet history, so a channel is only judged against itself."""
        cfg, fs = self.cfg, self.fs
        devs, steps = [], []
        for k, od in enumerate((od735, od850)):
            q = self._odq[k]
            if len(q) >= round(cfg.motion_dev_min_s * fs):
                devs.append(abs(od - self._median([v for _, v in q])))
            if q and 0 < t - q[-1][0] <= 1.5 / fs * 1.0 + 1e-9:
                steps.append(abs(od - q[-1][1]))
            q.append((t, od))
            while q and t - q[0][0] > cfg.motion_lag_s:
                q.popleft()
        zs = []
        snr_min = min(snr735, snr850)
        floor = cfg.motion_noise_floor_k / snr_min if snr_min > 0 else 0.0   # NaN/<=0 SNR: not eligible anyway
        if len(devs) == 2:
            zs.append(self._scaled(self._scale, t, max(devs), floor))
        if len(steps) == 2:
            zs.append(self._scaled(self._dscale, t, max(steps), floor))
        zs = [z for z in zs if z is not None]
        eligible = snr735 >= cfg.motion_min_snr and snr850 >= cfg.motion_min_snr
        return max(zs) if zs and eligible else None


def tier_for(snr: float | None, q: ChannelQuality, cfg: QualityConfig) -> str:
    """good / noisy / bad / not_assessable from SNR_frame (min of the two wavelengths) + indicators."""
    if snr is None or not math.isfinite(snr):
        return TIER_NA
    if ACHROMATIC in q.flags or snr < cfg.snr_noisy:
        return TIER_BAD
    if q.contact_ok is None:   # no cardiac verdict yet (warm-up, gaps, dark channel)
        return TIER_NOISY if snr < cfg.snr_good else TIER_NA
    return TIER_GOOD if (snr >= cfg.snr_good and q.contact_ok) else TIER_NOISY


class MotionDetector:
    """Hemisphere markers from the channels that are bright enough to judge. Marker only, no correction.

    A side is flagged in a frame when at least motion_min_channels eligible channels (and motion_side_fraction
    of them) have z >= motion_z. A marker needs motion_persist consecutive flagged frames. A frame without
    enough eligible channels (loss, invalid frames) is neutral for up to motion_hold_s: it neither adds to nor
    resets the run, so a burst loss no longer blinds the detector. If a run starts after a blind interval
    longer than motion_gap_bridge_s, the segment starts at the last evaluated frame so that the blind
    interval itself is marked (comparison of the level before and after the gap)."""

    def __init__(self, sides: dict, config: QualityConfig) -> None:
        self.sides, self.cfg = sides, config
        self.run = {s: 0 for s in sides}
        self.active = {s: False for s in sides}
        self.segments: list = []          # [side, t_start, t_end]
        self.frames = {s: 0 for s in sides}
        self._last_eval = {s: None for s in sides}     # last time the side had enough eligible channels
        self._run_start = {s: None for s in sides}

    def update(self, t: float, z: dict) -> dict:
        cfg = self.cfg
        for side, names in self.sides.items():
            elig = [z.get(n) for n in names if z.get(n) is not None]
            last = self._last_eval[side]
            if len(elig) < cfg.motion_min_channels:
                # Neutral frame. After motion_hold_s without a verdict the candidate and the marker end.
                if last is not None and t - last > cfg.motion_hold_s:
                    self.run[side], self._run_start[side], self.active[side] = 0, None, False
                continue
            hit = sum(1 for v in elig if v >= cfg.motion_z)
            flagged = hit >= cfg.motion_min_channels and hit >= cfg.motion_side_fraction * len(elig)
            if flagged:
                if self.run[side] == 0:
                    blind = last is not None and t - last > cfg.motion_gap_bridge_s
                    self._run_start[side] = last if blind else t
                self.run[side] += 1
            else:
                self.run[side], self._run_start[side] = 0, None
            self._last_eval[side] = t
            on = self.run[side] >= cfg.motion_persist
            self.active[side] = on
            if on:
                self.frames[side] += 1
                start = self._run_start[side] if self._run_start[side] is not None else t
                seg = next((s for s in reversed(self.segments) if s[0] == side), None)
                if seg is not None and start - seg[2] <= cfg.motion_merge_s:
                    seg[2] = t
                else:
                    self.segments.append([side, start, t])
        return dict(self.active)

    def summary(self) -> dict:
        return dict(frames=dict(self.frames), segments=[list(s) for s in self.segments[-200:]],
                    note="marker only; no correction is applied")
