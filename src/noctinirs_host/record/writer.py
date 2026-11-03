"""Recorder: received FRAME datagrams -> CSV rows + JSON sidecar, off the receiving thread.

Rules (issue #33):
* `feed()` never blocks: it puts (receive time, bytes) on a bounded queue; when the queue
  is full the datagram is dropped and counted (`queue_drops`), never silently.
* A writer thread parses (same strict parser as the live view) and writes. Frames are
  written complete (80 rows in one write) and flushed; fsync about once a second.
* The JSON sidecar says `complete: false` from the first moment and is refreshed
  atomically; a clean close sets `complete: true`, the CSV size and its SHA-256.
* Raw layout-3 packets and rejected datagrams are counted, not stored (stated in the
  metadata counters).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import __version__ as HOST_VERSION
from ..control.protocol import FIRMWARE_PIN
from ..geometry import Geometry
from ..wire import optical as opt
from ..wire.counters import RejectCounters
from ..wire.envelope import MSG_FRAME, parse_envelope
from ..wire.errors import WireError
from .codec import COLUMNS, frame_rows
from .meta import CONTRACT, SCHEMA

DEFAULT_QUEUE = 512
JSON_REFRESH_S = 2.0
FSYNC_S = 1.0


def _utc(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(time.time() if ts is None else ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def default_folder() -> Path:
    return Path.home() / "Documents" / "NoctiNIRS" / "recordings"


class Recorder:
    def __init__(self, folder: str | Path, geometry: Geometry, *, simulated: bool | None = None,
                 settings: Callable[[], dict] | None = None, queue_size: int = DEFAULT_QUEUE,
                 clock_us: Callable[[], int] | None = None, wall: Callable[[], float] = time.time,
                 opener: Callable = open, json_refresh_s: float = JSON_REFRESH_S,
                 name: str | None = None) -> None:
        self.folder = Path(folder)
        self.geometry = geometry
        self._wall = wall
        self._clock_us = clock_us or (lambda: int(wall() * 1_000_000))
        self._opener = opener
        self._settings = settings
        self._refresh_s = json_refresh_s
        tag = "sim" if simulated else ("dev" if simulated is False else "unk")
        stamp = datetime.fromtimestamp(wall(), tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.stem = name or f"noctinirs_{stamp}_{tag}"
        self.csv_path = self.folder / f"{self.stem}.csv"
        self.json_path = self.folder / f"{self.stem}.json"
        self._q: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._json_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._fh = None
        self._rejects = RejectCounters()
        self._events: list[dict] = []
        self._sessions: list[int] = []
        self._gates: set[int] = set()
        self._sim_flags: set[bool] = set()
        self.counters = dict(frames_written=0, rows_written=0, queue_drops=0, not_frame=0,
                             raw_packets_not_recorded=0, geometry_mismatch_frames=0)
        self._started_utc: str | None = None
        self._created_utc = _utc(wall())
        self._stopped_utc: str | None = None
        self._start_settings: dict = {}
        self._complete = False
        self._extra: dict = {}
        self.error: str | None = None
        self.running = False

    # ---- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            raise RuntimeError("already recording")
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.csv_path.exists() or self.json_path.exists():
            raise FileExistsError(f"{self.stem} already exists; recordings are never overwritten")
        self._start_settings = dict(self._settings() or {}) if self._settings else {}
        self._fh = self._opener(self.csv_path, "w", newline="", encoding="utf-8")
        self._fh.write(",".join(COLUMNS) + "\n")
        self._fh.flush()
        self._started_utc = _utc(self._wall())
        self._write_json()
        self.running = True
        self._thread = threading.Thread(target=self._run, name="nn2-recorder", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> bool:
        """Drain the queue and close. Returns True on a clean close (complete: true)."""
        if not self.running:
            return self._complete
        self._stop.set()
        assert self._thread is not None
        self._thread.join(timeout_s)
        clean = not self._thread.is_alive() and self.error is None
        self._stopped_utc = _utc(self._wall())
        if clean:
            self._fh.close()
            self._extra.update(self._file_facts())
            self._complete = True
        else:
            self._extra["close_timed_out" if self._thread.is_alive() else "close_error"] = True
        self.running = False
        self._write_json()
        return clean

    # ---- input (receiver thread) -------------------------------------------------------
    def feed(self, datagram: bytes) -> None:
        """Never blocks. Safe to call from the UI thread as a frame sink."""
        if not self.running:
            return
        try:
            self._q.put_nowait((self._clock_us(), bytes(datagram)))
        except queue.Full:
            self.counters["queue_drops"] += 1

    def event(self, kind: str, text: str) -> None:
        """Log a command / state / link event (copied into the sidecar with its time)."""
        with self._lock:
            self._events.append({"t_utc": _utc(self._wall()), "kind": kind, "text": text})

    def on_controller_event(self, ev) -> None:  # SessionController listener
        self.event(ev.kind, ev.text)

    # ---- worker --------------------------------------------------------------------------
    def _run(self) -> None:
        last_json = last_sync = time.monotonic()
        try:
            while True:
                try:
                    rx_us, data = self._q.get(timeout=0.1)
                except queue.Empty:
                    if self._stop.is_set():
                        break
                    continue
                self._handle(rx_us, data)
                now = time.monotonic()
                if now - last_sync >= FSYNC_S:
                    last_sync = now
                    try:
                        os.fsync(self._fh.fileno())
                    except (OSError, AttributeError, ValueError):
                        pass  # not every file object has a descriptor (tests); best effort
                if now - last_json >= self._refresh_s:
                    last_json = now
                    self._write_json()
            self._fh.flush()
        except Exception as e:  # noqa: BLE001 - report, never crash the app
            self.error = f"{type(e).__name__}: {e}"

    def _handle(self, rx_us: int, data: bytes) -> None:
        try:
            env = parse_envelope(data)
        except WireError as e:
            self._rejects.record_error(e)
            return
        if env.msg_type != MSG_FRAME:
            self.counters["not_frame"] += 1
            return
        frame = self._rejects.parse(env.payload)
        if frame is None:
            return
        if not isinstance(frame, opt.Opt2Frame):
            self.counters["raw_packets_not_recorded"] += 1
            return
        if frame.geometry_map_id != self.geometry.geometry_map_id:
            self.counters["geometry_mismatch_frames"] += 1
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(frame_rows(frame, env, rx_us, self.geometry))
        self._fh.write(buf.getvalue())  # one write per frame: a frame is whole or truncated
        self._fh.flush()
        self.counters["frames_written"] += 1
        self.counters["rows_written"] += buf.getvalue().count("\n")
        with self._lock:
            if env.session_id not in self._sessions:
                self._sessions.append(env.session_id)
            self._gates.add(frame.gate_config_id)
            self._sim_flags.add(env.simulated)

    # ---- metadata ---------------------------------------------------------------------------
    def _file_facts(self) -> dict:
        h = hashlib.sha256()
        with open(self.csv_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return {"csv_sha256": h.hexdigest(), "csv_bytes": self.csv_path.stat().st_size}

    def metadata(self) -> dict:
        with self._lock:
            events = list(self._events)
            sessions, gates, sims = list(self._sessions), sorted(self._gates), set(self._sim_flags)
        simulated = None if not sims else (True if sims == {True} else False)
        evidence = {True: "simulation", False: "device", None: "unknown"}[simulated]
        if len(sims) > 1:
            evidence = "mixed (simulated and non-simulated frames) - do not use as one evidence class"
        g = self.geometry
        counters = dict(self.counters)
        counters["rejected"] = dict(self._rejects.rejected)
        meta = {
            "schema": SCHEMA, "complete": self._complete, "host_version": HOST_VERSION,
            "created_utc": self._created_utc, "started_utc": self._started_utc,
            "stopped_utc": self._stopped_utc, "simulated": simulated, "evidence_class": evidence,
            "firmware_pin": FIRMWARE_PIN, "contract": CONTRACT,
            "geometry": {"map_id": g.geometry_map_id, "sha256": g.sha256, "status": g.status},
            "gate_config_ids": gates,
            "fs_hz": self._start_settings.get("fs_hz"),
            "led_current_ma": self._start_settings.get("led_current_ma"),
            "device_state_at_start": self._start_settings.get("device_state"),
            "session_ids": sessions, "events": events, "counters": counters,
            "csv_file": self.csv_path.name, "csv_sha256": None, "csv_bytes": None,
            "note": ("Fs and LED currents are the values at the start of the recording; later "
                     "changes are in events. Raw layout-3 packets and rejected datagrams are "
                     "counted, not stored."),
        }
        meta.update(self._extra)
        return meta

    def _write_json(self) -> None:
        with self._json_lock:  # worker refresh and stop() must not share the temp file
            tmp = self.json_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.metadata(), indent=2), encoding="utf-8")
            os.replace(tmp, self.json_path)
