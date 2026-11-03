"""Frame receiver: datagram -> parsed OPT2 -> per-channel buffers, with honest counters.

Loss is counted in the `frame_sequence` domain only (the envelope `msg_sequence` counts
every message and is not a loss counter). Late frames that were counted as lost are
recognised, counted as `late_recovered`, and NOT inserted into the plots: the display
stays in arrival order and the gap stays a gap (documented limit of this slice).
"""

from __future__ import annotations

from ..geometry import Geometry
from ..wire.counters import RejectCounters
from ..wire.envelope import MSG_FRAME, parse_envelope
from ..wire.errors import WireError
from ..wire.optical import Opt2Frame
from .model import WAVELENGTHS, ChannelBuffer, LiveChannel, Point, build_channels, intensity

DEFAULT_CAPACITY = 2000
MAX_TRACKED_MISSING = 256  # bigger jumps are a resync: counted as lost, not tracked


class FrameReceiver:
    def __init__(self, geometry: Geometry, capacity: int = DEFAULT_CAPACITY) -> None:
        self.geometry = geometry
        self.channels: tuple[LiveChannel, ...] = build_channels(geometry)
        self.buffers = [ChannelBuffer(capacity) for _ in self.channels]
        self.rejects = RejectCounters()
        self.reset()

    def reset(self) -> None:
        """New session: clear plots and sequence tracking (reject counters stay cumulative)."""
        for b in self.buffers:
            b.clear()
        self.session_id: int | None = None
        self.simulated: bool | None = None  # False/None until a frame says otherwise
        self.ts_estimated = False
        self.t0_us: int | None = None
        self.last_seq: int | None = None
        self.last_epoch: int | None = None
        self._missing: set[int] = set()
        self.counters = dict(frames=0, lost=0, duplicate=0, late_recovered=0, non_science=0,
                             geometry_mismatch=0, epoch_changes=0, not_frame=0, raw_packets=0,
                             rejected=0)

    @property
    def last_t_s(self) -> float | None:
        pts = self.buffers[0].points(WAVELENGTHS[0]) if self.buffers else []
        return pts[-1].t_s if pts else None

    def feed(self, datagram: bytes) -> str:
        """Returns what happened: ok | not_frame | rejected | duplicate | late | geometry_mismatch | raw."""
        try:
            env = parse_envelope(datagram)
        except WireError as e:
            self.rejects.record_error(e)
            self.counters["rejected"] += 1
            return "rejected"
        if env.msg_type != MSG_FRAME:
            self.counters["not_frame"] += 1
            return "not_frame"
        frame = self.rejects.parse(env.payload)
        if frame is None:
            self.counters["rejected"] += 1
            return "rejected"
        if not isinstance(frame, Opt2Frame):
            self.counters["raw_packets"] += 1  # raw ADC packets are not plotted here
            return "raw"
        if frame.geometry_map_id != self.geometry.geometry_map_id:
            # The channel map may be wrong for this device: fail closed, show nothing.
            self.counters["geometry_mismatch"] += 1
            return "geometry_mismatch"

        if self.session_id != env.session_id:
            self.reset()
            self.session_id = env.session_id
        self.simulated = env.simulated if self.simulated is None else (self.simulated and env.simulated)
        self.ts_estimated = self.ts_estimated or env.ts_estimated
        if self.t0_us is None:
            self.t0_us = env.timestamp_us

        seq = frame.frame_sequence
        if self.last_seq is not None:
            if seq in self._missing:
                self._missing.discard(seq)
                self.counters["lost"] -= 1
                self.counters["late_recovered"] += 1
                return "late"
            if seq <= self.last_seq:
                self.counters["duplicate"] += 1
                return "duplicate"
            gap = seq - self.last_seq - 1
            if gap:
                self.counters["lost"] += gap
                if gap <= MAX_TRACKED_MISSING:
                    self._missing.update(range(self.last_seq + 1, seq))
                    if len(self._missing) > MAX_TRACKED_MISSING:
                        self._missing = set(sorted(self._missing)[-MAX_TRACKED_MISSING:])
                else:
                    self._missing.clear()
        self.last_seq = seq
        if self.last_epoch is not None and frame.slot_plan_epoch != self.last_epoch:
            self.counters["epoch_changes"] += 1
        self.last_epoch = frame.slot_plan_epoch
        self.counters["frames"] += 1
        if not frame.is_science:
            self.counters["non_science"] += 1

        t_s = (env.timestamp_us - self.t0_us) / 1e6
        for ch, buf in zip(self.channels, self.buffers):
            for wl, slot in zip(WAVELENGTHS, ch.slots):
                value, tier = intensity(frame, slot, ch.ain)
                buf.push(wl, Point(seq, t_s, value, tier))
        return "ok"

    def summary(self) -> dict[str, int]:
        """Flat counters for a status line."""
        out = dict(self.counters)
        out["reject_total"] = sum(self.rejects.rejected.values())
        out["slots_invalid"] = sum(self.rejects.slot_invalid.values())
        return out
