"""REQUEST encode and REPLY decode for the NoctiNIRS 2 control protocol.

Pinned source (do not extend on the host side; raise changes in `noctinirs-2`):
  firmware repo noctinirs-2 @ bd880f5890ea1a72d54e8ab9cc2ed55f002d1382
  docs/adr/0004-host-protocol.md (Proposed, rev 4 + Batch 3 amendment)
  docs/adr/0004-local-implementation.md (concrete reply bodies; 2026-10-27)
  firmware/v4-idf61/main/v4_nn2_control.inc (INFO fs_allowed capability)
  components/nn2_app/src/nn2_app_comm.c (session and asynchronous states)
  tools/nn2ctl.py (reference client)
Not verified against a running device. All integers little endian.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

from ..wire.envelope import (
    ENVELOPE_VERSION, FLAG_SIMULATED, HEADER_SIZE, MAGIC, MAX_PAYLOAD, MSG_FRAME, MSG_REPLY,
    MSG_REQUEST,
    PROTOCOL_VERSION, parse_envelope,
)
from ..wire.errors import WireError

FIRMWARE_PIN = "bd880f5890ea1a72d54e8ab9cc2ed55f002d1382"
# Battery query (opcode 0x0033) is pinned separately. FIRMWARE_PIN above also feeds the RAW
# recording metadata and must not change for a battery feature.
BATTERY_FIRMWARE_PIN = "519a96a26ede6517595d4f8a87cd64ab6b7a6fa5"  # branch feat/local-v4-battery-refresh
BATTERY_CONTRACT = ("noctinirs-2 docs/adr/0004-host-protocol.md section 14 'V4 Batch 5A battery "
                    "query and idle refresh', registry row 0x0033 in section 3.5; wire=ADR0004/1; "
                    "reference client tools/nn2ctl.py get-battery @ 519a96a")

OP_HEARTBEAT = 0x0001
OP_INFO = 0x0002
OP_STATUS = 0x0003
OP_GET_CONFIG = 0x0004
OP_GET_TIME = 0x0005
OP_START = 0x0010
OP_STOP = 0x0011
OP_SAFE_OFF = 0x0020
OP_CLEAR_FAULT = 0x0021
OP_SET_FS = 0x0100
OP_SET_LED_CURRENT = 0x0101
OP_SET_MODE = 0x0102  # one byte: 0 ACQ, 1 ENGINEERING; STOPPED only
OP_GET_BATTERY = 0x0033  # no arguments; read-only; any session id; never refreshes a lease

OPCODE_NAMES = {
    OP_HEARTBEAT: "heartbeat", OP_INFO: "info", OP_STATUS: "status",
    OP_GET_CONFIG: "get_config", OP_GET_TIME: "get_time", OP_START: "start_stream",
    OP_STOP: "stop_stream", OP_SAFE_OFF: "safe_off", OP_CLEAR_FAULT: "clear_fault",
    OP_SET_FS: "set_fs", OP_SET_LED_CURRENT: "set_led_current", OP_SET_MODE: "set_mode",
    OP_GET_BATTERY: "get_battery",
}
TIMED_OPS = (OP_HEARTBEAT, OP_STATUS, OP_GET_TIME)  # carry an opaque u64 host timestamp
MUTATING_OPS = (OP_START, OP_STOP, OP_CLEAR_FAULT, OP_SET_FS, OP_SET_LED_CURRENT, OP_SET_MODE)

STATUS_NAMES = ("OK", "INVALID_STATE", "INVALID_ARG", "UNKNOWN_COMMAND", "HW_FAULT", "BUSY",
                "BAD_FRAME", "PRECONDITION_FAILED", "RESERVED_FLAGS")
STATUS_OK, STATUS_INVALID_STATE, STATUS_INVALID_ARG = 0, 1, 2
STATUS_UNKNOWN_COMMAND, STATUS_BAD_FRAME, STATUS_PRECONDITION_FAILED = 3, 6, 7
STATUS_RESERVED_FLAGS = 8

STATE_NAMES = ("BOOT", "STOPPED", "STARTING", "STREAMING", "STOPPING", "RECOVERING",
               "FAULTED", "SAFE_OFF")
ST_BOOT, ST_STOPPED, ST_STARTING, ST_STREAMING = 0, 1, 2, 3
ST_STOPPING, ST_RECOVERING, ST_FAULTED, ST_SAFE_OFF = 4, 5, 6, 7

LED_CHANNELS = 20
LED_MAX_MA = 50  # ADR-0004 upper bound; the device may report tighter limits

_HDR = struct.Struct("<IBBHHHIIQ")
_PREFIX = struct.Struct("<BHBQQ")  # state, fault, simulated, boot_id, t_rx_us (20 bytes)


def state_name(state: int) -> str:
    return STATE_NAMES[state] if 0 <= state < len(STATE_NAMES) else f"UNKNOWN({state})"


def status_name(status: int) -> str:
    return STATUS_NAMES[status] if 0 <= status < len(STATUS_NAMES) else f"UNKNOWN({status})"


def _envelope(msg_type: int, payload: bytes, flags: int, session_id: int, sequence: int,
              timestamp_us: int) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    body = _HDR.pack(MAGIC, ENVELOPE_VERSION, msg_type, PROTOCOL_VERSION, len(payload), flags,
                     session_id & 0xFFFFFFFF, sequence & 0xFFFFFFFF, timestamp_us) + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_request(req_id: int, opcode: int, args: bytes = b"", *, session_id: int = 0,
                   sequence: int = 0, flags: int = 0, timestamp_us: int = 0) -> bytes:
    if not 0 <= req_id <= 0xFFFFFFFF or not 1 <= opcode <= 0xFFFF:
        raise ValueError("request id / opcode range")
    return _envelope(MSG_REQUEST, struct.pack("<IH", req_id, opcode) + args, flags,
                     session_id, sequence, timestamp_us)


def args_set_fs(fs_hz: int) -> bytes:
    return struct.pack("<B", fs_hz)


def args_set_led_current(channel: int, current_ma: int) -> bytes:
    return struct.pack("<BH", channel, current_ma)


def args_host_time(t_host_us: int) -> bytes:
    return struct.pack("<Q", t_host_us)


@dataclass(frozen=True)
class RawRequest:
    """A host->device datagram after framing/CRC/length checks (simulator side)."""
    flags: int
    protocol_version: int
    session_id: int
    sequence: int
    req_id: int
    opcode: int
    args: bytes


def decode_request(datagram: bytes) -> RawRequest:
    """Check magic, sizes, CRC and REQUEST framing. Reserved flags and protocol_version
    are NOT checked here: a CRC-valid, parsable request with either problem gets a
    reply (RESERVED_FLAGS / BAD_FRAME), so the caller decides."""
    n = len(datagram)
    if n < HEADER_SIZE + 4 or n > HEADER_SIZE + MAX_PAYLOAD + 4:
        raise WireError("size", f"datagram size {n}")
    magic, env_ver, msg_type, proto, length, flags, session, seq, _ts = _HDR.unpack_from(datagram)
    if magic != MAGIC or env_ver != ENVELOPE_VERSION or msg_type != MSG_REQUEST:
        raise WireError("framing", "not a v1 REQUEST")
    if length != n - HEADER_SIZE - 4 or length < 6:
        raise WireError("length", "payload length")
    end = HEADER_SIZE + length
    if zlib.crc32(datagram[:end]) & 0xFFFFFFFF != struct.unpack_from("<I", datagram, end)[0]:
        raise WireError("crc", "CRC-32 mismatch")
    req_id, opcode = struct.unpack_from("<IH", datagram, HEADER_SIZE)
    return RawRequest(flags, proto, session, seq, req_id, opcode, bytes(datagram[HEADER_SIZE + 6:end]))


def encode_reply(req_id: int, opcode: int, status: int, *, state: int, fault: int, simulated: bool,
                 boot_id: int, t_rx_us: int, extension: bytes = b"", session_id: int = 0,
                 sequence: int = 0, timestamp_us: int = 0) -> bytes:
    body = struct.pack("<IHH", req_id, opcode, status)
    body += _PREFIX.pack(state, fault, 1 if simulated else 0, boot_id, t_rx_us)
    body += extension if status == STATUS_OK else b""
    return _envelope(MSG_REPLY, body, 1 if simulated else 0, session_id, sequence, timestamp_us)


def encode_frame(payload: bytes, *, simulated: bool, session_id: int, sequence: int,
                 timestamp_us: int, flags: int | None = None) -> bytes:
    """Device->host FRAME datagram (simulator and replay side). The payload is built
    elsewhere. `flags` (when given) replaces the flags derived from `simulated`, so a
    replay can reproduce the original envelope flags exactly."""
    if flags is None:
        flags = FLAG_SIMULATED if simulated else 0
    return _envelope(MSG_FRAME, payload, flags, session_id, sequence, timestamp_us)


_BATTERY_TEXT = re.compile(
    rb"battery_valid=([01]) battery_uv=(\d{1,20}) battery_mperm=(\d{1,20})"
    rb" battery_age_ms=(-?\d{1,20}) battery_error=(-?\d{1,10})")


@dataclass(frozen=True)
class BatteryReading:
    """get_battery extension (ASCII key=value text, ADR-0004 section 14).

    mperm is thousandths of a percent and is not clamped by the firmware. age_ms is the age of
    the last successful sample on the device's monotonic clock (-1: none yet). error is an
    esp_err_t decimal (0 ok, 259 no sample yet, 262 not supported by the binding)."""
    valid: bool
    uv: int
    mperm: int
    age_ms: int
    error: int

    @property
    def percent(self) -> float:
        return self.mperm / 1000.0

    @property
    def volts(self) -> float:
        return self.uv / 1_000_000.0


def parse_battery(ext: bytes) -> BatteryReading:
    m = _BATTERY_TEXT.fullmatch(ext)
    if not m:
        raise WireError("reply_ext", "battery reply fields")
    valid, uv, mperm, age_ms, error = (int(g) for g in m.groups())
    if valid and age_ms < 0:
        raise WireError("reply_ext", "battery valid with negative age")
    if not valid and (uv != 0 or mperm != 0 or age_ms != -1 or error == 0):
        raise WireError("reply_ext", "battery invalid reading carries values")
    return BatteryReading(bool(valid), uv, mperm, age_ms, error)


def encode_battery_ext(*, valid: bool, uv: int, mperm: int, age_ms: int, error: int) -> bytes:
    """Text extension for simulators and tests (same field order as the firmware)."""
    return (f"battery_valid={int(valid)} battery_uv={uv} battery_mperm={mperm} "
            f"battery_age_ms={age_ms} battery_error={error}").encode("ascii")


@dataclass(frozen=True)
class Reply:
    req_id: int
    opcode: int
    status: int
    state: int
    fault: int
    simulated: bool
    boot_id: int
    t_rx_us: int
    session_id: int
    msg_sequence: int
    device_us: int
    extension: bytes = b""
    info: dict[str, str] = field(default_factory=dict)
    fs_hz: int | None = None
    slots: int | None = None
    current_ma: tuple[int, ...] = ()
    counters: dict[str, int] = field(default_factory=dict)
    t_host_us: int | None = None
    t_tx_us: int | None = None
    battery: BatteryReading | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def status_text(self) -> str:
        return status_name(self.status)

    @property
    def state_text(self) -> str:
        return state_name(self.state)


def parse_info(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in text.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def decode_reply(datagram: bytes) -> Reply:
    """Parse and validate one device->host REPLY. Raises WireError (fail-closed)."""
    env = parse_envelope(datagram)
    if env.msg_type != MSG_REPLY:
        raise WireError("msg_type", "not a REPLY")
    p = env.payload
    if len(p) < 8 + _PREFIX.size:
        raise WireError("reply_length", "reply shorter than the common prefix")
    req_id, opcode, status = struct.unpack_from("<IHH", p)
    state, fault, simulated, boot_id, t_rx = _PREFIX.unpack_from(p, 8)
    if state >= len(STATE_NAMES) or simulated not in (0, 1) or status >= len(STATUS_NAMES):
        raise WireError("reply_fields", "state/simulated/status out of range")
    if bool(env.flags & 1) != bool(simulated):
        raise WireError("provenance", "SIMULATED flag differs from body")
    ext = p[8 + _PREFIX.size:]
    kw: dict = {}
    if status != STATUS_OK:
        if ext:
            raise WireError("reply_ext", "unexpected extension on error reply")
    elif opcode == OP_INFO:
        try:
            text = ext.decode("ascii")
        except UnicodeDecodeError as exc:
            raise WireError("reply_ext", "INFO is not ASCII") from exc
        kw["info"] = parse_info(text)
    elif opcode == OP_GET_CONFIG:
        if len(ext) != 42 or ext[1] != LED_CHANNELS:
            raise WireError("reply_ext", "get_config length")
        kw.update(fs_hz=ext[0], slots=ext[1], current_ma=struct.unpack_from("<20H", ext, 2))
    elif opcode in TIMED_OPS:
        want = 45 if opcode == OP_STATUS else 24
        if len(ext) != want:
            raise WireError("reply_ext", "time/status length")
        t_host, t_rx2, t_tx = struct.unpack_from("<QQQ", ext)
        if t_tx < t_rx2:
            raise WireError("reply_ext", "device timestamps run backwards")
        kw.update(t_host_us=t_host, t_tx_us=t_tx)
        if opcode == OP_STATUS:
            names = ("requests", "duplicates", "grace_expirations", "malformed",
                     "reserved_flags_rejects")
            vals = struct.unpack_from("<5I", ext, 24)
            grace = ext[44]
            if grace > 1:
                raise WireError("reply_ext", "grace field")
            kw["counters"] = {**dict(zip(names, vals)), "grace_active": grace}
    elif opcode == OP_GET_BATTERY:
        kw["battery"] = parse_battery(bytes(ext))
    elif ext:
        raise WireError("reply_ext", "unexpected command data")
    return Reply(req_id, opcode, status, state, fault, bool(simulated), boot_id, t_rx,
                 env.session_id, env.msg_sequence, env.timestamp_us, bytes(ext), **kw)
