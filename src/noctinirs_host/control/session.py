"""Session controller: the single owner of what the host believes about the device.

Rules (issue #24, firmware ADR-0004):
* Settings are validated locally first; an invalid value sends NO packet.
* Every user command (including local rejections and failures), every state change and
  every link event is appended to `events`. Successful periodic traffic (heartbeats and
  status polls) is counted but kept out of the log. Nothing is changed silently.
* `safe_off` is never blocked by local state and is retried harder than other commands.
* The controller never sends commands on its own. Its only automatic traffic is read-only
  or keep-alive: heartbeats while streaming and a `status` poll every 2 s otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..wire.errors import WireError
from .protocol import (
    LED_CHANNELS, LED_MAX_MA, OP_CLEAR_FAULT, OP_GET_CONFIG, OP_GET_TIME, OP_HEARTBEAT, OP_INFO, OP_SAFE_OFF,
    OP_SET_FS, OP_SET_LED_CURRENT, OP_START, OP_STATUS, OP_STOP, OPCODE_NAMES, ST_STOPPED,
    ST_FAULTED, ST_SAFE_OFF, ST_STREAMING, TIMED_OPS, Reply, args_host_time, args_set_fs, args_set_led_current,
    decode_reply, encode_request, state_name,
)

HEARTBEAT_PERIOD_S = 0.5  # local implementation record
STATUS_PERIOD_S = 2.0  # host-chosen: keeps the displayed state fresh while not streaming


class Transport(Protocol):
    def send(self, datagram: bytes) -> None: ...
    def receive(self, timeout_s: float) -> list[bytes]: ...


class LoopbackTransport:
    """Connects a controller to an in-process DeviceSimulator. No sockets."""

    def __init__(self, simulator) -> None:
        self.simulator = simulator
        self._queue: list[bytes] = []
        self.drop_next_replies = 0  # test hook: lose replies
        self.duplicate_next_reply = False  # test hook: deliver a reply twice

    def send(self, datagram: bytes) -> None:
        for reply in self.simulator.handle(datagram):
            if self.drop_next_replies:
                self.drop_next_replies -= 1
                continue
            self._queue.append(reply)
            if self.duplicate_next_reply:
                self.duplicate_next_reply = False
                self._queue.append(reply)

    def receive(self, timeout_s: float) -> list[bytes]:
        return [self._queue.pop(0)] if self._queue else []


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
    sent: bool  # False = rejected locally, nothing went on the wire
    status: str  # device status name, or LOCAL_REJECT / TIMEOUT
    message: str
    reply: Reply | None = None


@dataclass
class Settings:
    fs_hz: int | None = None
    current_ma: list[int] = field(default_factory=list)
    fs_min: int | None = None
    fs_max: int | None = None


class SessionController:
    def __init__(self, transport: Transport, clock: Callable[[], float] = time.monotonic, *,
                 retries: int = 2, timeout_s: float = 0.5) -> None:
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
        self.events: list[Event] = []
        self.listeners: list[Callable[[Event], None]] = []
        self.counters = dict(sent=0, retries=0, timeouts=0, unmatched=0, duplicate_replies=0,
                             malformed=0, local_rejects=0)
        self._next_id = 1
        self._seq = 0
        self._last_hb = 0.0
        self._last_status = 0.0
        self._done: set[int] = set()
        self.last_end: str | None = None
        self._rebooted = False

    # --- bookkeeping ----------------------------------------------------
    def _log(self, kind: str, text: str) -> None:
        ev = Event(self._clock(), kind, text, bool(self.simulated))
        self.events.append(ev)
        for fn in list(self.listeners):
            fn(ev)

    def _set_state(self, reply: Reply) -> None:
        if self.device_state is not None and reply.state != self.device_state:
            self._log("state", f"device state {state_name(self.device_state)} -> {reply.state_text}")
        self.device_state, self.fault_code = reply.state, reply.fault
        self.simulated = reply.simulated

    @property
    def state_text(self) -> str:
        return "NOT CONNECTED" if self.device_state is None else state_name(self.device_state)

    # --- one transaction ----------------------------------------------------
    def _request(self, opcode: int, args: bytes = b"", *, extra_retries: int = 0) -> Reply | None:
        self._rebooted = False
        rid = self._next_id
        self._next_id = (rid + 1) & 0xFFFFFFFF or 1
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        wire = encode_request(rid, opcode, args, session_id=self.session_id, sequence=self._seq)
        for attempt in range(self.retries + extra_retries + 1):
            self._t.send(wire)
            self.counters["sent"] += 1
            if attempt:
                self.counters["retries"] += 1
            for _ in range(64):  # bounded work under unrelated traffic
                batch = self._t.receive(self.timeout_s)
                if not batch:
                    break
                for raw in batch:
                    reply = self._accept(raw, rid, opcode, args)
                    if reply is not None:
                        return reply
                    if self._rebooted:
                        return None
        self.counters["timeouts"] += 1
        return None

    def _accept(self, raw: bytes, rid: int, opcode: int, args: bytes) -> Reply | None:
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
        if self.boot_id is not None and reply.boot_id != self.boot_id:
            self._log("link", "device rebooted (boot id changed): reconnect required")
            self.connected = False
            self.device_state, self.session_id = None, 0
            self.boot_id = None
            self._rebooted = True
            return None
        self.boot_id = reply.boot_id
        self._done.add(rid)
        if len(self._done) > 64:
            self._done.discard(min(self._done))
        return reply

    def _run(self, name: str, opcode: int, args: bytes = b"", *, extra_retries: int = 0,
             note: str = "", quiet_ok: bool = False) -> CommandResult:
        reply = self._request(opcode, args, extra_retries=extra_retries)
        if reply is None:
            self._log("command", f"{name}{note}: NO REPLY (timeout)")
            return CommandResult(name, False, True, "TIMEOUT", "no reply from device")
        self._set_state(reply)
        if opcode in (OP_START, OP_STOP, OP_SAFE_OFF, OP_CLEAR_FAULT) and reply.ok:
            self.session_id = reply.session_id if opcode == OP_START else 0
        if reply.status == 1 and opcode not in (OP_START, OP_SAFE_OFF):  # INVALID_STATE
            if opcode in (OP_HEARTBEAT, OP_STOP):
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
        res = self._run("info", OP_INFO)
        if not res.ok:
            return res
        self.info = res.reply.info
        for key, attr in (("fs_min", "fs_min"), ("fs_max", "fs_max")):
            try:
                setattr(self.settings, attr, int(self.info[key]))
            except (KeyError, ValueError):
                setattr(self.settings, attr, None)
        cfg = self._run("get_config", OP_GET_CONFIG)
        if not cfg.ok:
            return cfg
        self.settings.fs_hz = cfg.reply.fs_hz
        self.settings.current_ma = list(cfg.reply.current_ma)
        self.connected = True
        self._last_hb = self._last_status = self._clock()
        self._log("link", "connected" + (" to a SIMULATED device" if self.simulated else ""))
        return CommandResult("connect", True, True, "OK", "connected", cfg.reply)

    def disconnect(self) -> None:
        if self.connected:
            self._log("link", "disconnected by user (no command sent; a streaming device "
                              "stops itself after the grace period)")
        self.connected = False
        self.device_state, self.session_id, self.boot_id = None, 0, None

    def refresh_status(self, *, quiet: bool = False) -> CommandResult:
        return self._run("status", OP_STATUS, args_host_time(int(self._clock() * 1e6)),
                         quiet_ok=quiet)

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
        return res

    def stop(self) -> CommandResult:
        if not self.connected:
            return self._reject("stop_stream", "not connected")
        if self.device_state != ST_STREAMING:
            return self._reject("stop_stream", f"state is {self.state_text}, not STREAMING")
        res = self._run("stop_stream", OP_STOP)
        if res.ok:
            self.last_end = "HOST_STOP"
        return res

    def safe_off(self) -> CommandResult:
        """Never blocked locally; extra retries. Works even if the state is unknown."""
        res = self._run("safe_off", OP_SAFE_OFF, extra_retries=3)
        if res.ok:
            self.last_end = "SAFE_OFF"
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
        lo, hi = self.settings.fs_min, self.settings.fs_max
        if lo is None or hi is None:
            return self._reject("set_fs", "device did not report its Fs limits (INFO fs_min/fs_max)")
        if isinstance(fs_hz, bool) or not isinstance(fs_hz, int) or not lo <= fs_hz <= hi:
            return self._reject("set_fs", f"Fs must be an integer {lo}..{hi} Hz")
        if self.device_state != ST_STOPPED:
            return self._reject("set_fs", f"only allowed while STOPPED (state is {self.state_text})")
        old = self.settings.fs_hz
        res = self._run("set_fs", OP_SET_FS, args_set_fs(fs_hz), note=f" {old} -> {fs_hz} Hz")
        if res.ok:
            self.settings.fs_hz = fs_hz
        return res

    def set_led_current(self, channel: int, current_ma: int) -> CommandResult:
        if not self.connected:
            return self._reject("set_led_current", "not connected")
        ok_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
        if not ok_int(channel) or not 0 <= channel < LED_CHANNELS:
            return self._reject("set_led_current", f"channel must be 0..{LED_CHANNELS - 1}")
        if not ok_int(current_ma) or not 0 <= current_ma <= LED_MAX_MA:
            return self._reject("set_led_current", f"current must be 0..{LED_MAX_MA} mA")
        if self.device_state != ST_STOPPED:
            return self._reject("set_led_current",
                                f"only allowed while STOPPED (state is {self.state_text})")
        old = self.settings.current_ma[channel] if self.settings.current_ma else None
        res = self._run("set_led_current", OP_SET_LED_CURRENT, args_set_led_current(channel, current_ma),
                        note=f" ch{channel} {old} -> {current_ma} mA")
        if res.ok and self.settings.current_ma:
            self.settings.current_ma[channel] = current_ma
        return res

    # --- periodic work ---------------------------------------------------------------
    def tick(self) -> None:
        """Call a few times per second. Sends a heartbeat while streaming."""
        if not self.connected:
            return
        now = self._clock()
        if self.device_state != ST_STREAMING:
            if now - self._last_status >= STATUS_PERIOD_S:
                self._last_status = now
                self.refresh_status(quiet=True)
            return
        if now - self._last_hb < HEARTBEAT_PERIOD_S:
            return
        self._last_hb = now
        res = self._run("heartbeat", OP_HEARTBEAT, args_host_time(int(now * 1e6)),
                        quiet_ok=True)
        if res.sent and not res.ok and self.device_state != ST_STREAMING:
            self.last_end = "LINK_LOSS (device stopped the stream)"
            self._log("link", "device is no longer streaming: " + self.last_end)
