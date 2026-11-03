"""Channel map, quality tiers and per-channel buffers for the live view.

Everything here is HOST-SIDE presentation logic on top of `Opt2Frame.science_sample()`
(the single choke point for science values). Quality tiers are a host heuristic for
display; they are NOT a firmware verdict and not a data-validity claim.

Intensity = mean(LED on) - mean(LED off), in ADC counts, per slot (wavelength) and
detector. A value that `science_sample()` does not release is a GAP: it is stored as
None and is never interpolated, filled or smoothed.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ..geometry import Geometry
from ..wire import optical as opt

WAVELENGTHS = ("735", "850")  # nominal labels from the geometry file (identity not verified)

GOOD = "good"
MARGINAL = "marginal"
GAP = "gap"
_RANK = {GOOD: 0, MARGINAL: 1, GAP: 2}


@dataclass(frozen=True)
class LiveChannel:
    index: int
    name: str
    kind: str  # "long" | "short" (from the geometry file)
    source: str
    pd: str
    sd_mm: float
    ain: int
    slots: tuple[int, int]  # (slot for 735, slot for 850)


def build_channels(geometry: Geometry) -> tuple[LiveChannel, ...]:
    """Channels in geometry-file order. Every (source, detector) pair that the file
    does not list as a channel is simply not here."""
    by_id = {s.id: s for s in geometry.sources}
    out: list[LiveChannel] = []
    for i, c in enumerate(geometry.channels):
        src = by_id[c.source]
        slots = (src.slots["735"], src.slots["850"])
        for slot in slots:  # same translation the rest of the host uses
            if geometry.channel_for(slot, c.ain) is not c:
                raise ValueError(f"{c.name}: slot {slot} / AIN {c.ain} does not map back")
        out.append(LiveChannel(i, c.name, c.kind, c.source, c.pd, c.sd_mm, c.ain, slots))
    return tuple(out)


def worst(*tiers: str) -> str:
    return max(tiers, key=_RANK.__getitem__)


def intensity(frame: opt.Opt2Frame, slot: int, ain: int) -> tuple[float | None, str]:
    """(counts, tier) for one slot/detector. (None, GAP) when science_sample refuses."""
    s = frame.science_sample(slot, ain)
    if s is None or s.n_on <= 0 or s.n_off <= 0:
        return None, GAP
    value = s.sum_on / s.n_on - s.sum_off / s.n_off
    if not math.isfinite(value):
        return None, GAP
    rec = frame.slots[slot]
    d = rec.detectors[ain]
    tier = GOOD
    # HOST HEURISTIC: anything short of "target quality, all gate bits passed, no
    # saturation or clipping, positive difference" is shown as marginal.
    if rec.quality != opt.Q_VALID_TARGET:
        tier = MARGINAL
    if s.clipped_on or s.clipped_off:
        tier = MARGINAL
    if d.det_flags & (opt.DF_SAT_ON | opt.DF_SAT_OFF | opt.DF_DIFF_NEGATIVE):
        tier = MARGINAL
    if d.det_flags & opt.DF_PASS_BITS != opt.DF_PASS_BITS:
        tier = MARGINAL
    if value <= 0:
        tier = MARGINAL
    return value, tier


@dataclass(frozen=True)
class Point:
    seq: int  # frame_sequence (loss counter domain)
    t_s: float  # device timestamp, seconds since the first frame of the session
    value: float | None
    tier: str


class ChannelBuffer:
    """Ring buffer for one channel, one deque per wavelength."""

    def __init__(self, capacity: int) -> None:
        self._w = {wl: deque(maxlen=capacity) for wl in WAVELENGTHS}

    def push(self, wl: str, p: Point) -> None:
        self._w[wl].append(p)

    def clear(self) -> None:
        for d in self._w.values():
            d.clear()

    def __len__(self) -> int:
        return len(self._w[WAVELENGTHS[0]])

    def points(self, wl: str) -> list[Point]:
        return list(self._w[wl])

    def series(self, wl: str, last_s: float | None = None) -> tuple[list[float], list[float | None]]:
        """(t, value) with an explicit None wherever frames are missing or values withheld.
        A missing frame_sequence between two stored points inserts one None (a visible gap)."""
        pts = self._w[wl]
        if not pts:
            return [], []
        t_end = pts[-1].t_s
        xs: list[float] = []
        ys: list[float | None] = []
        prev: Point | None = None
        for p in pts:
            if last_s is not None and p.t_s < t_end - last_s:
                continue
            if prev is not None and p.seq - prev.seq > 1:
                xs.append((prev.t_s + p.t_s) / 2)
                ys.append(None)
            xs.append(p.t_s)
            ys.append(p.value)
            prev = p
        return xs, ys

    def latest_tier(self) -> str:
        """Worst tier of the newest point of each wavelength; GAP when nothing yet."""
        newest = [d[-1].tier for d in self._w.values() if d]
        return worst(*newest) if len(newest) == len(WAVELENGTHS) else GAP
