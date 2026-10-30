"""Host-side recheck of the firmware optical gate (contract v0.4.1 section 6.1).

The arithmetic is deliberately plain Python float (binary64), in the exact
order given by the contract, so results match the firmware bit for bit.
Gate parameters are INPUTS (they come from `gate_config_id` / session data);
they are never constants in this module.

LIMIT (contract section 10.8): this check is one-directional on gated
frames. It can catch a false PASS (a value that was sent although it should
have failed). It cannot see a false REJECTION, because rejected values are
blanked and never transmitted. False rejections are measurable only in
ENGINEERING mode and by offline RAW (dark-box) analysis.

Checked bit for bit against the 26 shared gate golden vectors (tests/test_golden.py).
The C code itself is not run here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .wire import optical as o


@dataclass(frozen=True)
class GateParams:
    gate_config_id: int
    z_witness_x10: int
    z_channel_x10: int
    stable_k_x10: int
    floor_counts: int
    se_floor_x100: int


@dataclass(frozen=True)
class GateResult:
    evaluated: bool
    step_z_pass: bool
    floor_pass: bool
    stable_pass: bool
    diff_negative: bool
    diff: float
    threshold: float


_NOT_EVALUATED = GateResult(False, False, False, False, False, 0.0, 0.0)


def evaluate(n_on: int, n_off: int, sum_on: int, sum_off: int, sd_on_q4: int,
             sd_off_q4: int, stab_q: int, params: GateParams, z_x10: int) -> GateResult:
    if (n_on <= 0 or n_off <= 0 or sum_on == o.INT32_MIN or sum_off == o.INT32_MIN
            or sd_on_q4 == o.SD_BLANK or sd_off_q4 == o.SD_BLANK):
        return _NOT_EVALUATED
    a = float(sum_on) / float(n_on)
    b = float(sum_off) / float(n_off)
    diff = a - b
    s_on = float(sd_on_q4) / 16.0
    s_off = float(sd_off_q4) / 16.0
    v_on = s_on * s_on / float(n_on)
    v_off = s_off * s_off / float(n_off)
    se = math.sqrt(v_on + v_off)
    floor_se = float(params.se_floor_x100) / 100.0
    se_eff = se if se > floor_se else floor_se
    z = float(z_x10) / 10.0
    thr = z * se_eff
    step_z = diff >= thr
    floor_ok = diff >= float(params.floor_counts)
    stable = stab_q < o.STAB_TRUNCATED and 10 * stab_q <= 16 * params.stable_k_x10
    return GateResult(True, step_z, floor_ok, stable, diff < 0.0, diff, thr)


@dataclass(frozen=True)
class Mismatch:
    slot_index: int  # -1 = frame level
    ain: int  # -1 = slot level
    code: str
    detail: str


def recheck_frame(frame: o.Opt2Frame, params: GateParams) -> list[Mismatch]:
    """Recheck every value the host would treat as science. Empty list = consistent.

    Not applicable (returns []) for ENGINEERING / non-science frames.
    """
    out: list[Mismatch] = []
    if not frame.is_science:
        return out
    if frame.gate_config_id != params.gate_config_id:
        out.append(Mismatch(-1, -1, "gate_config_id_mismatch",
                            f"frame {frame.gate_config_id} vs params {params.gate_config_id}"))
        return out
    for slot in frame.slots:
        mask = frame.science_mask(slot.slot_index)
        if not mask:
            continue
        if slot.witness_ain == o.WITNESS_NONE:
            out.append(Mismatch(slot.slot_index, -1, "verified_slot_without_witness", ""))
        for ain in range(o.DET_COUNT):
            if not (mask >> ain) & 1:
                continue
            d = slot.detectors[ain]
            r = evaluate(slot.n_on_used, slot.n_off_used, d.sum_on, d.sum_off,
                         d.sd_on_q4, d.sd_off_q4, d.stab_q, params, params.z_channel_x10)
            checks = (
                ("step_z", r.step_z_pass, bool(d.det_flags & o.DF_STEP_Z_PASS), True),
                ("stable", r.stable_pass, bool(d.det_flags & o.DF_STABLE_PASS), True),
                ("floor", r.floor_pass, bool(d.det_flags & o.DF_FLOOR_PASS), True),
                ("diff_negative", r.diff_negative, bool(d.det_flags & o.DF_DIFF_NEGATIVE), False),
            )
            for name, mine, reported, must_be_true in checks:
                if mine != reported:
                    out.append(Mismatch(slot.slot_index, ain, f"{name}_flag_differs",
                                        f"host={mine} firmware={reported}"))
                if must_be_true and not mine:
                    out.append(Mismatch(slot.slot_index, ain, "false_pass",
                                        f"{name} fails on host but value was sent as verified"))
            if d.det_flags & (o.DF_SAT_ON | o.DF_SAT_OFF):
                out.append(Mismatch(slot.slot_index, ain, "saturated_but_verified", ""))
        w = slot.witness_ain
        if w != o.WITNESS_NONE and (mask >> w) & 1:
            d = slot.detectors[w]
            r = evaluate(slot.n_on_used, slot.n_off_used, d.sum_on, d.sum_off,
                         d.sd_on_q4, d.sd_off_q4, d.stab_q, params, params.z_witness_x10)
            if not (r.step_z_pass and r.stable_pass and r.floor_pass):
                out.append(Mismatch(slot.slot_index, w, "witness_fails_host_check", ""))
    return out
