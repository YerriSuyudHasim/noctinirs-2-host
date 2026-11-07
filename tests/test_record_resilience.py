"""PR-A regression tests: host/unit and subprocess simulation, never device evidence."""

import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace

import pytest

from helpers import build_envelope, build_opt2
from test_record import GEO, ROOT, Rig, settle
from noctinirs_host.record.codec import COLUMNS, ROWS_PER_FRAME
from noctinirs_host.record.reader import read_recording
from noctinirs_host.record.writer import Recorder
from noctinirs_host.record import writer


@pytest.mark.parametrize("capacity", [0, -1, False, 1.5])
def test_queue_must_be_bounded(tmp_path, capacity):
    with pytest.raises(ValueError, match="positive integer"):
        Recorder(tmp_path, GEO, queue_size=capacity)


def test_identical_retransmitted_frames_are_not_coalesced(tmp_path):
    rec = Recorder(tmp_path, GEO, name="dups", clock_us=lambda: 123)
    data = build_envelope(build_opt2(), msg_type=2, flags=3)
    rec.start()
    for _ in range(3):
        rec.feed(data)
    assert rec.stop()
    loaded = read_recording(rec.csv_path)
    assert [f.datagram() for f in loaded.frames] == [data] * 3
    assert [f.host_rx_us for f in loaded.frames] == [123] * 3


@pytest.mark.parametrize("column,value", [
    ("sum_on", str(2**40)), ("stab_q", "256"), ("env_flags", "65536"),
    ("session_id", "-1"), ("host_rx_us", "torn"), ("slot", "99"),
])
def test_corrupt_numeric_cells_do_not_abort_salvage(tmp_path, column, value):
    r = Rig(tmp_path)
    r.rec.start()
    originals = [build_envelope(build_opt2(frame_sequence=i), msg_type=2) for i in range(3)]
    for data in originals:
        r.rec.feed(data)
    assert r.rec.stop()
    rows = list(csv.reader(io.StringIO(r.rec.csv_path.read_text())))
    rows[1][COLUMNS.index(column)] = value
    with r.rec.csv_path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    loaded = read_recording(r.rec.csv_path)
    assert [f.datagram() for f in loaded.frames] == originals[1:]
    assert loaded.bad_rows + loaded.partial_frames_dropped > 0
    assert "SHA256" in loaded.meta_problem


@pytest.mark.parametrize("sidecar", ["[]", "null", "{broken", "{}"])
def test_broken_sidecar_does_not_hide_csv(tmp_path, sidecar):
    r = Rig(tmp_path)
    r.rec.start()
    data = build_envelope(build_opt2(), msg_type=2)
    r.rec.feed(data)
    assert r.rec.stop()
    r.rec.json_path.write_text(sidecar)
    loaded = read_recording(r.rec.csv_path)
    assert [f.datagram() for f in loaded.frames] == [data]
    assert loaded.meta_problem


def test_all_last_frame_row_boundaries_recover_exact_prefix(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    originals = [build_envelope(build_opt2(frame_sequence=i), msg_type=2) for i in range(2)]
    for data in originals:
        r.rec.feed(data)
    assert r.rec.stop()
    lines = r.rec.csv_path.read_bytes().splitlines(keepends=True)
    for n in range(ROWS_PER_FRAME):
        prefix = b"".join(lines[:1 + ROWS_PER_FRAME + n])
        r.rec.csv_path.write_bytes(prefix)
        loaded = read_recording(r.rec.csv_path)
        assert [f.datagram() for f in loaded.frames] == originals[:1]
        assert loaded.partial_frames_dropped == bool(n)
    r.rec.csv_path.write_bytes(b"")
    assert read_recording(r.rec.csv_path).header_ok is False


def test_process_kill_leaves_flushed_frames_and_incomplete_sidecar(tmp_path):
    script = '''
import sys, time
from pathlib import Path
from helpers import build_envelope, build_opt2
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.writer import Recorder
folder = Path(sys.argv[1])
rec = Recorder(folder, load_geometry(sys.argv[2]), name="killed", simulated=True)
rec.start()
for i in range(3):
    rec.feed(build_envelope(build_opt2(frame_sequence=i), msg_type=2, flags=1))
end = time.monotonic() + 5
while rec.counters["frames_written"] != 3 and time.monotonic() < end:
    time.sleep(.01)
assert rec.counters["frames_written"] == 3
(folder / "ready").touch()
time.sleep(30)
'''
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]))
    child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path),
                              str(ROOT / "config/optical_geometry_v1.json")], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(.01)
        assert (tmp_path / "ready").exists(), "child failed to flush frames before kill"
    finally:
        child.kill()
        child.communicate(timeout=5)
    loaded = read_recording(tmp_path / "killed.csv")
    assert len(loaded.frames) == 3 and loaded.meta["complete"] is False
    assert loaded.meta["csv_sha256"] is None
    assert [f.datagram() for f in loaded.frames] == [
        build_envelope(build_opt2(frame_sequence=i), msg_type=2, flags=1) for i in range(3)]


@pytest.mark.parametrize("operation", ["fsync", "close", "hash", "sidecar"])
def test_stop_deadline_includes_disk_finalization(tmp_path, monkeypatch, operation):
    release, entered = threading.Event(), threading.Event()
    r = Rig(tmp_path)
    r.rec.start()
    r.rec.feed(build_envelope(build_opt2(), msg_type=2))
    settle(r.rec, 1)

    def stall():
        entered.set()
        assert release.wait(5), "test did not release fake disk"

    if operation == "hash":
        original = r.rec._file_facts
        def blocked():
            stall()
            return original()
        monkeypatch.setattr(r.rec, "_file_facts", blocked)
    elif operation == "sidecar":
        original = r.rec._write_json
        def blocked():
            stall()
            return original()
        monkeypatch.setattr(r.rec, "_write_json", blocked)
    elif operation == "fsync":
        original, csv_fd = writer.os.fsync, r.rec._fh.fileno()
        def blocked(fd):
            if fd == csv_fd:
                stall()
            return original(fd)
        monkeypatch.setattr(writer.os, "fsync", blocked)
    else:
        original = r.rec._fh
        class BlockedClose:
            def close(self):
                stall()
                return original.close()
            def __getattr__(self, name):
                return getattr(original, name)
        r.rec._fh = BlockedClose()
    try:
        t0 = time.monotonic()
        assert r.rec.stop(timeout_s=.3) is False
        assert time.monotonic() - t0 < 1
        assert entered.is_set()
        # If the sidecar itself is unavailable, on-disk state stays incomplete until recovery.
        if operation != "sidecar":
            assert json.loads(r.rec.json_path.read_text())["close_timed_out"] is True
    finally:
        release.set()
        r.rec._thread.join(5)
    assert not r.rec._thread.is_alive() and r.rec._fh.closed
    meta = json.loads(r.rec.json_path.read_text())
    assert meta["complete"] is False and meta["close_timed_out"] is True
    assert r.rec.stop() is False


def test_fsync_failure_is_reported_instead_of_clean_close(tmp_path, monkeypatch):
    r = Rig(tmp_path)
    r.rec.start()
    r.rec.feed(build_envelope(build_opt2(), msg_type=2))
    settle(r.rec, 1)
    original, csv_fd = writer.os.fsync, r.rec._fh.fileno()
    def failed(fd):
        if fd == csv_fd and not r.rec._fh.closed:
            raise OSError("injected disk failure")
        return original(fd)
    monkeypatch.setattr(writer.os, "fsync", failed)
    assert r.rec.stop() is False
    meta = json.loads(r.rec.json_path.read_text())
    assert meta["complete"] is False and "injected disk failure" in meta["writer_error"]
    assert r.rec._fh.closed


def test_provenance_does_not_infer_device_identity_or_evidence_from_flags(tmp_path):
    info, settings = {"serial": "synthetic"}, {"fs_hz": 5, "mode": "ENGINEERING", "led_current_ma": [0]}
    rec = Recorder(tmp_path, GEO, name="provenance", host_sha="a" * 40,
                   firmware_sha="b" * 40, firmware_sha_source="operator_declaration",
                   observed_info=info, settings=lambda: settings)
    rec.start()
    info["serial"] = "changed"
    settings["led_current_ma"][0] = 20
    rec.feed(build_envelope(build_opt2(), msg_type=2, flags=0))
    assert rec.stop()
    meta = json.loads(rec.json_path.read_text())
    assert meta["host_sha"] == "a" * 40 and meta["host_sha_source"] == "caller_supplied"
    assert meta["firmware"] == dict(declared_sha="b" * 40, sha_source="operator_declaration",
                                    observed_info={"serial": "synthetic"}, boot_id=None)
    assert meta["settings_at_start"]["led_current_ma"] == [0]
    assert meta["evidence_class"] == "unknown" and meta["simulated"] is False
    assert "not device identity" in meta["firmware_pin_source"]


def test_unknown_provenance_stays_unknown_and_sha_requires_source(tmp_path):
    rec = Recorder(tmp_path, GEO)
    meta = rec.metadata()
    assert meta["host_sha"] is None
    assert all(value is None for value in meta["firmware"].values())
    with pytest.raises(ValueError, match="provenance"):
        Recorder(tmp_path, GEO, firmware_sha="b" * 40)


def test_recorder_instance_cannot_restart_after_close(tmp_path):
    r = Rig(tmp_path)
    r.rec.start()
    assert r.rec.stop()
    with pytest.raises(RuntimeError, match="single-use"):
        r.rec.start()


def test_idle_recorder_refreshes_metadata_and_syncs_without_more_packets(tmp_path, monkeypatch):
    r = Rig(tmp_path, json_refresh_s=.01)
    r.rec.start()
    original, csv_fd = writer.os.fsync, r.rec._fh.fileno()
    synced = threading.Event()
    def observed(fd):
        if fd == csv_fd:
            synced.set()
        return original(fd)
    monkeypatch.setattr(writer.os, "fsync", observed)
    monkeypatch.setattr(writer, "FSYNC_S", .01)
    try:
        r.rec.feed(build_envelope(build_opt2(), msg_type=2))
        settle(r.rec, 1)
        r.rec.event("fault", "synthetic idle fault")
        end = time.monotonic() + 3
        while time.monotonic() < end:
            meta = json.loads(r.rec.json_path.read_text())
            if meta["events"] and synced.is_set():
                break
            time.sleep(.01)
        assert synced.is_set() and meta["events"][0]["text"] == "synthetic idle fault"
        assert meta["complete"] is False and meta["counters"]["frames_written"] == 1
    finally:
        r.rec.stop()


def test_short_frame_write_is_explicit_and_partial_frame_is_salvageable(tmp_path):
    holder = {}
    class ShortFile:
        def __init__(self, fh):
            self.fh, self.armed = fh, False
        def write(self, text):
            return self.fh.write(text[:100] if self.armed else text)
        def __getattr__(self, name):
            return getattr(self.fh, name)
    def opener(path, mode, **kwargs):
        holder["file"] = ShortFile(open(path, mode, **kwargs))
        return holder["file"]
    r = Rig(tmp_path, opener=opener)
    r.rec.start()
    holder["file"].armed = True
    r.rec.feed(build_envelope(build_opt2(), msg_type=2))
    assert r.rec.stop() is False
    meta = json.loads(r.rec.json_path.read_text())
    assert meta["complete"] is False and "short CSV frame write" in meta["writer_error"]
    loaded = read_recording(r.rec.csv_path)
    assert loaded.frames == [] and loaded.bad_rows + loaded.partial_frames_dropped > 0


def test_exclusive_csv_creation_preserves_a_competing_file(tmp_path):
    def opener(path, mode, **kwargs):
        path.write_bytes(b"competing recorder")  # appeared after the existence check
        return open(path, mode, **kwargs)
    r = Rig(tmp_path, opener=opener)
    with pytest.raises(FileExistsError):
        r.rec.start()
    assert r.rec.csv_path.read_bytes() == b"competing recorder"
    assert json.loads(r.rec.json_path.read_text())["complete"] is False


def test_geometry_information_with_csv_quoting_does_not_change_payload_or_row_count(tmp_path):
    geo = replace(GEO, sources=(replace(GEO.sources[0], id='synthetic,"source"\nlabel'),)
                  + GEO.sources[1:])
    rec = Recorder(tmp_path, geo, name="quoted")
    rec.start()
    data = build_envelope(build_opt2(), msg_type=2)
    rec.feed(data)
    assert rec.stop()
    loaded = read_recording(rec.csv_path)
    assert [f.datagram() for f in loaded.frames] == [data]
    assert loaded.meta["counters"]["rows_written"] == ROWS_PER_FRAME
