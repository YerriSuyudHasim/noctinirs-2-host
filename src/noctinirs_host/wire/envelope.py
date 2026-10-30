"""NN2 envelope parser (device -> host direction).

Source of truth: firmware ADR-0004 section 3.3 (status Proposed). Header 28 B,
little-endian, CRC-32/ISO-HDLC (same as zlib.crc32) over header + payload.
Not verified against the C implementation.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .errors import WireError

MAGIC = 0x32534E4E  # bytes 4E 4E 53 32
ENVELOPE_VERSION = 1
PROTOCOL_VERSION = 1
HEADER_SIZE = 28
CRC_SIZE = 4
MAX_PAYLOAD = 1440
MIN_DGRAM = HEADER_SIZE + CRC_SIZE
MAX_DGRAM = HEADER_SIZE + MAX_PAYLOAD + CRC_SIZE

MSG_REQUEST = 0
MSG_REPLY = 1
MSG_FRAME = 2
MSG_SESSION_REPORT = 3
MSG_EVENT = 4

FLAG_SIMULATED = 0x0001
FLAG_TS_ESTIMATED = 0x0002
FLAGS_RESERVED_MASK = 0xFFFC

_HDR = struct.Struct("<IBBHHHIIQ")
assert _HDR.size == HEADER_SIZE


@dataclass(frozen=True)
class Envelope:
    msg_type: int
    protocol_version: int
    flags: int
    session_id: int
    msg_sequence: int  # counts all messages; NOT a loss counter
    timestamp_us: int
    payload: bytes

    @property
    def simulated(self) -> bool:
        return bool(self.flags & FLAG_SIMULATED)

    @property
    def ts_estimated(self) -> bool:
        return bool(self.flags & FLAG_TS_ESTIMATED)


def parse_envelope(datagram: bytes) -> Envelope:
    """Parse one device->host datagram. Raises WireError (fail-closed)."""
    total = len(datagram)
    if total < MIN_DGRAM or total > MAX_DGRAM:
        raise WireError("size", f"datagram size {total} outside {MIN_DGRAM}..{MAX_DGRAM}")
    (magic, env_ver, msg_type, proto_ver, length, flags,
     session_id, msg_seq, ts_us) = _HDR.unpack_from(datagram, 0)
    if magic != MAGIC:
        raise WireError("magic", "bad magic")
    if env_ver != ENVELOPE_VERSION:
        raise WireError("envelope_version", f"envelope_version {env_ver} not supported")
    if length > MAX_PAYLOAD or length != total - MIN_DGRAM:
        raise WireError("length", f"length field {length} does not match datagram size {total}")
    end = HEADER_SIZE + length
    (crc_rx,) = struct.unpack_from("<I", datagram, end)
    if zlib.crc32(datagram[:end]) & 0xFFFFFFFF != crc_rx:
        raise WireError("crc", "CRC-32 mismatch")
    if flags & FLAGS_RESERVED_MASK:
        raise WireError("reserved_flags", f"reserved flag bits set (flags=0x{flags:04X})")
    if msg_type > MSG_EVENT:
        raise WireError("msg_type", f"reserved msg_type {msg_type}")
    if proto_ver != PROTOCOL_VERSION:
        raise WireError("protocol_version", f"protocol_version {proto_ver} not supported")
    return Envelope(msg_type, proto_ver, flags, session_id, msg_seq, ts_us,
                    bytes(datagram[HEADER_SIZE:end]))
