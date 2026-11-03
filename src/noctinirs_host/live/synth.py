"""Synthetic OPT2 frame generator for the device simulator.

MODEL, not data from any device. Values are made up (see `SignalModel`); only the byte
layout follows firmware ADR-0004 3.17 as implemented by the strict host parser. The
frames it builds are checked in tests by feeding them to `parse_opt2`.
"""

from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass

from ..geometry import Geometry
from ..wire import optical as opt

INT32_MIN = -(2 ** 31)
N_USED = 10  # MODEL CHOICE: samples averaged per LED phase
SAMPLE_COUNT = 100  # MODEL CHOICE: ADC samples per frame (the document does not fix it for the model)


@dataclass(frozen=True)
class DetSpec:
    sum_on: int
    sum_off: int
    sd_on_q4: int
    sd_off_q4: int
    flags: int = 0x1C  # STEP_Z | STABLE | FLOOR
    stab: int = 8


BLANK_DET = DetSpec(INT32_MIN, INT32_MIN, 0xFFFF, 0xFFFF, 0, 0xFF)


@dataclass(frozen=True)
class SlotSpec:
    quality: int
    reason: int
    mask: int
    witness: int
    dets: tuple[DetSpec, ...]
    n_on: int = N_USED
    n_off: int = N_USED


def blank_slot(reason: int = opt.REASON_NO_WITNESS) -> SlotSpec:
    return SlotSpec(opt.Q_INVALID, reason, 0, opt.WITNESS_NONE, (BLANK_DET,) * 4)


def build_opt2_payload(slots: list[SlotSpec], *, frame_sequence: int, first_sample: int,
                       epoch: int = 1, geometry_map_id: int = 1, gate_config_id: int = 1,
                       frame_flags: int = 0, sample_count: int = SAMPLE_COUNT) -> bytes:
    if len(slots) != opt.SLOT_COUNT:
        raise ValueError("need exactly 20 slots")
    out = struct.pack("<BBHIIHHBBHHH", opt.ADC_SOURCE_ADS131, opt.LAYOUT_OPT2, epoch,
                      frame_sequence & 0xFFFFFFFF, first_sample & 0xFFFFFFFF, sample_count,
                      frame_flags, opt.SLOT_COUNT, opt.DET_COUNT, geometry_map_id,
                      gate_config_id, 0)
    stab = bytearray()
    for i, s in enumerate(slots):
        out += struct.pack("<BBHBBBBBBBB", i, s.quality, s.reason, s.n_on, s.n_off,
                           s.n_on, s.n_off, 0, 0, s.witness, s.mask)
        for d in s.dets:
            out += struct.pack("<iiHHB", d.sum_on, d.sum_off, d.sd_on_q4, d.sd_off_q4, d.flags)
            stab.append(d.stab)
    return out + bytes(stab)


class SignalModel:
    """Made-up light levels: falls with source-detector distance, scales with LED current,
    small 'cardiac' and slow components, a little noise. Deterministic for a given seed."""

    BASE_COUNTS = 3000.0  # amplitude at 10 mm and 30 mA (arbitrary)
    DECAY_MM = 12.0
    AMBIENT = 100.0

    def __init__(self, geometry: Geometry | None = None, seed: int = 1) -> None:
        self.geometry = geometry
        self._rng = random.Random(seed)

    def _distance_mm(self, slot: int, ain: int) -> float:
        g = self.geometry
        if g is None:
            return 30.0
        ch = g.channel_for(slot, ain)
        return ch.sd_mm if ch is not None else 45.0  # pairs that are not channels: far / weak

    def slot_spec(self, slot: int, t: float, current_ma: int, dead_ains: frozenset[int]) -> SlotSpec:
        if current_ma <= 0:
            return blank_slot()  # LED off: nothing to measure on this slot
        wl_gain = 1.0 if slot % 2 == 0 else 1.15
        dets: list[DetSpec] = []
        mask = 0
        for ain in range(4):
            if ain in dead_ains:
                dets.append(BLANK_DET)
                continue
            mask |= 1 << ain
            dist = self._distance_mm(slot, ain)
            amp = (self.BASE_COUNTS * (current_ma / 30.0) * wl_gain
                   * math.exp(-(dist - 10.0) / self.DECAY_MM))
            phase = 0.7 * slot + 1.3 * ain
            amp *= 1.0 + 0.03 * math.sin(2 * math.pi * 1.1 * t + phase) \
                + 0.08 * math.sin(2 * math.pi * 0.08 * t + phase)
            mean_off = self.AMBIENT + self._rng.gauss(0, 0.5)
            mean_on = mean_off + amp + self._rng.gauss(0, 1.0 + 0.002 * amp)
            dets.append(DetSpec(round(mean_on * N_USED), round(mean_off * N_USED),
                                round(16 * (2.0 + 0.002 * amp)), 16 * 2))
        quality = opt.Q_VALID_TARGET if mask else opt.Q_INVALID
        reason = 0 if mask else opt.REASON_NO_WITNESS
        witness = (mask & -mask).bit_length() - 1 if mask else opt.WITNESS_NONE
        return SlotSpec(quality, reason, mask, witness, tuple(dets))
