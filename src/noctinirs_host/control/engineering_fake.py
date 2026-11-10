"""Opt-in RAW fixture stream for the desktop loopback, using the B simulator.

Synthetic counts on a 20-slot plan (10 samples per slot with ON/OFF phases). It models no
hardware timing and no real optics: the signal amplitude per channel is an arbitrary function
of the geometry distance, modulated slowly so a preview has something to draw.
"""

import math
import random
import struct

from ..wire.envelope import FLAG_SIMULATED, FLAG_TS_ESTIMATED
from ..wire.optical import ADC_SOURCE_ADS131, LAYOUT_RAW, PH_OUTSIDE, RAW_SLOT_NONE
from .profile import IREF_CODE_MAX, IREF_CODE_MIN, IREF_SLOTS, PROFILE_ARG_BYTES, encode_profile_text
from .protocol import (OP_CLEAR_PROFILE, OP_GET_PROFILE, OP_INFO, OP_SET_PROFILE, OP_START, STATUS_INVALID_ARG,
                       STATUS_INVALID_STATE, STATUS_OK, ST_FAULTED, ST_STARTING, ST_STOPPED, ST_STREAMING,
                       encode_frame)
from .simulator import DeviceSimulator, FS_MIN, FS_MAX

SLOTS = 20
PLAN = (3, 4, 4, 4, 5, 6, 7, 7, 7, 8)  # ON_SETTLE, ON_STABLE x3, OFF_TRANSITION, OFF_SETTLE, OFF_STABLE x3, END_GUARD
PER_SLOT = len(PLAN)
CYCLE_SAMPLES = SLOTS * PER_SLOT
ADC_MAX = 8388607


class EngineeringFake(DeviceSimulator):
    """Synthetic counts and cadence for UI exercise. No hardware timing model.

    Test hooks (all synthetic): ``dead_slots`` (LED never steps), ``saturate`` (set of
    (slot, ain) pinned at the ADC rail), ``drop_cycles`` (cycle numbers never sent, leaving a
    sequence discontinuity), ``info_extra`` (appended to the INFO text), ``leak`` (slot ->
    contralateral step in counts).

    ``profile_model=True`` models firmware with the Calib Stage-1 FIX1 profile report (a MODEL written from
    the patch sources, not the firmware): get-profile / set-profile / clear-profile, the extra INFO tokens
    and a START whose outcome is ``start_outcome`` ("ok", "profile_not_applied", "start_failed" or
    "start_cancelled"; the three failures follow the START ACK, as on the device). ``stop_evidence`` is the
    evidence class reported after a host STOP. Without ``profile_model`` the fake behaves like firmware
    that does not know the commands.
    """

    dead_slots: frozenset = frozenset()
    saturate: frozenset = frozenset()
    drop_cycles: frozenset = frozenset()
    leak: dict = {}
    info_extra: str = ""

    def __init__(self, *args, **kwargs):
        opts = {k: kwargs.pop(k) for k in ("dead_slots", "saturate", "drop_cycles", "leak",
                                           "info_extra", "battery_mode", "battery",
                                           "battery_age_override_ms", "profile_model", "start_outcome",
                                           "stop_evidence") if k in kwargs}
        super().__init__(*args, **kwargs)
        self.geometry = kwargs.get('geometry')
        for k, v in opts.items():
            setattr(self, k, v)
        self.battery_mode = opts.get("battery_mode", "ok")  # the UI fake models battery-capable firmware
        self._rng = random.Random(20261109)
        self._cycle = 0
        self._pending: list[bytes] = []
        # FIX1 profile model (only used when profile_model is set)
        self._profile = None            # (id, hash bytes, codes) when armed
        self._applied: dict | None = None
        self._fail_at: float | None = None
        self.profile_requests = 0

    profile_model = False
    start_outcome = "ok"
    stop_evidence = "VALID_MATCH"

    def _execute(self, req, op: int, args: bytes) -> tuple[bytes, bool]:
        if op == OP_INFO and not args:
            rates = ','.join(str(rate) for rate in range(FS_MIN, FS_MAX + 1))
            info = (f"Stage D RAW loopback fixture;frames=RAW_ENGINEERING;"
                    f"fs_allowed={rates};mode={self.mode};sim_model=stage-d-fixture{self.info_extra}")
            if self.profile_model:
                info += self._profile_info_tokens()
            return self._reply(req.req_id, op, STATUS_OK, info.encode('ascii')), False
        if self.profile_model:
            handled = self._execute_profile(req, op, args)
            if handled is not None:
                return handled
        return super()._execute(req, op, args)

    # -- FIX1 profile model (a MODEL of the patch sources) --------------------------------------
    def _armed_codes(self) -> list:
        return list(self._profile[2]) if self._profile else [133] * IREF_SLOTS

    def _profile_info_tokens(self) -> str:
        a = self._applied or {}
        return (";legacy_current=RECORDED_ONLY;iref_code_default=133;iref_code_max=222;iref_ua_per_lsb=225"
                f";wire_minor=1;profile_api=IREF1;profile_state={self._profile_state()}"
                f";profile_source={a.get('source', 'NONE')};applied_profile_id=0x{a.get('id', 0):08X}"
                f";applied_valid={int(bool(a.get('valid')))};start_result={a.get('start_result', 'NONE')}")

    def _profile_state(self) -> str:
        a = self._applied or {}
        if not self._profile:
            return "NONE"
        if a.get("source") == "PROFILE" and a.get("id") == self._profile[0]:
            if a.get("valid"):
                return "APPLIED"
            if a.get("start_result") == "PROFILE_NOT_APPLIED":
                return "NOT_APPLIED"
        return "ARMED"

    def _profile_fields(self) -> dict:
        a = self._applied or {}
        armed = self._profile is not None
        codes = a.get("codes")
        return dict(
            state=self._profile_state(), armed=armed, profile_id=self._profile[0] if armed else 0,
            profile_hash=self._profile[1] if armed else None, profile_code=self._profile[2] if armed else None,
            source=a.get("source", "NONE"), start_result=a.get("start_result", "NONE"),
            mismatch_slot=a.get("mismatch_slot", -1), applied_profile_id=a.get("id", 0),
            applied_profile_hash=a.get("hash"), requested_code=codes, applied_code=a.get("applied"),
            applied_read=bool(a.get("applied")), applied_valid=bool(a.get("valid")),
            applied_at_stop=a.get("at_stop"), stop_evidence=a.get("stop_evidence", "NONE"),
            stop_slot=a.get("stop_slot", -1), stop_error=a.get("stop_error", 0))

    def _execute_profile(self, req, op: int, args: bytes):
        rid = req.req_id
        if op == OP_GET_PROFILE:
            if args:
                return self._reply(rid, op, STATUS_INVALID_ARG), False
            self.profile_requests += 1
            return self._reply(rid, op, STATUS_OK, encode_profile_text(**self._profile_fields())), False
        if op == OP_SET_PROFILE:
            codes = list(args[36:]) if len(args) == PROFILE_ARG_BYTES else []
            ident = int.from_bytes(args[:4], "little") if len(args) >= 4 else 0
            if (len(args) != PROFILE_ARG_BYTES or ident == 0
                    or any(not IREF_CODE_MIN <= c <= IREF_CODE_MAX for c in codes)):
                return self._reply(rid, op, STATUS_INVALID_ARG), True     # never clamped
            if self.state != ST_STOPPED:
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            self._profile = (ident, bytes(args[4:36]), tuple(codes))
            return self._reply(rid, op, STATUS_OK), True
        if op == OP_CLEAR_PROFILE:
            if args:
                return self._reply(rid, op, STATUS_INVALID_ARG), True
            if self.state != ST_STOPPED:
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            self._profile = None
            return self._reply(rid, op, STATUS_OK), True
        if op == OP_START and not args and self.state == ST_STOPPED:
            codes = self._armed_codes()
            source = "PROFILE" if self._profile else "DEFAULT"
            self._applied = dict(source=source, id=self._profile[0] if self._profile else 0,
                                 hash=self._profile[1] if self._profile else None, codes=codes,
                                 start_result="NONE", applied=None, valid=False, stop_evidence="NONE")
            if self.start_outcome == "ok":
                self._applied.update(start_result="OK", applied=codes, valid=True)
                return None                                   # the normal START path of the base model
            # The ACK precedes the apply: STARTING now, the refusal or failure a moment later.
            now = self._clock()
            self.state, self.session_id = ST_STARTING, self._next_session
            self._next_session += 1
            self._owner_active_at, self._grace_started_at = now, None
            self._fail_at = now + 0.3
            return self._reply(rid, op, STATUS_OK), True
        return None

    def poll(self) -> None:
        if self._fail_at is not None and self._clock() >= self._fail_at:
            self._fail_at = None
            outcome, a = self.start_outcome, self._applied
            self.session_id = 0
            if outcome == "profile_not_applied":
                a.update(start_result="PROFILE_NOT_APPLIED", mismatch_slot=6, applied=[c + 1 if i == 6 else c
                                                                              for i, c in enumerate(a["codes"])])
                self.state = ST_STOPPED
            elif outcome == "start_failed":
                a.update(start_result="START_FAILED", applied=a["codes"], stop_evidence="SKIPPED_EMERGENCY")
                self.state, self.fault = ST_FAULTED, 1
            else:
                a.update(start_result="START_CANCELLED", applied=a["codes"], stop_evidence="SKIPPED_EMERGENCY")
                self.state = ST_STOPPED
            self.last_end_reason = "START_" + outcome.upper()
        super().poll()

    def _end_stream(self, reason: str) -> None:
        super()._end_stream(reason)
        a = self._applied
        if self.profile_model and a and a.get("valid"):
            a["valid"] = False                            # the live-session layer: never valid after STOP
            evidence = self.stop_evidence if reason == "HOST_STOP" else "SKIPPED_EMERGENCY"
            a["stop_evidence"] = evidence
            if evidence in ("VALID_MATCH", "VALID_MISMATCH"):
                at_stop = list(a["codes"])
                if evidence == "VALID_MISMATCH":
                    at_stop[6] += 1
                    a["stop_slot"] = 6
                a["at_stop"] = at_stop
            if evidence == "TRANSPORT_ERR":
                a["stop_error"] = 263

    # -- synthetic signal ------------------------------------------------
    def _base(self, slot: int, ain: int) -> float:
        geo = self.geometry
        if geo is None or slot in self.dead_slots:
            return 0.0
        ch = geo.channel_for(slot, ain)
        if ch is None:
            step = self.leak.get(slot, 0.0) if geo.is_contralateral(slot, ain) else 0.0
            return float(step)
        return 60000.0 * math.exp(-(ch.sd_mm - 5.0) / 6.0)

    def _modulation(self, slot: int, ain: int, t: float) -> float:
        wl850 = slot % 2
        phase = 0.7 * ain + 0.2 * (slot // 2)
        amp = 0.03 if not wl850 else -0.015
        return 1.0 + amp * math.sin(2 * math.pi * t / 12.0 + phase)

    def _cycle_packets(self, cycle: int, host_t: float) -> list[bytes]:
        samples = []
        t = cycle / self.fs_hz
        for slot in range(SLOTS):
            for phase in PLAN:
                row = []
                for ain in range(4):
                    ambient = 1800 + 90 * ain + self._rng.randint(-4, 4)
                    on = phase in (3, 4)
                    v = ambient + (self._base(slot, ain) * self._modulation(slot, ain, t) if on else 0)
                    if (slot, ain) in self.saturate and phase == 4:
                        v = ADC_MAX
                    row.append(max(-ADC_MAX, min(ADC_MAX, int(round(v)))))
                samples.append((row, phase, slot))
        packets = []
        for start in range(0, len(samples), 100):
            chunk = samples[start:start + 100]
            first = (cycle * CYCLE_SAMPLES + start) & 0xffffffff
            payload = struct.pack('<BBHIHHHH', ADC_SOURCE_ADS131, LAYOUT_RAW, 1, first,
                                  len(chunk), 1, self.geometry_map_id, 0)
            for row, phase, slot in chunk:
                payload += b''.join(n.to_bytes(3, 'little', signed=True) for n in row)
                payload += bytes((phase, slot))
            self._msg_seq = (self._msg_seq + 1) & 0xffffffff
            packets.append(encode_frame(payload, simulated=True, session_id=self.session_id,
                                        sequence=self._msg_seq, timestamp_us=int(host_t * 1e6),
                                        flags=FLAG_SIMULATED | FLAG_TS_ESTIMATED))
        return packets

    def emit_due(self, limit: int = 100) -> list[bytes]:
        if self.mode != 'ENGINEERING':
            return super().emit_due(limit)
        self.poll()
        if self.state != ST_STREAMING:
            self._cycle = 0
            self._pending = []
            return []
        now = self._clock()
        packets = []
        while self._next_frame_at <= now and len(self._pending) + len(packets) < limit:
            if self._cycle not in self.drop_cycles:
                packets += self._cycle_packets(self._cycle, self._next_frame_at)
            self._cycle += 1
            self._frame_seq += 1
            self._next_frame_at += 1.0 / self.fs_hz
        return packets
