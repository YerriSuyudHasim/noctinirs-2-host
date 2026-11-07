"""Read a recording back. Tolerant of truncation: every complete frame is returned, the
partial last frame is dropped and counted. Frames can be turned back into the exact
datagrams that were received (bit-exact replay)."""

from __future__ import annotations

import csv
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..control.protocol import encode_frame
from ..wire.envelope import parse_envelope
from ..wire.errors import WireError
from ..wire.optical import parse_opt2
from .codec import COLUMNS, FRAME_COLS, ROWS_PER_FRAME, rows_to_payload
from .meta import validate_metadata


@dataclass(frozen=True)
class RecordedFrame:
    host_rx_us: int
    session_id: int
    msg_sequence: int
    env_flags: int
    device_ts_us: int
    payload: bytes

    def datagram(self) -> bytes:
        """The datagram as it was received (envelope flags and fields reproduced)."""
        return encode_frame(self.payload, simulated=bool(self.env_flags & 1),
                            session_id=self.session_id, sequence=self.msg_sequence,
                            timestamp_us=self.device_ts_us, flags=self.env_flags)


@dataclass
class Recording:
    csv_path: Path
    frames: list[RecordedFrame] = field(default_factory=list)
    meta: dict | None = None
    meta_problem: str | None = None
    partial_frames_dropped: int = 0
    bad_rows: int = 0
    header_ok: bool = True

    @property
    def simulated(self) -> bool | None:
        if self.meta is not None and "simulated" in self.meta:
            return self.meta["simulated"]
        if not self.frames:
            return None
        return all(f.env_flags & 1 for f in self.frames)

    @property
    def duration_s(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        return (self.frames[-1].device_ts_us - self.frames[0].device_ts_us) / 1e6


def _load_meta(csv_path: Path) -> tuple[dict | None, str | None]:
    jp = csv_path.with_suffix(".json")
    if not jp.is_file():
        return None, "sidecar .json is missing"
    try:
        meta = json.loads(jp.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None, "sidecar .json must contain an object"
        missing = validate_metadata(meta)
        return meta, f"sidecar missing fields: {', '.join(missing)}" if missing else None
    except (OSError, ValueError) as e:
        return None, f"sidecar .json unreadable: {e}"


def read_recording(csv_path: str | Path) -> Recording:
    path = Path(csv_path)
    rec = Recording(path)
    rec.meta, rec.meta_problem = _load_meta(path)
    if rec.meta and rec.meta.get("complete") is True:
        with path.open("rb") as fh:
            digest = hashlib.file_digest(fh, "sha256").hexdigest()
        if rec.meta.get("csv_bytes") != path.stat().st_size or rec.meta.get("csv_sha256") != digest:
            problem = "CSV size/SHA256 differs from clean-close sidecar; salvaging complete frames"
            rec.meta_problem = "; ".join(filter(None, (rec.meta_problem, problem)))
    group: list[list[str]] = []
    key = None

    def flush_group() -> None:
        nonlocal group
        if not group:
            return
        if len(group) != ROWS_PER_FRAME:
            rec.partial_frames_dropped += 1
        else:
            try:
                vals, payload = rows_to_payload(group)
                frame = RecordedFrame(vals["host_rx_us"], vals["session_id"],
                                      vals["msg_sequence"], vals["env_flags"],
                                      vals["device_ts_us"], payload)
                # Edited or torn numeric cells must not escape as malformed replay packets.
                parse_opt2(payload)
                parse_envelope(frame.datagram())
            except (ValueError, IndexError, struct.error, OverflowError, WireError):
                rec.bad_rows += len(group)
            else:
                rec.frames.append(frame)
        group = []

    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        if fh.readline().rstrip("\r\n").split(",") != list(COLUMNS):
            rec.header_ok = False
            return rec
        def complete_lines():
            for line in fh:
                if line.endswith("\n"):
                    yield line
                else:
                    rec.bad_rows += 1

        rows = csv.reader(complete_lines(), strict=True)
        while True:
            try:
                row = next(rows)
            except StopIteration:
                break
            except csv.Error:
                rec.bad_rows += 1
                continue
            if len(row) != len(COLUMNS):
                rec.bad_rows += 1
                continue
            k = tuple(row[:len(FRAME_COLS)])
            if k != key or len(group) == ROWS_PER_FRAME:
                flush_group()
                key = k
            group.append(row)
    flush_group()
    return rec
