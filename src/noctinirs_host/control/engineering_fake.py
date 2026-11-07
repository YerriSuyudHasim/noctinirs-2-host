"""Opt-in RAW fixture stream for the desktop loopback, using the B simulator."""

import struct

from ..wire.envelope import FLAG_SIMULATED, FLAG_TS_ESTIMATED
from ..wire.optical import ADC_SOURCE_ADS131, LAYOUT_RAW, PH_OUTSIDE, RAW_SLOT_NONE
from .protocol import OP_INFO, STATUS_OK, ST_STREAMING, encode_frame
from .simulator import DeviceSimulator, FS_MIN, FS_MAX


class EngineeringFake(DeviceSimulator):
    """Synthetic counts and cadence for UI exercise. No hardware timing model."""

    def _execute(self, req, op: int, args: bytes) -> tuple[bytes, bool]:
        if op == OP_INFO and not args:
            rates = ','.join(str(rate) for rate in range(FS_MIN, FS_MAX + 1))
            info = (f"Stage D RAW loopback fixture;frames=RAW_ENGINEERING;"
                    f"fs_allowed={rates};mode={self.mode};sim_model=stage-d-fixture")
            return self._reply(req.req_id, op, STATUS_OK, info.encode('ascii')), False
        return super()._execute(req, op, args)

    def emit_due(self, limit: int = 100) -> list[bytes]:
        if self.mode != 'ENGINEERING':
            return super().emit_due(limit)
        self.poll()
        if self.state != ST_STREAMING:
            return []
        now = self._clock()
        packets = []
        while self._next_frame_at <= now and len(packets) < limit:
            count = 10
            first = (self._frame_seq * count) & 0xffffffff
            payload = struct.pack('<BBHIHHHH', ADC_SOURCE_ADS131, LAYOUT_RAW, 1,
                                  first, count, 1, self.geometry_map_id, 0)
            for index in range(count):
                value = (first + index) % 200 - 100
                payload += b''.join(n.to_bytes(3, 'little', signed=True)
                                    for n in (value, -value, value * 2, -value * 2))
                payload += bytes((PH_OUTSIDE, RAW_SLOT_NONE))
            self._msg_seq = (self._msg_seq + 1) & 0xffffffff
            packets.append(encode_frame(payload, simulated=True, session_id=self.session_id,
                                        sequence=self._msg_seq, timestamp_us=int(self._next_frame_at * 1e6),
                                        flags=FLAG_SIMULATED | FLAG_TS_ESTIMATED))
            self._frame_seq += 1
            self._next_frame_at += 0.1
        return packets
