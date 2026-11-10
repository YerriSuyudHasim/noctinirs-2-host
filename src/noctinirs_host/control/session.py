"""Session controller: the single owner of what the host believes about the device.

Rules (issue #24, firmware ADR-0004):
* Settings are validated locally first; an invalid value sends NO packet.
* Every user command (including local rejections and failures), every state change and
  every link event is appended to `events`. Successful periodic traffic (heartbeats and
  status polls) is counted but kept out of the log. Nothing is changed silently.
* `safe_off` is never blocked by local state and is retried harder than other commands.
* Automatic traffic is read-only or keep-alive: owner heartbeats in active states,
  status polling during transitions and while idle. START is always explicit.
* After an ambiguous START, even read-only traffic can renew the peer's lease.
  Suspend transmission until explicit recovery.
"""

from __future__ import annotations

import time
import math
import secrets
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..wire.envelope import MSG_FRAME, parse_envelope
from ..wire.errors import WireError
from ..wire.optical import LAYOUT_RAW, parse_raw
from .protocol import (
    LED_CHANNELS, LED_MAX_MA, OP_CLEAR_FAULT, OP_GET_BATTERY, OP_GET_CONFIG, OP_GET_PROFILE, OP_GET_TIME, OP_HEARTBEAT, OP_INFO, OP_SAFE_OFF,
    OP_SET_FS, OP_SET_LED_CURRENT, OP_SET_MODE, OP_START, OP_STATUS, OP_STOP, ST_STOPPED,
    ST_STARTING, ST_STOPPING,
    ST_FAULTED, ST_SAFE_OFF, ST_STREAMING, STATUS_UNKNOWN_COMMAND, TIMED_OPS, Reply, args_host_time, args_set_fs, args_set_led_current,
    decode_reply, encode_request, state_name,
)
from .profile import ProfileReport

from .battery import QUERY_TIMEOUT_S as BATTERY_TIMEOUT_S, BatteryMonitor

PROFILE_TIMEOUT_S = 0.4  # one read-only get-profile attempt (a reply is at most ~1.4 kB); no retries
HEARTBEAT_PERIOD_S = 0.5  # local implementation record
STATUS_PERIOD_S = 2.0  # host-chosen: keeps the displayed state fresh while not streaming
TRANSITION_POLL_S = 0.05  # nn2ctl stop confirmation poll cadence
START_REPLY_WINDOW_S = 1.5  # bounded original transaction, at most the owner silence interval
START_QUIET_S = 1.5 + 3.0  # firmware silence + grace; a wait is not STOP evidence
OWNED_STATES = (ST_STARTING, ST_STREAMING, ST_STOPPING)
# B-01: a reply with session id 0 is accepted for a held session only in one of these explicit terminal
# states and only for these read-only observation commands (see SessionController._accept).
TERMINAL_STATES = (ST_STOPPED, ST_FAULTED, ST_SAFE_OFF)
SESSION_GONE_OPS = (OP_STATUS, OP_INFO, OP_GET_CONFIG)


class Transport(Protocol):
    def send(self, datagram: bytes) -> None: ...
    def receive(self, timeout_s: float) -> list[bytes]: ...
    def open(self) -> None: ...
    def close(self) -> None: ...


class LoopbackTransport:
    """Connects a controller to an in-process DeviceSimulator. No sockets."""

    def __init__(self, simulator) -> None:
        self.simulator = simulator
        self._queue: list[bytes] = []
        self.drop_next_replies = 0  # test hook: lose replies
        self.duplicate_next_reply = False  # test hook: deliver a reply twice
        self.drop_next_frames = 0  # test hook: lose FRAME datagrams
        self.duplicate_next_frame = False  # test hook: deliver a frame twice
        self.reorder_next_frames = False  # test hook: swap the next two frames
        self._held: bytes | None = None

    def send(self, datagram: bytes) -> None:
        for reply in self.simulator.handle(datagram):
            if self.drop_next_replies:
                self.drop_next_replies -= 1
                continue
            self._queue.append(reply)
            if self.duplicate_next_reply:
                self.duplicate_next_reply = False
                self._queue.append(reply)

    def _pull_frames(self) -> None:
        for fr in self.simulator.emit_due():
            if self.drop_next_frames:
                self.drop_next_frames -= 1
                continue
            if self.reorder_next_frames:
                if self._held is None:
                    self._held = fr  # deliver it after the next frame
                    continue
                self._queue.extend([fr, self._held])
                self._held, self.reorder_next_frames = None, False
                continue
            self._queue.append(fr)
            if self.duplicate_next_frame:
                self.duplicate_next_frame = False
                self._queue.append(fr)

    def receive(self, timeout_s: float) -> list[bytes]:
        if not self._queue:  # replies queued by send() always go first
            self._pull_frames()
        return [self._queue.pop(0)] if self._queue else []

    def open(self) -> None:
        pass

    def close(self) -> None:
        self._queue.clear()
        self._held = None


@dataclass(frozen=True)
class Event:
    t: float
    kind: str  # "command" | "state" | "link" | "info"
    text: str
    simulated: bool = True


@dataclass(frozen=True)
class CommandResult:
    command: str
    ok: bool
    sent: bool  # at least one request attempt was accepted by the transport
    status: str  # device status name, or LOCAL_REJECT / TIMEOUT / IO_ERROR
    message: str
    reply: Reply | None = None


@dataclass
class Settings:
    fs_hz: int | None = None
    current_ma: list[int] = field(default_factory=list)
    fs_min: int | None = None
    fs_max: int | None = None
    fs_allowed: tuple[int, ...] = ()
    mode: str | None = None
    current_max_ma: int = LED_MAX_MA


class SessionController:
    def __init__(self, transport: Transport, clock: Callable[[], float] = time.monotonic, *,
                 retries: int = 2, timeout_s: float = 0.5) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout must be positive and finite")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10:
            raise ValueError("retries must be 0..10")
        self._t = transport
        self._clock = clock
        self.retries, self.timeout_s = retries, timeout_s
        self.connected = False
        self.simulated: bool | None = None
        self.device_state: int | None = None
        self.fault_code = 0
        self.boot_id: int | None = None
        self.session_id = 0
        self.settings = Settings()
        self.info: dict[str, str] = {}
        self._connected_info: Reply | None = None
        self.events: list[Event] = []
        self.listeners: list[Callable[[Event], None]] = []
        self.counters = dict(sent=0, retries=0, timeouts=0, unmatched=0, duplicate_replies=0,
                             malformed=0, local_rejects=0, frames_routed=0, frames_unrouted=0,
                             wrong_session=0, raw_routed=0, io_errors=0)
        self._next_id = secrets.randbelow(0x7fffffff) + 1  # reference client watermark strategy
        self._seq = 0
        self._last_hb = 0.0
        self._last_status = 0.0
        self._done: set[int] = set()
        self.last_end: str | None = None
        self._rebooted = False
        # FRAME datagrams go here, untouched; the live-view receiver does the parsing.
        self.frame_sink: Callable[[bytes], object] | None = None
        # Validated RAW datagrams, unchanged. No recorder or implicit storage.
        self.raw_sink: Callable[[bytes], object] | None = None
        self.raw_capture_sink: Callable[[bytes, int, int | None], object] | None = None
        self._busy = False
        self._stop_requested = False
        self._start_probe_at: float | None = None
        # Battery (ADR-0004 section 14). Off by default so the earlier stages are unchanged;
        # EngineeringRun turns it on. Separate counters and log: a battery miss is not a link
        # loss, and these events are not recorded in a RAW sidecar.
        self.battery_enabled = False
        self.battery = BatteryMonitor()
        self.battery_counters = dict(queries=0, timeouts=0, unsupported=0, errors=0)
        self.battery_log: list[str] = []
        # LED calibration profile report (get-profile). Read-only and opt-in like the battery query; the
        # host never sends set-profile / clear-profile by itself.
        self.profile_enabled = False
        self.profile: ProfileReport | None = None
        self.profile_counters = dict(queries=0, timeouts=0, unsupported=0, errors=0)

    # --- bookkeeping ----------------------------------------------------
    def _log(self, kind: str, text: str) -> None:
        ev = Event(self._clock(), kind, text, bool(self.simulated))
        self.events.append(ev)
        for fn in list(self.listeners):
            fn(ev)

    def _set_state(self, reply: Reply) -> None:
        if (self.battery_enabled and self.session_id and self.device_state in OWNED_STATES
                and reply.state == ST_STOPPED):
            self.battery.note_stopped(self._clock())  # the device refreshes ~5 s after cleanup
        if self.device_state is not None and reply.state != self.device_state:
            self._log("state", f"device state {state_name(self.device_state)} -> {reply.state_text}")
        self.device_state, self.fault_code = reply.state, reply.fault
        self.simulated = reply.simulated

    @property
    def state_text(self) -> str:
        return "NOT CONNECTED" if self.device_state is None else state_name(self.device_state)

    @property
    def start_uncertain(self) -> bool:
        return self._start_probe_at is not None

    @property
    def start_recheck_wait_s(self) -> float:
        return 0.0 if self._start_probe_at is None else max(0.0, self._start_probe_at - self._clock())

    def _receive(self, timeout_s: float) -> tuple[list[bytes], int | None]:
        batch = self._t.receive(timeout_s)
        # UDPTransport returns one datagram. Timestamp at userspace delivery,
        # before routing or callbacks; batch-based test transports share this stamp.
        rx_us = time.time_ns() // 1000 if batch and self.raw_capture_sink is not None else None
        return batch, rx_us

    # --- one transaction ----------------------------------------------------
    def _request(self, opcode: int, args: bytes, *, retries: int, timeout_s: float | None = None,
                 count_timeout: bool = True) -> Reply | None:
        wait = self.timeout_s if timeout_s is None else timeout_s
        self._rebooted = False
        rid = self._next_id
        self._next_id = (rid + 1) & 0xFFFFFFFF or 1
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        wire = encode_request(rid, opcode, args, session_id=self.session_id, sequence=self._seq)
        cutoff = time.monotonic() + START_REPLY_WINDOW_S if opcode == OP_START else math.inf
        for attempt in range(retries + 1):
            now = time.monotonic()
            if now >= cutoff:
                break
            deadline = min(now + wait, cutoff)
            self._t.send(wire)
            self.counters["sent"] += 1
            if attempt:
                self.counters["retries"] += 1
            for _ in range(64):  # bounded work AND time under unrelated traffic
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                batch, rx_us = self._receive(remaining)
                if not batch or time.monotonic() >= cutoff:
                    break
                for raw in batch:
                    reply = self._accept(raw, rid, opcode, args, rx_us)
                    if reply is not None:
                        return reply
                    if self._rebooted:
                        return None
        if count_timeout:
            self.counters["timeouts"] += 1
        return None

    def _route_frame(self, raw: bytes, rx_us: int | None) -> bool:
        """True if `raw` is a FRAME datagram (consumed here, never mistaken for a reply)."""
        try:
            env = parse_envelope(raw)
            is_frame = env.msg_type == MSG_FRAME
        except WireError:
            return False  # decode_reply counts it as malformed
        if is_frame:
            # Streams have no req_id/opcode or boot_id. Only the held session on
            # the same endpoint is eligible (nn2ctl Client.request).
            if not self.session_id or env.session_id != self.session_id:
                self.counters["wrong_session"] += 1
                return True
            if len(env.payload) >= 2 and env.payload[1] == LAYOUT_RAW:
                try:
                    parse_raw(env.payload)
                except WireError:
                    self.counters["malformed"] += 1
                    return True
                if self.raw_capture_sink is not None and rx_us is not None:
                    self.counters["raw_routed"] += 1
                    self.raw_capture_sink(raw, rx_us, self.boot_id)
                    return True
                if self.raw_sink is not None:
                    self.counters["raw_routed"] += 1
                    self.raw_sink(raw)
                    return True
            if self.frame_sink is None:
                self.counters["frames_unrouted"] += 1
            else:
                self.counters["frames_routed"] += 1
                self.frame_sink(raw)
        return is_frame

    def _accept(self, raw: bytes, rid: int, opcode: int, args: bytes,
                rx_us: int | None) -> Reply | None:
        if self._route_frame(raw, rx_us):
            return None
        try:
            reply = decode_reply(raw)
        except WireError:
            self.counters["malformed"] += 1
            return None
        if reply.req_id in self._done and reply.req_id != rid:
            self.counters["duplicate_replies"] += 1  # late/duplicate of an old request
            return None
        if reply.req_id != rid or reply.opcode != opcode:
            self.counters["unmatched"] += 1
            return None
        if opcode in TIMED_OPS and reply.ok and len(args) == 8 \
                and reply.t_host_us != int.from_bytes(args, "little"):
            self.counters["unmatched"] += 1
            return None
        # Session-changing ACKs can return a new ID, an old STOP acceptance
        # (STOPPING), or zero after teardown. Errors can report another owner's
        # ID: observe the state, never acquire that ID.
        # get_battery carries the current session id of the device, which may belong to another
        # endpoint. It is data about the device, never a session to adopt or reject.
        # get_profile likewise reports the device's current session, which may be another endpoint's.
        # A START that the device ends after its ACK (FIX1: PROFILE_NOT_APPLIED, START_FAILED, START_CANCELLED)
        # leaves the device with no session in a terminal state. Firmware FIX2 answers a STATUS / INFO /
        # get_config that still carries the ended session id with INVALID_STATE and session 0 (handled below
        # as "session gone"). A peer that answers such a read with OK and session 0 is accepted only for those
        # observation commands and only in an explicit terminal state (STOPPED / FAULTED / SAFE_OFF): that
        # report cannot belong to another owner, and dropping it would turn a refused START into "control
        # poll timed out". Session 0 with BOOT / RECOVERING / an owned state, or on a heartbeat or a setting
        # command, stays a wrong-session reply (compatibility allowance, not a firmware path).
        session_gone = (reply.session_id == 0 and reply.state in TERMINAL_STATES
                        and opcode in SESSION_GONE_OPS)
        if reply.ok and self.session_id and opcode not in (
                OP_START, OP_STOP, OP_SAFE_OFF, OP_CLEAR_FAULT, OP_GET_BATTERY, OP_GET_PROFILE) \
                and reply.session_id != self.session_id and not session_gone:
            self.counters["wrong_session"] += 1
            return None
        if reply.ok and opcode == OP_START and not reply.session_id:
            self.counters["wrong_session"] += 1
            return None
        if reply.ok and opcode == OP_STOP and reply.session_id not in (0, self.session_id):
            self.counters["wrong_session"] += 1
            return None
        if reply.ok and opcode in (OP_SAFE_OFF, OP_CLEAR_FAULT) and reply.session_id:
            self.counters["wrong_session"] += 1
            return None
        if self.boot_id is not None and reply.boot_id != self.boot_id:
            self._log("link", "device rebooted (boot id changed): reconnect required")
            self._forget_link()
            self._t.close()
            self._rebooted = True
            return None
        self.boot_id = reply.boot_id
        self._done.add(rid)
        if len(self._done) > 64:
            self._done.discard(min(self._done))
        return reply

    def _run(self, name: str, opcode: int, args: bytes = b"", *, retries: int | None = None,
             note: str = "", quiet_ok: bool = False, start_probe: bool = False) -> CommandResult:
        if self._busy:
            return self._reject(name, "another transaction is active")
        if self.start_uncertain and opcode != OP_SAFE_OFF and not start_probe:
            return self._reject(name, "START outcome unresolved; use explicit recovery")
        self._busy = True
        try:
            return self._run_transaction(name, opcode, args, retries=self.retries if retries is None else retries,
                                         note=note, quiet_ok=quiet_ok)
        finally:
            self._busy = False

    def _run_transaction(self, name: str, opcode: int, args: bytes, *, retries: int,
                         note: str, quiet_ok: bool) -> CommandResult:
        sent_before = self.counters["sent"]
        try:
            reply = self._request(opcode, args, retries=retries)
        except OSError as exc:
            sent = self.counters["sent"] > sent_before
            self.counters["io_errors"] += 1
            self._forget_link()
            self._t.close()
            self._log("link", f"{name}: transport error ({exc}); reconnect required")
            self._log("command", f"{name}{note}: IO_ERROR ({exc})")
            return CommandResult(name, False, sent, "IO_ERROR", str(exc))
        if reply is None:
            self._log("command", f"{name}{note}: NO REPLY (timeout)")
            return CommandResult(name, False, self.counters["sent"] > sent_before,
                                 "TIMEOUT", "no reply from device")
        self._set_state(reply)
        if opcode in (OP_START, OP_STOP, OP_SAFE_OFF, OP_CLEAR_FAULT) and reply.ok:
            self.session_id = reply.session_id if opcode in (OP_START, OP_STOP) else 0
            if reply.state not in OWNED_STATES:
                self.session_id = 0
        if reply.status == 1 and reply.session_id != self.session_id:  # INVALID_STATE
            self.session_id = 0  # session gone; never adopt another owner's id
        if not (quiet_ok and reply.ok):  # periodic traffic stays out of the log unless it fails
            self._log("command", f"{name}{note}: {reply.status_text} (state {reply.state_text})")
        return CommandResult(name, reply.ok, True, reply.status_text,
                             "ok" if reply.ok else f"device answered {reply.status_text}", reply)

    def _reject(self, name: str, why: str) -> CommandResult:
        self.counters["local_rejects"] += 1
        self._log("command", f"{name}: REJECTED locally, nothing sent ({why})")
        return CommandResult(name, False, False, "LOCAL_REJECT", why)

    # --- public commands ---------------------------------------------------------
    def connect(self) -> CommandResult:
        if self._busy:
            return self._reject("connect", "another transaction is active")
        if self.start_uncertain:
            return self._reject("connect", "START outcome unresolved; use explicit recovery")
        if self.connected:
            return self._reject("connect", "already connected; disconnect first")
        self._forget_link()
        try:
            self._t.open()
        except OSError as exc:
            self._t.close()
            return CommandResult("connect", False, False, "IO_ERROR", str(exc))
        res = self._run("info", OP_INFO)
        if not res.ok:
            self.disconnect()
            return res
        self.info = res.reply.info
        self._connected_info = res.reply
        for key, attr in (("fs_min", "fs_min"), ("fs_max", "fs_max")):
            try:
                setattr(self.settings, attr, int(self.info[key]))
            except (KeyError, ValueError):
                setattr(self.settings, attr, None)
        try:
            values = tuple(int(v) for v in self.info["fs_allowed"].split(","))
            if not values or any(v < 2 or v > 255 for v in values):
                raise ValueError("invalid Fs capability")
            self.settings.fs_allowed = tuple(sorted(set(values)))
        except (KeyError, ValueError):
            self.settings.fs_allowed = ()  # no range or hard-coded fallback
        self.settings.mode = self.info.get("mode")
        try:
            limit = int(self.info["current_max_ma"])
            if 0 <= limit <= LED_MAX_MA:
                self.settings.current_max_ma = limit
        except (KeyError, ValueError):
            pass
        cfg = self._run("get_config", OP_GET_CONFIG)
        if not cfg.ok:
            self.disconnect()
            return cfg
        self.settings.fs_hz = cfg.reply.fs_hz
        self.settings.current_ma = list(cfg.reply.current_ma)
        self.connected = True
        self._last_hb = self._last_status = self._clock()
        self._log("link", "connected" + (" to a SIMULATED device" if self.simulated else ""))
        if self.battery_enabled:
            self.query_battery()  # first reading right after INFO and get_config; failure is not fatal
        return CommandResult("connect", True, True, "OK", "connected", cfg.reply)

    def disconnect(self) -> None:
        if self.connected:
            self._log("link", "disconnected (no command sent; device STOP unconfirmed by disconnect)")
        self._forget_link()
        self._t.close()

    def _forget_link(self) -> None:
        self.connected = False
        self.device_state, self.session_id, self.boot_id = None, 0, None
        self.settings = Settings()
        self.info = {}
        self._connected_info = None
        self._stop_requested = False
        self.battery.reset()

    close = disconnect

    def refresh_info(self) -> CommandResult:
        """Explicit read-only INFO for current identity, mode and RAW diagnostics."""
        if not self.connected:
            return self._reject("info", "not connected")
        res = self._run("info", OP_INFO)
        self._observe_end(res)
        return res

    def refresh_config(self) -> CommandResult:
        """Read the active Fs and currents without writing either setting."""
        if not self.connected:
            return self._reject("get_config", "not connected")
        res = self._run("get_config", OP_GET_CONFIG)
        if res.ok:
            self.settings.fs_hz = res.reply.fs_hz
            self.settings.current_ma = list(res.reply.current_ma)
        self._observe_end(res)
        return res

    def recording_snapshot(self, *, info: CommandResult | None = None,
                           status: CommandResult | None = None) -> dict:
        """Copy current host context and explicitly supplied device observations. No I/O."""
        def observation(result: CommandResult | None, opcode: int) -> Reply | None:
            if result and result.ok and result.reply and result.reply.opcode == opcode:
                if result.reply.boot_id == self.boot_id:
                    return result.reply
            return None

        def reply_context(reply: Reply) -> dict:
            return dict(boot_id=reply.boot_id, session_id=reply.session_id,
                        device_reply_us=reply.device_us, state=reply.state_text)

        fresh_info = observation(info, OP_INFO)
        reported = fresh_info or self._connected_info
        status_reply = observation(status, OP_STATUS)
        info_snapshot = None if reported is None else {
            **reply_context(reported), "source": "INFO" if fresh_info else "connection_INFO",
            "ascii": reported.extension.decode("ascii"), "fields": dict(reported.info),
        }
        raw_diag = None
        if fresh_info is not None:
            raw_diag = {**reply_context(fresh_info), "source": "INFO"}
            for field in ("raw_packets_sent", "raw_dropped_samples", "raw_buffer_highwater"):
                value = fresh_info.info.get(field, "")
                raw_diag[field] = (int(value) if value.isdecimal() and len(value) <= 10
                                   and int(value) <= 0xffffffff else None)
        endpoint = getattr(self._t, "endpoint", None)
        transport_counters = getattr(self._t, "counters", None)
        return {
            "host_snapshot_us": time.time_ns() // 1000, "source_endpoint": endpoint,
            "boot_id": self.boot_id, "owned_session_id": self.session_id or None,
            "device_state": self.state_text if self.connected else None,
            "fault_code": self.fault_code if self.connected else None, "simulated": self.simulated,
            "config": {"fs_hz": self.settings.fs_hz,
                       "mode": fresh_info.info.get("mode") if fresh_info else self.settings.mode,
                       "led_current_ma": list(self.settings.current_ma) or None},
            "observed_info": info_snapshot, "firmware_raw_diagnostics": raw_diag,
            "firmware_status": None if status_reply is None else {
                **reply_context(status_reply), "source": "STATUS", "counters": dict(status_reply.counters),
            },
            "host_controller_counters": dict(self.counters),
            "host_transport_counters": None if transport_counters is None else dict(transport_counters),
        }

    def refresh_status(self, *, quiet: bool = False) -> CommandResult:
        res = self._run("status", OP_STATUS, args_host_time(int(self._clock() * 1e6)),
                        quiet_ok=quiet)
        self._observe_end(res)
        return res

    def _observe_end(self, res: CommandResult) -> None:
        if res.reply and self.device_state not in OWNED_STATES:
            self.session_id = 0
            if self._stop_requested and self.device_state == ST_STOPPED:
                self.last_end = "HOST_STOP"
            self._stop_requested = False

    def get_time(self) -> CommandResult:
        return self._run("get_time", OP_GET_TIME, args_host_time(int(self._clock() * 1e6)))

    def start(self) -> CommandResult:
        if not self.connected:
            return self._reject("start_stream", "not connected")
        if self.device_state != ST_STOPPED:
            return self._reject("start_stream", f"state is {self.state_text}, not STOPPED")
        res = self._run("start_stream", OP_START)
        if res.ok:
            self._last_hb = self._clock()
            self.last_end = None
            self._stop_requested = False
        elif res.sent and res.status in ("TIMEOUT", "IO_ERROR"):
            self._start_probe_at = self._clock() + START_QUIET_S
            self._log("link", "START outcome unresolved; lease-refresh traffic suspended")
        return res

    def _run_recovery(self, name: str, opcode: int, args: bytes = b"", *,
                      retries: int) -> CommandResult:
        """Allow explicit recovery after disconnect or reboot, without normal connect traffic."""
        if self._busy:
            return self._reject(name, "another transaction is active")
        try:
            self._t.open()
        except OSError as exc:
            self.counters["io_errors"] += 1
            self._log("link", f"{name}: IO_ERROR ({exc})")
            return CommandResult(name, False, False, "IO_ERROR", str(exc))
        try:
            return self._run(name, opcode, args, retries=retries, start_probe=opcode == OP_STATUS)
        finally:
            if not self.connected:
                self._forget_link()
                self._t.close()

    def check_start_outcome(self) -> CommandResult:
        """After the quiet window, explicitly ask for terminal state without taking a lease."""
        name = "check_start_outcome"
        if not self.start_uncertain:
            return self._reject(name, "no unresolved START")
        if self.start_recheck_wait_s > 0:
            return self._reject(name, "wait for the quiet window before checking device state")
        res = self._run_recovery(name, OP_STATUS, args_host_time(int(self._clock() * 1e6)), retries=0)
        if res.ok and res.reply.session_id == 0 and res.reply.state in (ST_STOPPED, ST_SAFE_OFF):
            self._start_probe_at = None
            self._log("link", f"START uncertainty resolved by device report: {res.reply.state_text}")
            return res
        # A valid owner STATUS can itself refresh the lease if the device is still active.
        if res.sent:
            self._start_probe_at = self._clock() + START_QUIET_S
        if res.ok:
            return CommandResult(name, False, res.sent, "START_UNRESOLVED",
                                 f"peer reported {res.reply.state_text}; ownership not acquired", res.reply)
        return res

    def stop(self) -> CommandResult:
        if not self.connected:
            return self._reject("stop_stream", "not connected")
        if not self.session_id or self.device_state not in OWNED_STATES:
            return self._reject("stop_stream", "no owned active session")
        res = self._run("stop_stream", OP_STOP)
        if res.ok:
            self._stop_requested = self.device_state == ST_STOPPING
        if res.ok and self.device_state == ST_STOPPED:
            self.last_end = "HOST_STOP"
        return res

    def safe_off(self) -> CommandResult:
        """Never blocked locally; extra retries. Works even if the state is unknown."""
        if self.start_uncertain:
            res = self._run_recovery("safe_off", OP_SAFE_OFF, retries=self.retries + 3)
            if res.ok and res.reply.state == ST_SAFE_OFF:
                self._start_probe_at = None
                self._log("link", "START uncertainty resolved by device report: SAFE_OFF")
            elif res.ok:
                return CommandResult("safe_off", False, res.sent, "START_UNRESOLVED",
                                     "reply did not report SAFE_OFF", res.reply)
        else:
            res = self._run("safe_off", OP_SAFE_OFF, retries=self.retries + 3)
        if res.ok:
            self.last_end = "SAFE_OFF"
            self._stop_requested = False
        return res

    def clear_fault(self) -> CommandResult:
        if not self.connected:
            return self._reject("clear_fault", "not connected")
        if self.device_state not in (ST_SAFE_OFF, ST_FAULTED):
            return self._reject("clear_fault", f"nothing to clear (state is {self.state_text})")
        return self._run("clear_fault", OP_CLEAR_FAULT)

    def set_fs(self, fs_hz: int) -> CommandResult:
        if not self.connected:
            return self._reject("set_fs", "not connected")
        allowed = self.settings.fs_allowed
        if not allowed:
            return self._reject("set_fs", "device did not report a valid INFO fs_allowed")
        if isinstance(fs_hz, bool) or not isinstance(fs_hz, int) or fs_hz not in allowed:
            return self._reject("set_fs", f"Fs must be one of {allowed} Hz")
        if self.device_state != ST_STOPPED:
            return self._reject("set_fs", f"only allowed while STOPPED (state is {self.state_text})")
        old = self.settings.fs_hz
        res = self._run("set_fs", OP_SET_FS, args_set_fs(fs_hz), note=f" {old} -> {fs_hz} Hz")
        if res.ok:
            self.settings.fs_hz = fs_hz
        return res

    def set_mode(self, mode: str) -> CommandResult:
        if not self.connected or self.device_state != ST_STOPPED:
            return self._reject("set_mode", "requires a connected STOPPED device")
        if mode not in ("ACQ", "ENGINEERING"):
            return self._reject("set_mode", "mode must be ACQ or ENGINEERING")
        res = self._run("set_mode", OP_SET_MODE, bytes([mode == "ENGINEERING"]))
        if res.ok:
            self.settings.mode = mode
        return res

    def set_led_current(self, channel: int, current_ma: int) -> CommandResult:
        if not self.connected:
            return self._reject("set_led_current", "not connected")
        ok_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
        if not ok_int(channel) or not 0 <= channel < LED_CHANNELS:
            return self._reject("set_led_current", f"channel must be 0..{LED_CHANNELS - 1}")
        if not ok_int(current_ma) or not 0 <= current_ma <= self.settings.current_max_ma:
            return self._reject("set_led_current", f"current must be 0..{self.settings.current_max_ma} mA")
        if self.device_state != ST_STOPPED:
            return self._reject("set_led_current",
                                f"only allowed while STOPPED (state is {self.state_text})")
        old = self.settings.current_ma[channel] if self.settings.current_ma else None
        res = self._run("set_led_current", OP_SET_LED_CURRENT, args_set_led_current(channel, current_ma),
                        note=f" ch{channel} {old} -> {current_ma} mA")
        if res.ok and self.settings.current_ma:
            self.settings.current_ma[channel] = current_ma
        return res

    def query_battery(self) -> CommandResult:
        """Read-only battery query (opcode 0x0033). Never changes session, state or lease.

        One attempt with a short timeout so it cannot hold up the owner heartbeat. A missing or
        unusable reply is counted as a battery timeout, not a control timeout."""
        name = "get_battery"
        if (not self.connected or self._busy or self.start_uncertain
                or self.device_state in (ST_STARTING, ST_STOPPING)):
            return CommandResult(name, False, False, "LOCAL_REJECT", "battery query not allowed now")
        now = self._clock()
        self.battery.attempted(now)
        self.battery_counters["queries"] += 1
        before = self.battery.status
        self._busy = True
        try:
            reply = self._request(OP_GET_BATTERY, b"", retries=0, timeout_s=BATTERY_TIMEOUT_S,
                                  count_timeout=False)
        except OSError as exc:
            self.counters["io_errors"] += 1
            self._forget_link()
            self._t.close()
            self._log("link", f"{name}: transport error ({exc}); reconnect required")
            return CommandResult(name, False, True, "IO_ERROR", str(exc))
        finally:
            self._busy = False
        if reply is None:
            if not self.connected:
                return CommandResult(name, False, True, "LINK_LOST", "device rebooted")
            self.battery_counters["timeouts"] += 1
            self.battery.on_no_reply(now)
            result = CommandResult(name, False, True, "TIMEOUT", "no usable reply")
        elif reply.status == STATUS_UNKNOWN_COMMAND:
            self.battery_counters["unsupported"] += 1
            self.battery.on_unsupported(now)
            result = CommandResult(name, False, True, reply.status_text,
                                   "firmware without battery query", reply)
        elif not reply.ok:
            self.battery_counters["errors"] += 1
            self.battery.on_status_error(now, reply.status_text)
            result = CommandResult(name, False, True, reply.status_text,
                                   f"device answered {reply.status_text}", reply)
        else:
            self.battery.on_reading(now, reply.battery, reply.simulated)
            result = CommandResult(name, True, True, "OK", "ok", reply)
        if self.battery.status != before and self.battery.status != "OK":
            self.battery_log.append(f"battery: {self.battery.status}"
                                    + (f" ({self.battery.detail})" if self.battery.detail else ""))
            del self.battery_log[:-50]
        return result

    def query_profile(self) -> CommandResult:
        """Read-only LED profile report (opcode 0x0034). Never changes the host's session or state.

        Lease: the firmware (patch 0004, nn2_app_comm.c) checks lease expiry before it answers, and a
        successful GET_PROFILE that carries the current owner's session id refreshes the owner activity
        anchor like any successful owner request; with session id 0 it does not. The host does not count
        the query as a heartbeat and leaves its own heartbeat schedule unchanged.

        Identity: the report is device-wide. The reply's session id is the device's current session (which
        may be another endpoint's) and is returned in `CommandResult.reply` for attribution by the caller;
        it is never adopted. One attempt with a short timeout so it cannot hold up the owner heartbeat. A
        missing reply is counted as a profile timeout, not a control timeout. A firmware without the command
        (UNKNOWN_COMMAND) or one that reports `v4profile=0` is "not available", not an error.
        `self.profile` keeps the last report."""
        name = "get_profile"
        if (not self.connected or self._busy or self.start_uncertain
                or self.device_state in (ST_STARTING, ST_STOPPING)):
            return CommandResult(name, False, False, "LOCAL_REJECT", "profile query not allowed now")
        owned = bool(self.session_id and self.device_state in OWNED_STATES)
        if owned and self._clock() - self._last_hb > HEARTBEAT_PERIOD_S - PROFILE_TIMEOUT_S:
            return CommandResult(name, False, False, "LOCAL_REJECT", "owner heartbeat is due first")
        self.profile_counters["queries"] += 1
        self._busy = True
        try:
            reply = self._request(OP_GET_PROFILE, b"", retries=0, timeout_s=PROFILE_TIMEOUT_S,
                                  count_timeout=False)
        except OSError as exc:
            self.counters["io_errors"] += 1
            self._forget_link()
            self._t.close()
            self._log("link", f"{name}: transport error ({exc}); reconnect required")
            return CommandResult(name, False, True, "IO_ERROR", str(exc))
        finally:
            self._busy = False
        if reply is None:
            if not self.connected:
                return CommandResult(name, False, True, "LINK_LOST", "device rebooted")
            self.profile_counters["timeouts"] += 1
            return CommandResult(name, False, True, "TIMEOUT", "no usable reply")
        if reply.status == STATUS_UNKNOWN_COMMAND:
            self.profile_counters["unsupported"] += 1
            self.profile = ProfileReport(False, "unsupported")
            return CommandResult(name, False, True, reply.status_text, "firmware without get-profile", reply)
        if not reply.ok:
            self.profile_counters["errors"] += 1
            return CommandResult(name, False, True, reply.status_text,
                                 f"device answered {reply.status_text}", reply)
        self.profile = reply.profile
        return CommandResult(name, True, True, "OK", "ok", reply)

    def _battery_tick(self, now: float) -> None:
        if (not self.connected or self.start_uncertain or not self.battery_enabled
                or self.device_state in (ST_STARTING, ST_STOPPING)):
            return
        owned = bool(self.session_id and self.device_state in OWNED_STATES)
        # The owner heartbeat goes first. Do not start a query that could run into it.
        if owned and self._clock() - self._last_hb > HEARTBEAT_PERIOD_S - BATTERY_TIMEOUT_S:
            return
        if self.battery.due(now, streaming=self.device_state not in (ST_STOPPED, None)):
            self.query_battery()

    def pump(self) -> None:
        """Hand pending FRAME datagrams to the sink. Replies seen here are late/duplicates."""
        if self._busy or not self.connected:
            return
        try:
            for _ in range(256):  # bounded work per call
                batch, rx_us = self._receive(0.0)
                if not batch:
                    return
                for raw in batch:
                    if not self._route_frame(raw, rx_us):
                        try:
                            reply = decode_reply(raw)
                        except WireError:
                            self.counters["malformed"] += 1
                            continue
                        key = "duplicate_replies" if reply.req_id in self._done else "unmatched"
                        self.counters[key] += 1
        except OSError as exc:
            self.counters["io_errors"] += 1
            self._forget_link()
            self._t.close()
            self._log("link", f"receive: transport error ({exc}); reconnect required")

    # --- periodic work ---------------------------------------------------------------
    def tick(self) -> None:
        """Call a few times per second. Sends a heartbeat while streaming."""
        if not self.connected:
            return
        self.pump()
        if not self.connected or self.start_uncertain:
            return
        now = self._clock()
        if self.session_id and self.device_state in OWNED_STATES:
            if now - self._last_hb >= HEARTBEAT_PERIOD_S:
                self._last_hb = now
                res = self._run("heartbeat", OP_HEARTBEAT, args_host_time(int(now * 1e6)),
                                quiet_ok=True)
                if res.reply and self.device_state not in OWNED_STATES:
                    self.session_id = 0
                    self.last_end = "HOST_STOP" if self._stop_requested and \
                        self.device_state == ST_STOPPED else "LINK_LOSS (device stopped the stream)"
                    self._stop_requested = False
                    self._log("link", "device is no longer streaming: " + self.last_end)
        if not self.connected:
            return
        if self.device_state != ST_STREAMING or not self.session_id:
            period = TRANSITION_POLL_S if self.device_state in (ST_STARTING, ST_STOPPING) \
                else STATUS_PERIOD_S
            if now - self._last_status >= period:
                self._last_status = now
                res = self.refresh_status(quiet=True)
                if res.reply and self.device_state not in OWNED_STATES:
                    self.session_id = 0
        self._battery_tick(now)
