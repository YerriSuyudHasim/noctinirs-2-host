"""In-process device simulator for host development and tests.

This is a MODEL written from the pinned firmware documents (see protocol.py), not
the firmware and not evidence about a real device. Every reply carries SIMULATED.
Where the documents do not fix a behaviour (for example the order of argument and
state checks) the choice is marked `MODEL CHOICE` in the code and tests only
assert it as simulator behaviour.
"""

from __future__ import annotations

import struct
import time
from collections import OrderedDict
from typing import Callable

from ..geometry import Geometry
from ..live.synth import SAMPLE_COUNT, SignalModel, build_opt2_payload
from ..wire import optical as opt
from ..wire.envelope import PROTOCOL_VERSION
from ..wire.errors import WireError
from .protocol import (
    FIRMWARE_PIN, LED_CHANNELS, LED_MAX_MA, MUTATING_OPS, OP_CLEAR_FAULT, OP_GET_CONFIG,
    OP_GET_TIME, OP_HEARTBEAT, OP_INFO, OP_SAFE_OFF, OP_SET_FS, OP_SET_LED_CURRENT, OP_START,
    OP_STATUS, OP_STOP, ST_FAULTED, ST_SAFE_OFF, ST_STOPPED, ST_STREAMING, STATUS_BAD_FRAME,
    STATUS_INVALID_ARG, STATUS_INVALID_STATE, STATUS_OK, STATUS_PRECONDITION_FAILED,
    STATUS_RESERVED_FLAGS, STATUS_UNKNOWN_COMMAND, TIMED_OPS, decode_request, encode_frame,
    encode_reply,
)

FS_MIN, FS_MAX = 2, 10  # docs/LED_HOST_CONTROL.md (integer Hz)
DEFAULT_FS, DEFAULT_MA = 5, 30  # ADR-0005
SILENCE_S = 1.5  # owner silence before grace starts (local implementation record)
GRACE_S = 3.0  # grace before the simulated stream is ended
CACHE_ENTRIES = 4


class DeviceSimulator:
    """Handles one datagram at a time; time comes from an injectable clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, boot_id: int = 0x5151_0001,
                 geometry: Geometry | None = None, seed: int = 1) -> None:
        self._clock = clock
        self.model = SignalModel(geometry, seed)
        self.geometry_map_id = geometry.geometry_map_id if geometry is not None else 1
        # Test / demo knobs for the frame stream (simulator only)
        self.dead_detectors: set[int] = set()  # AIN indexes reported as not verified
        self.warmup_frames = 0  # first N frames of a session carry FF_WARMUP (not science)
        self.frames_sent = 0
        self._frame_seq = 0
        self._next_frame_at = 0.0
        self._t0 = 0.0
        self.boot_id = boot_id
        self.state = ST_STOPPED
        self.fault = 0
        self.fs_hz = DEFAULT_FS
        self.current_ma = [DEFAULT_MA] * LED_CHANNELS
        self.session_id = 0
        self._next_session = 1
        self._owner_active_at = 0.0
        self._grace_started_at: float | None = None
        self.last_end_reason: str | None = None
        self.fault_active = False  # test hook: clear_fault fails while True
        self._cache: OrderedDict[int, tuple[int, bytes, int, bytes]] = OrderedDict()
        self._msg_seq = 0
        self.counters = dict(requests=0, duplicates=0, grace_expirations=0, malformed=0,
                             reserved_flags_rejects=0)

    # --- time and link supervision -------------------------------------
    def _now_us(self) -> int:
        return int(self._clock() * 1_000_000)

    def poll(self) -> None:
        """Advance supervision (silence -> grace -> LINK_LOSS stop). Call before replies."""
        if self.state != ST_STREAMING:
            return
        now = self._clock()
        # Deadlines are absolute (local implementation record, refinement 4): grace
        # starts at the silence deadline, however late this poll runs.
        silence_deadline = self._owner_active_at + SILENCE_S
        if self._grace_started_at is None and now >= silence_deadline:
            self._grace_started_at = silence_deadline
        if self._grace_started_at is not None and now >= self._grace_started_at + GRACE_S:
            self.counters["grace_expirations"] += 1
            self._end_stream("LINK_LOSS")

    def _end_stream(self, reason: str) -> None:
        self.state = ST_STOPPED
        self.session_id = 0
        self._grace_started_at = None
        self.last_end_reason = reason

    @property
    def grace_active(self) -> bool:
        return self.state == ST_STREAMING and self._grace_started_at is not None

    # --- datagram entry point --------------------------------------------
    def handle(self, datagram: bytes) -> list[bytes]:
        """Return the reply datagrams for one request (empty list = silently dropped)."""
        self.poll()
        try:
            req = decode_request(datagram)
        except WireError:
            self.counters["malformed"] += 1
            return []
        self.counters["requests"] += 1
        # MODEL CHOICE: BAD_FRAME (protocol mismatch) is checked before RESERVED_FLAGS;
        # the documents do not order the two.
        if req.protocol_version != PROTOCOL_VERSION:
            return [self._reply(req.req_id, req.opcode, STATUS_BAD_FRAME)]
        if req.flags & 0xFFFE:  # host bit0 ignored; bit1 and bits 2..15 rejected
            self.counters["reserved_flags_rejects"] += 1
            return [self._reply(req.req_id, req.opcode, STATUS_RESERVED_FLAGS)]
        return [self._dispatch(req)]

    # --- dispatch --------------------------------------------------------------
    def _dispatch(self, req) -> bytes:
        op, args = req.opcode, req.args
        # MODEL CHOICE: no per-endpoint high-water mark for mutation request ids. The local
        # implementation record (refinement 2) adds one in the firmware; this model only
        # keeps the 4-entry exact-match cache, so a stale mutation after cache eviction
        # is executed here but would be rejected by the firmware.
        if op in MUTATING_OPS:
            hit = self._cache.get(req.req_id)
            if hit is not None:
                c_op, c_args, c_session, c_reply = hit
                if (c_op, c_args, c_session) == (op, args, req.session_id):
                    self.counters["duplicates"] += 1
                    self._refresh_if_owner(req.session_id)
                    return c_reply
                return self._reply(req.req_id, op, STATUS_INVALID_ARG)
        reply, cacheable = self._execute(req, op, args)
        # MODEL CHOICE: replies to rejected mutations (INVALID_ARG / INVALID_STATE /
        # PRECONDITION_FAILED) are cached like successful ones; the documents only say that
        # exact cached acknowledgements are returned, not which outcomes are cached.
        if cacheable and op in MUTATING_OPS:
            self._cache[req.req_id] = (op, args, req.session_id, reply)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return reply

    # --- frame stream -------------------------------------------------------
    def emit_due(self, limit: int = 100) -> list[bytes]:
        """FRAME datagrams that fall due by now (empty unless STREAMING). At most `limit`
        per call; a long stall is caught up on the next calls, never skipped."""
        self.poll()
        out: list[bytes] = []
        if self.state != ST_STREAMING:
            return out
        now = self._clock()
        period = 1.0 / self.fs_hz
        while self._next_frame_at <= now and len(out) < limit:
            t = self._next_frame_at - self._t0
            specs = [self.model.slot_spec(s, t, self.current_ma[s], frozenset(self.dead_detectors))
                     for s in range(opt.SLOT_COUNT)]
            flags = opt.FF_WARMUP if self._frame_seq < self.warmup_frames else 0
            payload = build_opt2_payload(specs, frame_sequence=self._frame_seq,
                                         first_sample=self._frame_seq * SAMPLE_COUNT,
                                         geometry_map_id=self.geometry_map_id, frame_flags=flags)
            self._msg_seq = (self._msg_seq + 1) & 0xFFFFFFFF
            out.append(encode_frame(payload, simulated=True, session_id=self.session_id,
                                    sequence=self._msg_seq,
                                    timestamp_us=int(self._next_frame_at * 1_000_000)))
            self._frame_seq += 1
            self.frames_sent += 1
            self._next_frame_at += period
        return out

    def _is_owner(self, session_id: int) -> bool:
        return self.state == ST_STREAMING and session_id == self.session_id and self.session_id != 0

    def _refresh_if_owner(self, session_id: int) -> None:
        if self._is_owner(session_id):
            self._owner_active_at = self._clock()
            self._grace_started_at = None

    def _execute(self, req, op: int, args: bytes) -> tuple[bytes, bool]:
        rid = req.req_id
        # Zero-argument commands take no argument bytes (local implementation record table).
        # MODEL CHOICE: trailing bytes -> INVALID_ARG, except safe_off, which is never
        # refused (it ignores extra bytes so that turning everything off cannot fail on framing).
        if op in (OP_INFO, OP_GET_CONFIG, OP_START, OP_STOP, OP_CLEAR_FAULT) and args:
            return self._reply(rid, op, STATUS_INVALID_ARG), op in MUTATING_OPS
        if op == OP_SAFE_OFF:  # accepted in every state, never cached
            was = self.state
            self.state, self.session_id, self._grace_started_at = ST_SAFE_OFF, 0, None
            if was == ST_STREAMING:
                self.last_end_reason = "SAFE_OFF"
            return self._reply(rid, op, STATUS_OK), False
        if op == OP_INFO:
            # MODEL CHOICE: the field list is the documented subset only (frames/capture
            # from the local implementation record; duration, limit_s, single_run, fs_min,
            # fs_max from LED_HOST_CONTROL.md) plus a `sim_model` tag. The firmware INFO text
            # has more fields (for example short_policy and i2c) that this model omits.
            text = (f"frames=unsupported;capture=unsupported;duration=HOST;limit_s=0;single_run=0;"
                    f"fs_min={FS_MIN};fs_max={FS_MAX};sim_model={FIRMWARE_PIN[:7]}")
            return self._reply(rid, op, STATUS_OK, text.encode("ascii")), False
        if op == OP_GET_CONFIG:
            ext = struct.pack("<BB20H", self.fs_hz, LED_CHANNELS, *self.current_ma)
            return self._reply(rid, op, STATUS_OK, ext), False
        if op in TIMED_OPS:
            if len(args) != 8:
                return self._reply(rid, op, STATUS_INVALID_ARG), False
            if op == OP_HEARTBEAT:
                if not self._is_owner(req.session_id):
                    return self._reply(rid, op, STATUS_INVALID_STATE), False
                self._refresh_if_owner(req.session_id)
            t_rx = self._now_us()
            ext = args + struct.pack("<QQ", t_rx, t_rx)
            if op == OP_STATUS:
                c = self.counters
                ext += struct.pack("<5IB", c["requests"], c["duplicates"], c["grace_expirations"],
                                   c["malformed"], c["reserved_flags_rejects"],
                                   1 if self.grace_active else 0)
            return self._reply(rid, op, STATUS_OK, ext), False
        if op == OP_START:
            if self.state != ST_STOPPED:
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            self.state, self.session_id = ST_STREAMING, self._next_session
            self._next_session += 1
            self._owner_active_at, self._grace_started_at = self._clock(), None
            self.last_end_reason = None
            # MODEL CHOICE: frame_sequence restarts at 0 for each session; the first frame
            # is due one period after START.
            self._frame_seq, self._t0 = 0, self._clock()
            self._next_frame_at = self._t0 + 1.0 / self.fs_hz
            return self._reply(rid, op, STATUS_OK), True
        if op == OP_STOP:
            if not self._is_owner(req.session_id):
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            ended = self.session_id
            self._end_stream("HOST_STOP")
            return self._reply(rid, op, STATUS_OK, session_id=ended), True
        if op == OP_CLEAR_FAULT:
            if self.state not in (ST_SAFE_OFF, ST_FAULTED):
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            if self.fault_active:
                return self._reply(rid, op, STATUS_PRECONDITION_FAILED), True
            self.state, self.fault = ST_STOPPED, 0
            return self._reply(rid, op, STATUS_OK), True
        if op == OP_SET_FS:  # MODEL CHOICE: argument check before state check
            if len(args) != 1 or not FS_MIN <= args[0] <= FS_MAX:
                return self._reply(rid, op, STATUS_INVALID_ARG), True
            if self.state != ST_STOPPED:
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            self.fs_hz = args[0]
            return self._reply(rid, op, STATUS_OK), True
        if op == OP_SET_LED_CURRENT:
            if len(args) != 3:
                return self._reply(rid, op, STATUS_INVALID_ARG), True
            ch, ma = struct.unpack("<BH", args)
            if ch >= LED_CHANNELS or ma > LED_MAX_MA:
                return self._reply(rid, op, STATUS_INVALID_ARG), True
            if self.state != ST_STOPPED:
                return self._reply(rid, op, STATUS_INVALID_STATE), True
            self.current_ma[ch] = ma
            return self._reply(rid, op, STATUS_OK), True
        return self._reply(rid, op, STATUS_UNKNOWN_COMMAND), False

    def _reply(self, req_id: int, opcode: int, status: int, extension: bytes = b"", *,
               session_id: int | None = None) -> bytes:
        self._msg_seq = (self._msg_seq + 1) & 0xFFFFFFFF
        now = self._now_us()
        return encode_reply(req_id, opcode, status, state=self.state, fault=self.fault,
                            simulated=True, boot_id=self.boot_id, t_rx_us=now,
                            extension=extension,
                            session_id=self.session_id if session_id is None else session_id,
                            sequence=self._msg_seq, timestamp_us=now)
