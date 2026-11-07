"""RAW archive and streaming replay. The binary datagram, not CSV, is authoritative."""

import csv
import hashlib
import io
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..wire.envelope import MAX_DGRAM, MIN_DGRAM, MSG_FRAME, Envelope, parse_envelope
from ..wire.errors import WireError
from ..wire.optical import RAW_MAX_SAMPLES, RAW_PREFIX_SIZE, RAW_SAMPLE_SIZE, RawPacket, parse_raw
from .continuity import RawContinuity
from .meta import file_facts

RAW_SCHEMA = "noctinirs.raw/1"
MAGIC = b"NN2RAW\x01\x00"
_RECORD = struct.Struct("<HBQQ")  # wire length, context flags, host receive us, observed boot ID
_CRC = struct.Struct("<I")
_MIN_WIRE = MIN_DGRAM + RAW_PREFIX_SIZE + RAW_SAMPLE_SIZE
_MAX_WIRE = MIN_DGRAM + RAW_PREFIX_SIZE + RAW_SAMPLE_SIZE * RAW_MAX_SAMPLES
assert _MAX_WIRE <= MAX_DGRAM
_MAX_METADATA = 8 * 1024 * 1024

RAW_COLUMNS = ("packet", "host_rx_us", "observed_boot_id", "session_id", "msg_sequence",
               "env_flags", "device_first_sample_us", "ts_estimated", "slot_plan_epoch",
               "geometry_map_id", "gap_before", "sample_sequence", "ain0", "ain1", "ain2",
               "ain3", "phase_class", "slot_index")


@dataclass(frozen=True)
class RecordedRaw:
    host_rx_us: int
    observed_boot_id: int | None
    wire: bytes
    envelope: Envelope
    raw: RawPacket

    def datagram(self) -> bytes:
        return self.wire


def decode_raw(wire: bytes, host_rx_us: int, boot_id: int | None) -> RecordedRaw:
    env = parse_envelope(wire)
    if env.msg_type != MSG_FRAME:
        raise WireError("msg_type", "RAW capture requires a FRAME")
    return RecordedRaw(host_rx_us, boot_id, wire, env, parse_raw(env.payload))


def write_exact(fh, data: str | bytes) -> None:
    if fh.write(data) != len(data):
        raise OSError("short RAW archive or sample CSV write")
    fh.flush()


class RawArchive:
    """Disk operations only. Recorder owns the queue, worker and sidecar."""

    def __init__(self, folder: Path, stem: str) -> None:
        self.path = folder / f"{stem}.raw.bin"
        self.csv_path = folder / f"{stem}.raw.csv"
        self.wire = self.csv = None
        self.packets = self.rows = 0
        self.continuity = RawContinuity()

    def open(self, opener) -> None:
        self.wire = opener(self.path, "xb")
        write_exact(self.wire, MAGIC)
        self.csv = opener(self.csv_path, "x", newline="", encoding="utf-8")
        write_exact(self.csv, ",".join(RAW_COLUMNS) + "\n")

    def write_packet(self, packet: RecordedRaw) -> None:
        env, raw, boot = packet.envelope, packet.raw, packet.observed_boot_id
        record = _RECORD.pack(len(packet.wire), int(boot is not None), packet.host_rx_us,
                              0 if boot is None else boot) + packet.wire
        write_exact(self.wire, record + _CRC.pack(zlib.crc32(record)))
        self.packets += 1
        self.continuity.observe(env, raw, packet.host_rx_us, boot)

    def write_rows(self, packet: RecordedRaw) -> None:
        env, raw, boot = packet.envelope, packet.raw, packet.observed_boot_id
        buf = io.StringIO()
        prefix = (self.packets, packet.host_rx_us, boot, env.session_id, env.msg_sequence,
                  env.flags, env.timestamp_us, int(env.ts_estimated), raw.slot_plan_epoch,
                  raw.geometry_map_id, int(raw.gap_before))
        csv.writer(buf, lineterminator="\n").writerows(
            (*prefix, sample.sample_sequence, *sample.ain, sample.phase_class, sample.slot_index)
            for sample in raw.samples)
        write_exact(self.csv, buf.getvalue())
        self.rows += raw.sample_count

    def files(self):
        return (fh for fh in (self.wire, self.csv) if fh is not None and not fh.closed)

    def facts(self) -> dict:
        return {**file_facts(self.path, "archive"), **file_facts(self.csv_path, "csv")}


class RawReplay:
    """Single-pass replay of complete records. Consume to EOF for final diagnostics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.name.endswith(".raw.bin"):
            raise ValueError("RAW replay requires a .raw.bin archive")
        self.meta_path = self.path.with_name(self.path.name.removesuffix(".raw.bin") + ".json")
        self.meta: dict | None = None
        self.meta_problem: str | None = None
        self.archive_problem: str | None = None
        self._continuity = RawContinuity()
        self._started = False
        self._load_metadata()

    @property
    def diagnostics(self) -> dict:
        return self._continuity.snapshot()

    def _load_metadata(self) -> None:
        try:
            with self.meta_path.open("rb") as fh:
                data = fh.read(_MAX_METADATA + 1)
            if len(data) > _MAX_METADATA:
                raise ValueError("sidecar exceeds 8 MiB replay limit")
            meta = json.loads(data)
            raw = meta.get("raw") if isinstance(meta, dict) else None
            if not isinstance(raw, dict) or raw.get("schema") != RAW_SCHEMA:
                raise ValueError("missing or unsupported RAW metadata schema")
            self.meta = meta
            if meta.get("complete") is not True:
                self.meta_problem = "sidecar indicates an incomplete recording"
            elif raw.get("archive_file") != self.path.name:
                self.meta_problem = "archive name differs from clean-close sidecar"
        except (OSError, ValueError, RecursionError) as exc:
            self.meta_problem = f"RAW sidecar unavailable or invalid: {exc}"

    def __iter__(self):
        if self._started:
            raise RuntimeError("RAW replay is single-pass; open a new RawReplay to repeat")
        self._started = True
        with self.path.open("rb") as fh:
            if fh.read(len(MAGIC)) != MAGIC:
                self.archive_problem = "missing, truncated or unsupported RAW archive header"
                return
            digest = hashlib.sha256(MAGIC)
            size = len(MAGIC)
            while header := fh.read(_RECORD.size):
                packet = self._read_record(fh, header, digest)
                if packet is None:
                    return
                size += _RECORD.size + len(packet.wire) + _CRC.size
                self._continuity.observe(packet.envelope, packet.raw, packet.host_rx_us,
                                         packet.observed_boot_id)
                yield packet
            if self.meta and self.meta.get("complete") is True:
                raw = self.meta["raw"]
                if raw.get("archive_bytes") != size or raw.get("archive_sha256") != digest.hexdigest():
                    self.archive_problem = "archive size/SHA-256 differs from clean-close sidecar"

    def _read_record(self, fh, header: bytes, digest) -> RecordedRaw | None:
        try:
            if len(header) != _RECORD.size:
                raise ValueError("truncated record header")
            length, flags, rx_us, boot = _RECORD.unpack(header)
            if not _MIN_WIRE <= length <= _MAX_WIRE or flags not in (0, 1) or (not flags and boot):
                raise ValueError("invalid record length or context flags")
            body = fh.read(length + _CRC.size)
            if len(body) != length + _CRC.size:
                raise ValueError("truncated record body")
            wire, checksum = body[:length], _CRC.unpack_from(body, length)[0]
            if zlib.crc32(header + wire) != checksum:
                raise ValueError("record CRC mismatch")
            packet = decode_raw(wire, rx_us, boot if flags else None)
        except (ValueError, WireError) as exc:
            self.archive_problem = str(exc)
            return None
        digest.update(header)
        digest.update(body)
        return packet
