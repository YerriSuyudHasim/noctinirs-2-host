"""FRAME payload parsers: layout 2 (OPT2) and layout 3 (RAW).

Source of truth: firmware optical data contract v0.4.1 (merged in the firmware
repository, see config/reference_pins.json). Tested against the shared golden
vectors (tests/test_golden.py); the C code itself is not run here.

Rule: the host is exactly as strict as the C codec. Packets the codec rejects are
rejected here (WireError, counted in wire/counters.py). Only unknown bits and a few
extra host-side checks make a single slot host-INVALID.

Safety rule enforced here: a value is science data only through
`Opt2Frame.science_mask()` / `science_sample()`. Everything else (blank,
ENGINEERING, STARTING_GATE, WARMUP, inconsistent slots) is never science.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import WireError

ADC_SOURCE_ADS131 = 1
LAYOUT_1 = 1
LAYOUT_OPT2 = 2
LAYOUT_RAW = 3

SLOT_COUNT = 20
DET_COUNT = 4
OPT2_PREFIX_SIZE = 24
SLOT_SIZE = 64
DET_SIZE = 13
STAB_SIZE = 80
OPT2_PAYLOAD_SIZE = OPT2_PREFIX_SIZE + SLOT_COUNT * SLOT_SIZE + STAB_SIZE  # 1384
RAW_PREFIX_SIZE = 16
RAW_SAMPLE_SIZE = 14
RAW_MAX_SAMPLES = 100

INT32_MIN = -(2 ** 31)
SD_CLIPPED = 0xFFFE
SD_BLANK = 0xFFFF
STAB_TRUNCATED = 0xFE
STAB_NONE = 0xFF
WITNESS_NONE = 0xFF

# quality (contract v0.3/v0.4.1)
Q_INVALID, Q_VALID_MINIMUM, Q_VALID_TARGET, Q_ABORTED_BY_STOP, Q_STARTUP_WARMUP = range(5)

# reason bits 0..11 as named in contract v0.4.1 section 4.3 (confirmed by firmware reviewer)
REASON_UNDER_K_ON = 1 << 0
REASON_UNDER_K_OFF = 1 << 1
REASON_LED_ON_MISSING = 1 << 2
REASON_LED_OFF_MISSING = 1 << 3
REASON_RUNTIME_TIMING = 1 << 4
REASON_ADS_INTEGRITY = 1 << 5
REASON_STOP_ABORT = 1 << 6
REASON_STALE_EPOCH = 1 << 7
REASON_INTERNAL = 1 << 8
REASON_STARTUP_WARMUP = 1 << 9
REASON_NO_WITNESS = 1 << 10
REASON_WITNESS_UNSTABLE = 1 << 11
REASON_KNOWN_MASK = 0x0FFF  # bits 0..11; 12..15 reserved

# det_flags
DF_SAT_ON = 1 << 0
DF_SAT_OFF = 1 << 1
DF_STEP_Z_PASS = 1 << 2
DF_STABLE_PASS = 1 << 3
DF_FLOOR_PASS = 1 << 4
DF_DIFF_NEGATIVE = 1 << 5
DF_GATE_OFF = 1 << 6
DF_RESERVED_MASK = 0x80
DF_GATE_BITS = DF_STEP_Z_PASS | DF_STABLE_PASS | DF_FLOOR_PASS | DF_DIFF_NEGATIVE
DF_PASS_BITS = DF_STEP_Z_PASS | DF_STABLE_PASS | DF_FLOOR_PASS

# frame_flags
FF_WARMUP = 1 << 0
FF_PAIR_SAME_SLOT_OFF_AFTER_ON = 1 << 1
FF_ENGINEERING_UNGATED = 1 << 2
FF_STARTING_GATE = 1 << 3
FF_KNOWN_MASK = 0x000F

# RAW phase classes
PH_OUTSIDE = 0
PH_END_GUARD = 8
RAW_SLOT_NONE = 0xFF

_PREFIX = struct.Struct("<BBHIIHHBBHHH")
_SLOT_HEAD = struct.Struct("<BBHBBBBBBBB")
_DET = struct.Struct("<iiHHB")
_RAW_PREFIX = struct.Struct("<BBHIHHHH")
assert _PREFIX.size == OPT2_PREFIX_SIZE
assert _SLOT_HEAD.size + DET_COUNT * _DET.size == SLOT_SIZE
assert _DET.size == DET_SIZE
assert _RAW_PREFIX.size == RAW_PREFIX_SIZE

LAYOUT1_MESSAGE = ("layout tidak didukung (ADR-0004 3.17.5 layout 1 tidak membawa "
                   "4 detektor dan sebaran)")


@dataclass(frozen=True)
class DetectorRecord:
    sum_on: int
    sum_off: int
    sd_on_q4: int
    sd_off_q4: int
    det_flags: int
    stab_q: int

    @property
    def fully_blank(self) -> bool:
        return (self.sum_on == INT32_MIN and self.sum_off == INT32_MIN
                and self.sd_on_q4 == SD_BLANK and self.sd_off_q4 == SD_BLANK)

    @property
    def fully_present(self) -> bool:
        return (self.sum_on != INT32_MIN and self.sum_off != INT32_MIN
                and self.sd_on_q4 != SD_BLANK and self.sd_off_q4 != SD_BLANK)


@dataclass(frozen=True)
class SlotRecord:
    slot_index: int
    quality: int
    reason: int
    n_on_used: int
    n_off_used: int
    n_on_available: int
    n_off_available: int
    on_commit_late: int
    off_commit_late: int
    witness_ain: int
    verified_mask: int
    detectors: tuple[DetectorRecord, ...]
    host_invalid: str | None = None  # set when the host treats this slot as INVALID


@dataclass(frozen=True)
class ScienceSample:
    sum_on: int
    n_on: int
    sum_off: int
    n_off: int
    sd_on: float
    sd_off: float
    clipped_on: bool
    clipped_off: bool
    # quality 1 vs 2 depends on k_min/k_target of the session, which the host does not
    # have yet. Until it does, this is never reported as passed.
    quality_vs_k: str = "not_checked"


@dataclass(frozen=True)
class Opt2Frame:
    adc_source: int
    slot_plan_epoch: int
    frame_sequence: int
    first_sample_sequence: int
    sample_count: int
    frame_flags: int
    geometry_map_id: int
    gate_config_id: int
    slots: tuple[SlotRecord, ...]
    warnings: tuple[str, ...]

    @property
    def engineering(self) -> bool:
        # fail-closed: either indicator makes the frame non-science
        return bool(self.frame_flags & FF_ENGINEERING_UNGATED) or self.gate_config_id == 0

    @property
    def is_science(self) -> bool:
        """False for ENGINEERING, STARTING_GATE and WARMUP frames."""
        return not (self.engineering
                    or self.frame_flags & (FF_STARTING_GATE | FF_WARMUP))

    def science_mask(self, slot_index: int) -> int:
        """AIN bit mask whose values may be used as science data. Single choke point."""
        if not self.is_science:
            return 0
        s = self.slots[slot_index]
        if s.host_invalid or s.quality not in (Q_VALID_MINIMUM, Q_VALID_TARGET) or s.reason:
            return 0
        mask = s.verified_mask & 0x0F
        for d in range(DET_COUNT):
            if not s.detectors[d].fully_present:
                mask &= ~(1 << d)
        return mask

    def science_sample(self, slot_index: int, ain: int) -> ScienceSample | None:
        if not (self.science_mask(slot_index) >> ain) & 1:
            return None
        s = self.slots[slot_index]
        d = s.detectors[ain]
        return ScienceSample(
            d.sum_on, s.n_on_used, d.sum_off, s.n_off_used,
            d.sd_on_q4 / 16.0, d.sd_off_q4 / 16.0,
            d.sd_on_q4 == SD_CLIPPED, d.sd_off_q4 == SD_CLIPPED)


@dataclass(frozen=True)
class RawSample:
    sample_sequence: int
    ain: tuple[int, int, int, int]
    phase_class: int
    slot_index: int


@dataclass(frozen=True)
class RawPacket:
    slot_plan_epoch: int
    first_sample_sequence: int
    sample_count: int
    gap_before: bool
    geometry_map_id: int
    samples: tuple[RawSample, ...]
    # RAW is ENGINEERING by definition; never science data.
    is_science: bool = False


def _int24(b0: int, b1: int, b2: int) -> int:
    v = b0 | (b1 << 8) | (b2 << 16)
    return v - 0x1000000 if v & 0x800000 else v


def _slot_problem(f_flags_ok: bool, quality: int, reason: int, mask: int, witness: int,
                  n_on: int, n_off: int, avail_on: int, avail_off: int,
                  dets: list[DetectorRecord]) -> tuple[str, bool] | None:
    """First problem of a gated slot as (detail, fatal), else None.

    fatal=True: the C codec rejects the whole packet, so the host rejects it too.
    fatal=False: only this slot becomes host-INVALID (unknown bits, or an extra
    host-side consistency rule); the other slots stay usable.
    """
    if not f_flags_ok:
        return "unknown_frame_flags", False
    if quality > Q_STARTUP_WARMUP:
        return "unknown_quality", False
    if reason & ~REASON_KNOWN_MASK:
        return "unknown_reason_bits", False
    for d in dets:
        if d.det_flags & (DF_RESERVED_MASK | DF_GATE_OFF):
            return "bad_det_flags", False
    if witness != WITNESS_NONE and witness >= DET_COUNT:
        return "bad_witness", True
    if mask & 0xF0:
        return "bad_verified_mask", True
    if n_on > avail_on or n_off > avail_off:
        return "used_gt_available", True
    if reason:
        if reason & REASON_STOP_ABORT and reason & REASON_STARTUP_WARMUP:
            return "reason_quality", True
        want = (Q_ABORTED_BY_STOP if reason & REASON_STOP_ABORT
                else Q_STARTUP_WARMUP if reason & REASON_STARTUP_WARMUP
                else Q_INVALID)
        if quality != want:
            return "reason_quality", True
    for d in dets:
        if not (d.fully_blank or d.fully_present):
            return "partial_blank", True
        if d.fully_blank and d.stab_q != STAB_NONE:
            return "masked_stab_not_unknown", True
    if quality in (Q_VALID_MINIMUM, Q_VALID_TARGET):
        for i, d in enumerate(dets):
            verified = (mask >> i) & 1
            if verified and not d.fully_present:
                return "verified_but_blank", True
            if not verified and not d.fully_blank:
                return "unverified_value_present", True
            if verified and (n_on < 2 or n_off < 2):
                return "verified_n_lt_2", True
        # extra host-side rules (stricter than the codec): never science
        if witness == WITNESS_NONE:
            return "verified_slot_without_witness", False
        for i, d in enumerate(dets):
            if not (mask >> i) & 1:
                continue
            if d.det_flags & (DF_SAT_ON | DF_SAT_OFF):
                return "verified_but_saturated", False
            if d.det_flags & DF_PASS_BITS != DF_PASS_BITS or d.det_flags & DF_DIFF_NEGATIVE:
                return "verified_flags_not_pass", False
            if d.stab_q >= STAB_TRUNCATED:
                return "verified_with_unusable_stab_q", False
    else:
        if mask or not all(d.fully_blank for d in dets):
            return "values_present_but_not_verified", True
    return None


def _engineering_check(i: int, mask: int, witness: int, reason: int,
                       dets: list[DetectorRecord]) -> str | None:
    """ENGINEERING fill rules the C codec enforces; a violation rejects the packet."""
    if mask:
        return f"slot {i}: ENGINEERING verified_mask != 0"
    if witness != WITNESS_NONE:
        return f"slot {i}: ENGINEERING witness_ain != 0xFF"
    if reason & (REASON_NO_WITNESS | REASON_WITNESS_UNSTABLE):
        return f"slot {i}: ENGINEERING reason bit 10/11 set"
    for a, d in enumerate(dets):
        if d.det_flags & DF_GATE_BITS or not d.det_flags & DF_GATE_OFF:
            return f"slot {i} AIN{a}: ENGINEERING det_flags gate bits set or GATE_OFF missing"
    return None


def _engineering_warnings(i: int, quality: int, reason: int,
                          dets: list[DetectorRecord]) -> list[str]:
    """Unknown bits in ENGINEERING frames: values kept as diagnostics, warn only."""
    w: list[str] = []
    if reason & ~REASON_KNOWN_MASK:
        w.append(f"slot {i}: unknown reason bits")
    if quality > Q_STARTUP_WARMUP:
        w.append(f"slot {i}: unknown quality {quality}")
    for a, d in enumerate(dets):
        if d.det_flags & DF_RESERVED_MASK:
            w.append(f"slot {i} AIN{a}: reserved det_flags bit set")
    return w


def parse_opt2(payload: bytes) -> Opt2Frame:
    if len(payload) != OPT2_PAYLOAD_SIZE:
        raise WireError("opt2_size", f"OPT2 payload must be {OPT2_PAYLOAD_SIZE} B, got {len(payload)}")
    (adc, layout, epoch, fseq, first_seq, sample_count, fflags, slot_count, det_count,
     geo_id, gate_id, reserved) = _PREFIX.unpack_from(payload, 0)
    if adc != ADC_SOURCE_ADS131:
        raise WireError("unknown_adc_source", f"adc_source {adc} not supported")
    if layout != LAYOUT_OPT2:
        raise WireError("unsupported_layout", f"expected layout 2, got {layout}")
    if slot_count != SLOT_COUNT or det_count != DET_COUNT:
        raise WireError("bad_counts", f"slot_count={slot_count} det_count={det_count}")
    if reserved != 0:
        raise WireError("reserved", "reserved prefix field is not 0")
    engineering = bool(fflags & FF_ENGINEERING_UNGATED)
    if engineering != (gate_id == 0):
        raise WireError("engineering_mismatch",
                        "ENGINEERING_UNGATED flag and gate_config_id==0 disagree")

    warnings: list[str] = []
    flags_ok = not (fflags & ~FF_KNOWN_MASK)
    if not flags_ok:
        warnings.append(f"unknown frame_flags bits (0x{fflags:04X})")

    stab_off = OPT2_PREFIX_SIZE + SLOT_COUNT * SLOT_SIZE
    slots: list[SlotRecord] = []
    for i in range(SLOT_COUNT):
        base = OPT2_PREFIX_SIZE + i * SLOT_SIZE
        (idx, quality, reason, n_on, n_off, a_on, a_off, late_on, late_off,
         witness, mask) = _SLOT_HEAD.unpack_from(payload, base)
        if idx != i:
            raise WireError("slot_index", f"slot record {i} carries slot_index {idx}")
        dets: list[DetectorRecord] = []
        for d in range(DET_COUNT):
            s_on, s_off, sd_on, sd_off, df = _DET.unpack_from(
                payload, base + _SLOT_HEAD.size + d * DET_SIZE)
            dets.append(DetectorRecord(s_on, s_off, sd_on, sd_off, df,
                                       payload[stab_off + i * DET_COUNT + d]))
        invalid = None
        if engineering:
            if n_on > a_on or n_off > a_off:
                raise WireError("used_gt_available",
                                f"slot {i}: used counts exceed available counts")
            bad = _engineering_check(i, mask, witness, reason, dets)
            if bad:
                raise WireError("engineering_rule", bad)
            warnings.extend(_engineering_warnings(i, quality, reason, dets))
        else:
            problem = _slot_problem(flags_ok, quality, reason, mask, witness, n_on, n_off,
                                   a_on, a_off, dets)
            if problem:
                detail, fatal = problem
                if fatal:
                    # codes with their own counter keep their name; others are grouped
                    code = detail if detail in ("reason_quality", "verified_n_lt_2",
                                                "used_gt_available") \
                        else "slot_inconsistent"
                    raise WireError(code, f"slot {i}: {detail}")
                invalid = detail
                warnings.append(f"slot {i}: treated INVALID ({invalid})")
        for d_i, d in enumerate(dets):
            if SD_CLIPPED in (d.sd_on_q4, d.sd_off_q4):
                warnings.append(f"slot {i} AIN{d_i}: sd clipped (>= 4095.875 counts), "
                                "a bound and not an exact value")
        slots.append(SlotRecord(i, quality, reason, n_on, n_off, a_on, a_off, late_on,
                                late_off, witness, mask, tuple(dets), invalid))
    return Opt2Frame(adc, epoch, fseq, first_seq, sample_count, fflags, geo_id, gate_id,
                     tuple(slots), tuple(warnings))


def parse_raw(payload: bytes) -> RawPacket:
    if len(payload) < RAW_PREFIX_SIZE:
        raise WireError("raw_size", "RAW payload shorter than prefix")
    (adc, layout, epoch, first_seq, n, raw_flags, geo_id,
     reserved) = _RAW_PREFIX.unpack_from(payload, 0)
    if adc != ADC_SOURCE_ADS131:
        raise WireError("unknown_adc_source", f"adc_source {adc} not supported")
    if layout != LAYOUT_RAW:
        raise WireError("unsupported_layout", f"expected layout 3, got {layout}")
    if not 1 <= n <= RAW_MAX_SAMPLES:
        raise WireError("sample_count", f"sample_count {n} outside 1..{RAW_MAX_SAMPLES}")
    if len(payload) != RAW_PREFIX_SIZE + n * RAW_SAMPLE_SIZE:
        raise WireError("raw_size", f"payload {len(payload)} B does not match sample_count {n}")
    if not raw_flags & 1:
        raise WireError("raw_flags", "ENGINEERING bit (bit0) must be 1 in RAW")
    if raw_flags & ~0x3:
        raise WireError("raw_flags", f"reserved raw_flags bits set (0x{raw_flags:04X})")
    if reserved != 0:
        raise WireError("reserved", "reserved prefix field is not 0")
    samples: list[RawSample] = []
    off = RAW_PREFIX_SIZE
    for i in range(n):
        p = payload[off:off + RAW_SAMPLE_SIZE]
        ain = tuple(_int24(p[3 * k], p[3 * k + 1], p[3 * k + 2]) for k in range(4))
        phase, slot = p[12], p[13]
        if phase > PH_END_GUARD:
            raise WireError("raw_phase_class", f"phase_class {phase} outside 0..8")
        if phase == PH_OUTSIDE and slot != RAW_SLOT_NONE:
            raise WireError("raw_slot_index", "OUTSIDE sample with slot_index != 0xFF")
        if phase != PH_OUTSIDE and slot == RAW_SLOT_NONE:
            raise WireError("raw_slot_index", "non-OUTSIDE sample with slot_index 0xFF")
        if slot != RAW_SLOT_NONE and slot >= SLOT_COUNT:
            raise WireError("raw_slot_index", f"slot_index {slot} >= {SLOT_COUNT}")
        samples.append(RawSample((first_seq + i) & 0xFFFFFFFF, ain, phase, slot))
        off += RAW_SAMPLE_SIZE
    return RawPacket(epoch, first_seq, n, bool(raw_flags & 2), geo_id, tuple(samples))


def parse_frame(payload: bytes) -> Opt2Frame | RawPacket:
    """Dispatch on frame_layout. Unknown layout or adc_source: reject and count."""
    if len(payload) < 2:
        raise WireError("size", "FRAME payload shorter than 2 bytes")
    if payload[0] != ADC_SOURCE_ADS131:
        raise WireError("unknown_adc_source", f"adc_source {payload[0]} not supported")
    layout = payload[1]
    if layout == LAYOUT_OPT2:
        return parse_opt2(payload)
    if layout == LAYOUT_RAW:
        return parse_raw(payload)
    if layout == LAYOUT_1:
        raise WireError("unsupported_layout", LAYOUT1_MESSAGE)
    raise WireError("unsupported_layout", f"{LAYOUT1_MESSAGE.split(' (')[0]} (frame_layout {layout})")
