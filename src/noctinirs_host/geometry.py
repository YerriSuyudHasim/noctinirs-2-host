"""Optical geometry loader with validation.

`Geometry.pd_for_ain()` is the ONLY place that translates an AIN index to a
photodiode. All other lookups go through it. Do not build a second map.
Wavelength labels ("735"/"850") are nominal; identity is not verified (D7).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "noctinirs.optical_geometry/1"


class GeometryError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class DetectorInfo:
    pd: str
    ain: int
    side: str


@dataclass(frozen=True)
class SourceInfo:
    id: str
    side: str
    slots: dict[str, int]  # nominal wavelength label -> slot index


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    source: str
    pd: str
    ain: int
    sd_mm: float
    kind: str


@dataclass(frozen=True)
class Geometry:
    sha256: str
    geometry_map_id: int
    status: str
    detectors: tuple[DetectorInfo, ...]
    sources: tuple[SourceInfo, ...]
    channels: tuple[ChannelInfo, ...]

    @property
    def is_draft(self) -> bool:
        return self.status.upper().startswith("DRAFT")

    def pd_for_ain(self, ain: int) -> DetectorInfo:
        for d in self.detectors:
            if d.ain == ain:
                return d
        raise KeyError(f"AIN {ain} has no detector")

    def source_for_slot(self, slot: int) -> tuple[SourceInfo, str]:
        for s in self.sources:
            for wl, idx in s.slots.items():
                if idx == slot:
                    return s, wl
        raise KeyError(f"slot {slot} has no source")

    def channel_for(self, slot: int, ain: int) -> ChannelInfo | None:
        """Selected channel measured by (slot, AIN), or None (not a selected channel)."""
        src, _ = self.source_for_slot(slot)
        pd = self.pd_for_ain(ain).pd
        for c in self.channels:
            if c.source == src.id and c.pd == pd:
                return c
        return None

    def is_contralateral(self, slot: int, ain: int) -> bool:
        """True if the detector is on the opposite side of the source (negative control)."""
        src, _ = self.source_for_slot(slot)
        return self.pd_for_ain(ain).side != src.side


def _strict_int(v: object, what: str, lo: int, hi: int) -> int:
    """Accept only real JSON integers (not bool, not 2.9, not strings) inside [lo, hi]."""
    if type(v) is not int:
        raise ValueError(f"{what} must be an integer, got {v!r}")
    if not lo <= v <= hi:
        raise ValueError(f"{what} must be in {lo}..{hi}, got {v}")
    return v


def _positive_finite(v: object, what: str) -> float:
    if type(v) not in (int, float):
        raise ValueError(f"{what} must be a number, got {v!r}")
    f = float(v)
    if not math.isfinite(f) or f <= 0:
        raise ValueError(f"{what} must be finite and > 0, got {v!r}")
    return f


def load_geometry(path: str | Path) -> Geometry:
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        g = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GeometryError([f"not valid JSON: {e}"]) from e
    problems: list[str] = []
    try:
        if g.get("schema") != SCHEMA:
            problems.append(f"schema must be {SCHEMA}")
        dets = tuple(DetectorInfo(d["pd"], _strict_int(d["ain"], "detector ain", 0, 3), d["side"])
                     for d in g["detectors"])
        srcs = tuple(SourceInfo(s["id"], s["side"],
                                {k: _strict_int(v, "slot", 0, 19) for k, v in s["slot"].items()})
                     for s in g["sources"])
        chans = tuple(ChannelInfo(c["name"], c["source"], c["pd"],
                                  _strict_int(c["ain"], "channel ain", 0, 3),
                                  _positive_finite(c["sd_mm"], "sd_mm"), c["kind"])
                      for c in g["channels"])
        ain_to_pd = {_strict_int(int(k), "ain_to_pd key", 0, 3): v
                     for k, v in g["adc"]["ain_to_pd"].items()}
        map_id = _strict_int(g["geometry_map_id"], "geometry_map_id", 0, 0xFFFF)
        status = str(g["status"])
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise GeometryError([f"missing or malformed field: {e}"]) from e

    if sorted(d.ain for d in dets) != [0, 1, 2, 3]:
        problems.append("detectors must cover AIN 0..3 exactly once")
    if len({d.pd for d in dets}) != len(dets):
        problems.append("duplicate photodiode name")
    if {d.ain: d.pd for d in dets} != ain_to_pd:
        problems.append("adc.ain_to_pd disagrees with detectors")
    if any(d.side not in ("L", "R") for d in dets) or any(s.side not in ("L", "R") for s in srcs):
        problems.append("side must be L or R")
    if len(srcs) != 10 or len({s.id for s in srcs}) != 10:
        problems.append("need 10 unique sources")
    all_slots = [i for s in srcs for i in s.slots.values()]
    if sorted(all_slots) != list(range(20)):
        problems.append("slots must be 0..19, each used exactly once")
    if any(sorted(s.slots) != ["735", "850"] for s in srcs):
        problems.append("each source needs exactly the wavelength labels 735 and 850")
    if len(chans) != 20 or len({c.name for c in chans}) != 20:
        problems.append("need 20 unique channel names")
    src_ids = {s.id for s in srcs}
    det_by_pd = {d.pd: d for d in dets}
    pairs = set()
    for c in chans:
        if c.source not in src_ids:
            problems.append(f"{c.name}: unknown source {c.source}")
        if c.pd not in det_by_pd:
            problems.append(f"{c.name}: unknown photodiode {c.pd}")
        elif det_by_pd[c.pd].ain != c.ain:
            problems.append(f"{c.name}: AIN {c.ain} disagrees with detector table")
        if c.sd_mm <= 0:
            problems.append(f"{c.name}: sd_mm must be > 0")
        if (c.source, c.pd) in pairs:
            problems.append(f"{c.name}: duplicate source/detector pair")
        pairs.add((c.source, c.pd))
    for s in srcs:
        if sum(1 for c in chans if c.source == s.id) != 2:
            problems.append(f"source {s.id} must have exactly 2 channels")
    if problems:
        raise GeometryError(problems)
    return Geometry(sha, map_id, status, dets, srcs, chans)
