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
from .protocol import OP_INFO, STATUS_OK, ST_STREAMING, encode_frame
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
    """

    dead_slots: frozenset = frozenset()
    saturate: frozenset = frozenset()
    drop_cycles: frozenset = frozenset()
    leak: dict = {}
    info_extra: str = ""

    def __init__(self, *args, **kwargs):
        opts = {k: kwargs.pop(k) for k in ("dead_slots", "saturate", "drop_cycles", "leak",
                                           "info_extra", "battery_mode", "battery",
                                           "battery_age_override_ms") if k in kwargs}
        super().__init__(*args, **kwargs)
        self.geometry = kwargs.get('geometry')
        for k, v in opts.items():
            setattr(self, k, v)
        self.battery_mode = opts.get("battery_mode", "ok")  # the UI fake models battery-capable firmware
        self._rng = random.Random(20261109)
        self._cycle = 0
        self._pending: list[bytes] = []

    def _execute(self, req, op: int, args: bytes) -> tuple[bytes, bool]:
        if op == OP_INFO and not args:
            rates = ','.join(str(rate) for rate in range(FS_MIN, FS_MAX + 1))
            info = (f"Stage D RAW loopback fixture;frames=RAW_ENGINEERING;"
                    f"fs_allowed={rates};mode={self.mode};sim_model=stage-d-fixture{self.info_extra}")
            return self._reply(req.req_id, op, STATUS_OK, info.encode('ascii')), False
        return super()._execute(req, op, args)

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
