"""S3 core: recorder, reader, replay codec. Host/unit + simulation evidence only."""

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import pytest

from helpers import build_envelope, build_opt2, build_raw
from noctinirs_host.control.protocol import FIRMWARE_PIN
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.control.simulator import DeviceSimulator
from noctinirs_host.geometry import load_geometry
from noctinirs_host.live.receiver import FrameReceiver
from noctinirs_host.record.codec import COLUMNS, ROWS_PER_FRAME
from noctinirs_host.record.meta import REQUIRED_FIELDS, validate_metadata
from noctinirs_host.record.reader import read_recording
from noctinirs_host.record.writer import Recorder
from noctinirs_host.wire.optical import parse_opt2

ROOT = Path(__file__).resolve().parents[1]
GEO = load_geometry(ROOT / "config" / "optical_geometry_v1.json")


class Rig:
    def __init__(self, tmp_path, **rec_kw):
        self.t = [0.0]
        self.sim = DeviceSimulator(lambda: self.t[0], geometry=GEO)
        self.tr = LoopbackTransport(self.sim)
        self.ctl = SessionController(self.tr, lambda: self.t[0])
        self.rx = FrameReceiver(GEO)
        self.originals: list[bytes] = []
        assert self.ctl.connect().ok
        self.rec = Recorder(tmp_path, GEO, simulated=True, name="rec",
                            clock_us=lambda: int(self.t[0] * 1e6),
                            settings=lambda: dict(fs_hz=self.ctl.settings.fs_hz,
                                                  led_current_ma=list(self.ctl.settings.current_ma),
                                                  device_state=self.ctl.state_text),
                            **rec_kw)
        self.ctl.listeners.append(self.rec.on_controller_event)
        self.ctl.frame_sink = self._sink

    def _sink(self, raw):
        self.originals.append(raw)
        self.rx.feed(raw)
        self.rec.feed(raw)

    def run(self, seconds, step=0.05):
        for _ in range(round(seconds / step)):
            self.t[0] += step
            self.ctl.tick()


def settle(rec, n, timeout=5.0):
    """Wait until the worker has written n frames (it runs in its own thread)."""
    end = time.monotonic() + timeout
    while rec.counters["frames_written"] < n and time.monotonic() < end:
        time.sleep(0.01)
    assert rec.counters["frames_written"] >= n


def test_round_trip_is_bit_exact(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(2.2)
    assert r.ctl.stop().ok
    assert r.rec.stop() is True
    rec = read_recording(r.rec.csv_path)
    assert len(r.originals) >= 20 and len(rec.frames) == len(r.originals)
    assert [f.datagram() for f in rec.frames] == r.originals  # byte for byte
    assert rec.partial_frames_dropped == 0 and rec.bad_rows == 0 and rec.header_ok
    # and the parsed frames are equal too
    from noctinirs_host.wire.envelope import parse_envelope
    for f, raw in zip(rec.frames, r.originals):
        assert parse_opt2(f.payload) == parse_opt2(parse_envelope(raw).payload)
    assert rec.frames[3].host_rx_us > rec.frames[2].host_rx_us


def test_replay_reproduces_the_live_buffers(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.start().ok
    r.run(3.1)
    r.rec.stop()
    replay = FrameReceiver(GEO)
    for f in read_recording(r.rec.csv_path).frames:
        assert replay.feed(f.datagram()) == "ok"
    for a, b in zip(r.rx.buffers, replay.buffers):
        for wl in ("735", "850"):
            assert a.points(wl) == b.points(wl)
    assert replay.counters == r.rx.counters


def test_metadata_has_every_required_field(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.set_fs(5).ok and r.ctl.set_led_current(0, 20).ok and r.ctl.start().ok
    r.run(1.2)
    assert r.ctl.stop().ok
    assert r.rec.stop()
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    assert validate_metadata(meta) == []
    assert meta["complete"] is True and meta["stopped_utc"] and meta["started_utc"]
    assert meta["firmware_pin"] == FIRMWARE_PIN
    assert meta["geometry"] == {"map_id": GEO.geometry_map_id, "sha256": GEO.sha256,
                                "status": GEO.status}
    assert meta["simulated"] is True and meta["evidence_class"] == "simulation"
    assert meta["session_ids"] == [1] and meta["gate_config_ids"] == [1]
    assert meta["fs_hz"] == 5 and len(meta["led_current_ma"]) == 20  # values at the start
    kinds = {e["kind"] for e in meta["events"]}
    texts = " ".join(e["text"] for e in meta["events"])
    assert {"command", "state"} <= kinds and "set_fs" in texts and "set_led_current" in texts
    sha = hashlib.sha256(r.rec.csv_path.read_bytes()).hexdigest()
    assert meta["csv_sha256"] == sha and meta["csv_bytes"] == r.rec.csv_path.stat().st_size
    assert meta["counters"]["frames_written"] == len(r.originals) >= 5
    assert meta["counters"]["queue_drops"] == 0


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_a_missing_metadata_field_is_detected(tmp_path, field):
    r = Rig(tmp_path)
    r.rec.start()
    r.rec.stop()
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    node = meta
    *parents, leaf = field.split(".")
    for p in parents:
        node = node[p]
    del node[leaf]
    assert validate_metadata(meta) == [field]


def test_killed_recording_is_readable_and_marked_incomplete(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(1.0)
    settle(r.rec, 10)
    # "crash": read the files while the recorder is still open, never call stop()
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    assert meta["complete"] is False and meta["csv_sha256"] is None
    rec = read_recording(r.rec.csv_path)
    assert len(rec.frames) == 10 and rec.meta["complete"] is False
    r.rec.stop()


@pytest.mark.parametrize("cut", [1, 7, 150, 5000, 9000])
def test_truncated_csv_keeps_every_complete_frame(tmp_path, cut):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(1.0)
    r.rec.stop()
    data = r.rec.csv_path.read_bytes()
    r.rec.csv_path.write_bytes(data[:-cut])
    rec = read_recording(r.rec.csv_path)
    assert len(rec.frames) in (len(r.originals) - 1, len(r.originals) - 2)
    assert [f.datagram() for f in rec.frames] == r.originals[:len(rec.frames)]
    assert rec.partial_frames_dropped + rec.bad_rows >= 1


def test_slow_disk_never_blocks_the_receiver_and_drops_are_counted(tmp_path):
    gate = threading.Event()

    class SlowFile:
        def __init__(self, fh):
            self._fh = fh
            self.armed = False

        def write(self, s):
            if self.armed:
                gate.wait(10)
            return self._fh.write(s)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    holder = {}

    def opener(path, mode, **kw):
        holder["f"] = SlowFile(open(path, mode, **kw))
        return holder["f"]

    r = Rig(tmp_path, queue_size=8, opener=opener)
    r.rec.start()
    holder["f"].armed = True  # from now on every write waits for the gate
    good = build_envelope(build_opt2(frame_sequence=1), msg_type=2)
    t0 = time.monotonic()
    for i in range(200):
        r.rec.feed(build_envelope(build_opt2(frame_sequence=i), msg_type=2, msg_sequence=i))
    assert time.monotonic() - t0 < 1.0  # the receiver side did not wait for the disk
    drops = r.rec.counters["queue_drops"]
    assert drops >= 150
    gate.set()
    assert r.rec.stop(timeout_s=10) is True
    c = r.rec.counters
    assert c["frames_written"] + c["queue_drops"] == 200  # every datagram accounted for
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    assert meta["counters"]["queue_drops"] == c["queue_drops"] >= 150
    del good


def test_stop_does_not_hang_on_a_stuck_disk(tmp_path):
    gate = threading.Event()

    class Stuck:
        def __init__(self, fh):
            self._fh, self.armed = fh, False

        def write(self, s):
            if self.armed:
                gate.wait(10)
            return self._fh.write(s)

        def __getattr__(self, n):
            return getattr(self._fh, n)

    holder = {}

    def opener(path, mode, **kw):
        holder["f"] = Stuck(open(path, mode, **kw))
        return holder["f"]

    r = Rig(tmp_path, opener=opener)
    r.rec.start()
    holder["f"].armed = True
    r.rec.feed(build_envelope(build_opt2(), msg_type=2))
    time.sleep(0.2)
    t0 = time.monotonic()
    assert r.rec.stop(timeout_s=0.5) is False
    assert time.monotonic() - t0 < 3
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    assert meta["complete"] is False and meta.get("close_timed_out") is True
    gate.set()


def test_names_come_from_the_geometry_only(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.ctl.start().ok
    r.run(0.5)
    r.rec.stop()
    lines = r.rec.csv_path.read_text(encoding="utf-8").splitlines()
    head = lines[0].split(",")
    rows = [dict(zip(head, ln.split(","))) for ln in lines[1:1 + ROWS_PER_FRAME]]
    for row in rows:
        slot, ain = int(row["slot"]), int(row["ain"])
        src, wl = GEO.source_for_slot(slot)
        assert (row["source"], row["wavelength"], row["pd"]) == (src.id, wl, GEO.pd_for_ain(ain).pd)
        ch = GEO.channel_for(slot, ain)
        assert row["channel"] == (ch.name if ch else "")
    # no module of the recorder carries its own AIN/PD/source table
    for p in (ROOT / "src" / "noctinirs_host" / "record").glob("*.py"):
        assert not re.search(r"\bPD[1-4]\b|\bL(?:10|[1-9])\b|ain_to_pd", p.read_text(encoding="utf-8")), p


def test_frames_with_a_different_geometry_id_are_recorded_without_names(tmp_path):
    r = Rig(tmp_path)
    r.sim.geometry_map_id = GEO.geometry_map_id + 1
    r.rec.start()
    assert r.ctl.start().ok
    r.run(0.5)
    r.rec.stop()
    lines = r.rec.csv_path.read_text(encoding="utf-8").splitlines()
    row = dict(zip(lines[0].split(","), lines[1].split(",")))
    assert row["source"] == row["pd"] == row["channel"] == ""
    assert "geometry_map_id_differs" in row["host_note"]
    assert r.rec.counters["geometry_mismatch_frames"] == r.rec.counters["frames_written"] > 0


def test_non_science_frames_are_recorded_with_their_flags(tmp_path):
    r = Rig(tmp_path)
    r.sim.warmup_frames = 2
    r.rec.start()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(0.5)
    r.rec.stop()
    rec = read_recording(r.rec.csv_path)
    n = len(r.originals)
    assert n >= 4 and len(rec.frames) == n
    flags = [parse_opt2(f.payload).frame_flags for f in rec.frames]
    assert flags == [1, 1] + [0] * (n - 2)
    assert "non_science" in r.rec.csv_path.read_text(encoding="utf-8").splitlines()[1]


def test_rejects_raw_packets_and_non_frames_are_counted_not_stored(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    r.rec.feed(b"\x00" * 40)  # not a datagram
    r.rec.feed(build_envelope(b"\x01" * 10, msg_type=2))  # FRAME with a bad payload
    r.rec.feed(build_envelope(build_raw(), msg_type=2))  # raw layout 3
    r.rec.feed(build_envelope(b"", msg_type=1))  # a reply, not a frame
    r.rec.feed(build_envelope(build_opt2(), msg_type=2))  # the one good frame
    r.rec.stop()
    meta = json.loads(r.rec.json_path.read_text(encoding="utf-8"))
    c = meta["counters"]
    assert c["frames_written"] == 1 and c["raw_packets_not_recorded"] == 1 and c["not_frame"] == 1
    assert sum(c["rejected"].values()) == 2
    assert len(read_recording(r.rec.csv_path).frames) == 1


def test_a_recording_is_never_overwritten(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    r.rec.stop()
    again = Recorder(tmp_path, GEO, simulated=True, name="rec")
    with pytest.raises(FileExistsError):
        again.start()


def test_reader_reports_a_wrong_header_and_a_missing_sidecar(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("not,a,recording\n1,2,3\n", encoding="utf-8")
    rec = read_recording(p)
    assert rec.header_ok is False and rec.frames == [] and "missing" in rec.meta_problem
    assert len(COLUMNS) == 36
