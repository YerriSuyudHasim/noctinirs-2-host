"""Read a recording back. Tolerant of truncation: every complete frame is returned, the
partial last frame is dropped and counted. Frames can be turned back into the exact
datagrams that were received (bit-exact replay)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..control.protocol import encode_frame
from .codec import COLUMNS, FRAME_COLS, ROWS_PER_FRAME, rows_to_payload


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
        return json.loads(jp.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as e:
        return None, f"sidecar .json unreadable: {e}"


def read_recording(csv_path: str | Path) -> Recording:
    path = Path(csv_path)
    rec = Recording(path)
    rec.meta, rec.meta_problem = _load_meta(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] != "":
        lines.pop()  # last line has no newline: it was cut off
        rec.bad_rows += 1
    else:
        lines.pop()  # the empty string after the final newline
    if not lines or lines[0].split(",") != list(COLUMNS):
        rec.header_ok = False
        return rec
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
            except (ValueError, IndexError):
                rec.bad_rows += len(group)
            else:
                rec.frames.append(RecordedFrame(vals["host_rx_us"], vals["session_id"],
                                                vals["msg_sequence"], vals["env_flags"],
                                                vals["device_ts_us"], payload))
        group = []

    for row in csv.reader(lines[1:]):
        if len(row) != len(COLUMNS):
            rec.bad_rows += 1
            continue
        k = tuple(row[:len(FRAME_COLS)])
        if k != key:
            flush_group()
            key = k
        group.append(row)
    flush_group()
    return rec
