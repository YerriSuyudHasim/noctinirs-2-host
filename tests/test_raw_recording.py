"""Synthetic host-only tests of RAW recording, replay and failure recovery."""

import csv
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from helpers import build_envelope, build_opt2, build_raw, raw_sample
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import MAGIC, RawReplay
from noctinirs_host.record.reader import read_recording
from noctinirs_host.record.writer import Recorder

GEOMETRY_PATH = Path(__file__).resolve().parents[1] / "config/optical_geometry_v1.json"
GEOMETRY = load_geometry(GEOMETRY_PATH)


def wire(first=0, count=1, *, sid=7, epoch=1, ts=1000, msg=10, flags=2, gap=False):
    sample = raw_sample((-8388608, 8388607, -1, 0), phase=8, slot_idx=19)
    return build_envelope(build_raw([sample] * count, first=first, epoch=epoch,
                                    raw_flags=3 if gap else 1), session_id=sid,
                          msg_sequence=msg, flags=flags, ts=ts)


def recorder(folder, **kwargs):
    return Recorder(folder, GEOMETRY, name="capture", record_raw=True, **kwargs)


def replay(rec):
    return RawReplay(rec.raw_path)


def sidecar(rec):
    return json.loads(rec.json_path.read_text(encoding="utf-8"))


def assert_raw_balance(meta, *, enqueued, persisted, dropped, rows):
    raw = meta["raw"]
    assert raw["observed_before_queue"]["packets"] == enqueued + dropped
    assert raw["packets_enqueued"] == enqueued
    assert raw["persisted_archive"]["packets"] == persisted
    assert raw["enqueued_not_confirmed_in_archive"] == enqueued - persisted
    assert raw["queue_drops"] == dropped
    assert meta["counters"]["queue_drops"] >= dropped
    assert raw["csv_rows_written"] == rows <= raw["persisted_archive"]["samples"]


def test_mixed_capture_round_trips_exact_wire_and_projects_raw_rows(tmp_path):
    rec = recorder(tmp_path, clock_us=lambda: 70)
    rec.start()
    opt = build_envelope(build_opt2(), flags=1)
    rec.feed(opt)
    packets = [wire(0xffffffff, msg=0xffffffff, flags=3, ts=2**64-1),
               wire(0, 100, msg=42, flags=0, sid=0xffffffff, epoch=0xffff, gap=True)]
    assert rec.feed_raw(packets[0], 2**64-1, 0)  # zero is a known boot ID
    rec.feed(packets[1])
    assert rec.stop()
    loaded = replay(rec)
    raw1, raw2 = list(loaded)
    assert [raw1.datagram(), raw2.datagram()] == packets
    assert (raw1.host_rx_us, raw2.host_rx_us) == (2**64-1, 70)
    assert (raw1.observed_boot_id, raw2.observed_boot_id) == (0, None)
    assert (raw1.envelope.flags, raw2.envelope.flags) == (3, 0)
    assert (raw1.envelope.timestamp_us, raw2.envelope.session_id) == (2**64-1, 0xffffffff)
    assert not raw1.raw.is_science and not raw2.raw.is_science
    assert raw2.raw.sample_count == 100
    assert loaded.archive_problem is None and loaded.meta_problem is None
    meta = sidecar(rec)
    assert meta["raw"]["persisted_archive"] == loaded.diagnostics
    assert meta["raw"]["classification"] == "ENGINEERING/non-science"
    assert meta["raw"]["observed_before_queue"] == loaded.diagnostics
    assert meta["raw"]["csv_rows_written"] == 101
    with (tmp_path / "capture.raw.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 101
    assert tuple(int(rows[-1][f"ain{i}"]) for i in range(4)) == (-8388608, 8388607, -1, 0)
    assert (rows[0]["sample_sequence"], rows[1]["sample_sequence"], rows[-1]["sample_sequence"]) == (
        "4294967295", "0", "99")
    assert (rows[0]["observed_boot_id"], rows[1]["observed_boot_id"]) == ("0", "")
    assert (rows[-1]["phase_class"], rows[-1]["slot_index"]) == ("8", "19")
    assert [frame.datagram() for frame in read_recording(rec.csv_path).frames] == [opt]


def test_u32_wrap_gap_recovery_duplicate_overlap_reorder_and_context_changes(tmp_path):
    rec = recorder(tmp_path)
    rec.start()
    rows = [
        (wire(0xfffffffe, 2, epoch=65535, ts=1000), 100, 10),
        (wire(0, 2, epoch=65535, ts=1250, msg=80), 101, 10),  # wrap and missing envelope IDs
        (wire(5, 2, epoch=65535, ts=1875, gap=True), 102, 10),  # missing 2, 3, 4
        (wire(3, 1, epoch=65535, ts=1625), 103, 10),  # late 3
        (wire(5, 2, epoch=65535, ts=1875), 104, 10),  # duplicate
        (wire(4, 2, epoch=65535, ts=1750), 105, 10),  # late 4 plus overlap of 5
        (wire(6, 2, epoch=65535, ts=2000), 106, 10),  # overlap with new forward suffix
        (wire(8, 2, epoch=65535, ts=3000), 107, 10),
        (wire(10, 1, epoch=65535, ts=2999), 106, 10),  # device backstep and host backstep
        (wire(500, epoch=0), 108, 10),  # epoch u16 wrap; new segment
        (wire(11, epoch=65535), 109, 10),  # delayed old epoch
        (wire(0, sid=8), 110, 10),  # new session
        (wire(501, epoch=0), 111, 10),  # delayed old session
        (wire(0, sid=8), 112, 11),  # reused session ID after boot context change
    ]
    for packet, rx, boot in rows:
        assert rec.feed_raw(packet, rx, boot)
    assert rec.stop()
    loaded = replay(rec)
    assert [packet.datagram() for packet in loaded] == [row[0] for row in rows]
    d = loaded.diagnostics
    expected = dict(segments=4, session_changes=1, epoch_changes=1, boot_context_changes=1,
                    old_session_packets=1, old_epoch_packets=1, gap_packets=1, gap_before_packets=1,
                    forward_gap_samples=3, late_gap_samples_observed=2,
                    forward_gap_samples_not_seen_late=1, duplicate_packets=1, overlap_packets=2,
                    reorder_packets=1, device_timestamp_nonincreasing=1, host_clock_backsteps=1)
    assert {k: d[k] for k in expected} == expected
    assert d["adc_rate_ts_estimated"] == dict(hz=8000, sample_steps=2, span_us=250, anchor_pairs=1)
    assert d["adc_rate_ts_not_flagged"]["hz"] is None
    assert sidecar(rec)["raw"]["persisted_archive"] == d


def test_wrap_order_ambiguity_and_bounded_history_are_visible(tmp_path):
    rec = recorder(tmp_path)
    rec.start()
    for packet in (wire(1), wire(2 + 2**31), wire(2, epoch=1 + 2**15), wire(8000), wire(1),
                   wire(8001, ts=2000, flags=0), wire(8002, ts=2125, flags=0)):
        assert rec.feed_raw(packet, 100)
    assert rec.stop()
    d = sidecar(rec)["raw"]["observed_before_queue"]
    assert d["ambiguous_sequence_packets"] == d["ambiguous_epoch_packets"] == 1
    assert d["history_unknown_packets"] == 1
    assert d["gap_packets"] == 1 and d["forward_gap_samples"] == 7998
    assert d["adc_rate_ts_not_flagged"]["hz"] == 8000


def test_timestamp_backsteps_and_flag_changes_do_not_pollute_rates(tmp_path):
    rec = recorder(tmp_path)
    rec.start()
    anchors = [(1000, 2), (1125, 2), (1125, 2), (1100, 2), (2000, 2), (2125, 2),
               (2250, 0), (2375, 0)]
    for i, (ts, flags) in enumerate(anchors):
        assert rec.feed_raw(wire(i, ts=ts, flags=flags), 500 + i)
    assert rec.stop()
    loaded = replay(rec)
    assert len(list(loaded)) == len(anchors)
    assert loaded.diagnostics["device_timestamp_nonincreasing"] == 2
    assert loaded.diagnostics["adc_rate_ts_estimated"] == dict(
        hz=8000, sample_steps=2, span_us=250, anchor_pairs=2)
    assert loaded.diagnostics["adc_rate_ts_not_flagged"] == dict(
        hz=8000, sample_steps=1, span_us=125, anchor_pairs=1)


@pytest.mark.parametrize(("payload", "code"), [
    (build_raw([raw_sample(phase=9, slot_idx=1)]), "raw_phase_class"),
    (build_raw([raw_sample(phase=0, slot_idx=1)]), "raw_slot_index"),
    (build_raw([raw_sample(phase=4, slot_idx=255)]), "raw_slot_index"),
    (build_raw([raw_sample(phase=4, slot_idx=20)]), "raw_slot_index"),
    (build_raw([], count=0), "sample_count"),
    (build_raw([raw_sample()] * 101), "sample_count"),
    (build_raw(raw_flags=0), "raw_flags"),
])
def test_rejected_wire_is_not_archived(tmp_path, payload, code):
    rec = recorder(tmp_path)
    rec.start()
    assert not rec.feed_raw(build_envelope(payload))
    assert rec.stop()
    assert list(replay(rec)) == []
    assert sidecar(rec)["raw"]["rejected"] == {code: 1}


class WrappedFile:
    def __init__(self, fh, write):
        self._fh, self._write = fh, write

    def write(self, data):
        return self._write(self._fh, data)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def test_active_metadata_keeps_one_packet_accounting_cut(tmp_path, monkeypatch):
    rec = recorder(tmp_path, json_refresh_s=float("inf"))
    rec.start()
    first_written, second_written = threading.Event(), threading.Event()
    reader_cut, resume_reader = threading.Event(), threading.Event()
    snapshots = []
    lock = rec._input_lock
    write_rows = rec._raw.write_rows

    def signal_after_write(packet):
        write_rows(packet)
        event = first_written if packet.raw.first_sample_sequence == 0 else second_written
        event.set()

    class PauseReaderAfterInputLock:
        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *exc):
            lock.__exit__(*exc)
            if threading.current_thread() is reader and not reader_cut.is_set():
                reader_cut.set()
                assert resume_reader.wait(5)

    reader = threading.Thread(target=lambda: snapshots.append(rec.metadata()), daemon=True)
    monkeypatch.setattr(rec._raw, "write_rows", signal_after_write)
    monkeypatch.setattr(rec, "_input_lock", PauseReaderAfterInputLock())
    try:
        assert rec.feed_raw(wire(0)) and first_written.wait(5)
        reader.start()
        assert reader_cut.wait(5)
        assert rec.feed_raw(wire(1)) and second_written.wait(5)
    finally:
        resume_reader.set()
        if reader.ident is not None:
            reader.join(5)
        stopped = rec.stop()

    assert not reader.is_alive() and stopped
    active, = snapshots
    assert not active["complete"]
    raw = active["raw"]
    assert (raw["observed_before_queue"]["packets"], raw["packets_enqueued"],
            raw["persisted_archive"]["packets"], raw["enqueued_not_confirmed_in_archive"]) == (1, 1, 1, 0)
    closed = sidecar(rec)
    assert closed["complete"]
    assert closed["raw"]["observed_before_queue"]["packets"] == 2
    assert closed["raw"]["packets_enqueued"] == closed["raw"]["persisted_archive"]["packets"] == 2
    assert closed["raw"]["enqueued_not_confirmed_in_archive"] == 0


def test_stuck_raw_writer_keeps_receive_and_stop_bounded_and_separates_loss(tmp_path, monkeypatch):
    entered, release, feed_done = threading.Event(), threading.Event(), threading.Event()
    timeout_saved = threading.Event()
    accepted = []

    def write(fh, data):
        if data != MAGIC and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return fh.write(data)

    def opener(path, mode, **kwargs):
        fh = open(path, mode, **kwargs)
        return WrappedFile(fh, write) if path.name.endswith(".raw.bin") else fh

    rec = recorder(tmp_path, queue_size=1, opener=opener)
    rec.start()
    write_json = rec._write_json

    def signal_timeout_snapshot():
        write_json()
        if rec._close_timed_out:
            timeout_saved.set()

    monkeypatch.setattr(rec, "_write_json", signal_timeout_snapshot)
    assert rec.feed_raw(wire(0)) and entered.wait(2)

    def receive_while_stuck():
        accepted.extend(rec.feed_raw(wire(n)) for n in (1, 2, 4))
        feed_done.set()

    feeder = threading.Thread(target=receive_while_stuck)
    feeder.start()
    try:
        assert feed_done.wait(1) and accepted == [True, False, False]
        active = rec.metadata()
        assert not active["complete"] and not active["close_timed_out"]
        assert_raw_balance(active, enqueued=2, persisted=0, dropped=2, rows=0)
        t0 = time.monotonic()
        assert not rec.stop(.25)
        assert time.monotonic() - t0 < 1
        assert timeout_saved.wait(5)
        meta = sidecar(rec)
        assert not meta["complete"] and meta["close_timed_out"]
        assert_raw_balance(meta, enqueued=2, persisted=0, dropped=2, rows=0)
        assert meta["raw"]["observed_before_queue"]["forward_gap_samples"] == 1
    finally:
        release.set()
        feeder.join(5)
        rec._thread.join(5)
    assert not feeder.is_alive() and not rec._thread.is_alive()
    recovered = sidecar(rec)
    assert not recovered["complete"] and recovered["close_timed_out"]
    assert_raw_balance(recovered, enqueued=2, persisted=2, dropped=2, rows=2)
    loaded = replay(rec)
    assert [packet.raw.first_sample_sequence for packet in loaded] == [0, 1]
    assert loaded.diagnostics["forward_gap_samples"] == 0
    assert "incomplete" in loaded.meta_problem and loaded.archive_problem is None
    assert not rec.stop()


def test_sidecar_serialization_does_not_hold_input_lock(tmp_path, monkeypatch):
    rec = recorder(tmp_path, json_refresh_s=0)
    rec.start()
    entered, release, feed_done = threading.Event(), threading.Event(), threading.Event()
    accepted, published = [], []
    dump = json.dump

    def stall_first_packet_snapshot(meta, *args, **kwargs):
        if meta["raw"]["persisted_archive"]["packets"] == 1 and not entered.is_set():
            published.append(meta)
            entered.set()
            assert release.wait(10)
        return dump(meta, *args, **kwargs)

    def feed_during_serialization():
        accepted.append(rec.feed_raw(wire(1)))
        feed_done.set()

    feeder = threading.Thread(target=feed_during_serialization, daemon=True)
    monkeypatch.setattr(json, "dump", stall_first_packet_snapshot)
    try:
        assert rec.feed_raw(wire(0)) and entered.wait(5)
        feeder.start()
        assert feed_done.wait(5) and accepted == [True]
        assert_raw_balance(published[0], enqueued=1, persisted=1, dropped=0, rows=1)
        assert_raw_balance(rec.metadata(), enqueued=2, persisted=1, dropped=0, rows=1)
    finally:
        release.set()
        if feeder.ident is not None:
            feeder.join(5)
        stopped = rec.stop()
    assert not feeder.is_alive() and stopped
    closed = sidecar(rec)
    assert closed["complete"]
    assert_raw_balance(closed, enqueued=2, persisted=2, dropped=0, rows=2)


def test_replay_gap_can_be_caused_by_queue_drop_not_stream_gap(tmp_path):
    entered, release, second_writing = threading.Event(), threading.Event(), threading.Event()
    writes = 0

    def write(fh, data):
        nonlocal writes
        if data != MAGIC:
            writes += 1
            if writes == 1:
                entered.set()
                assert release.wait(5)
            elif writes == 2:
                second_writing.set()  # second item dequeued; next queue slot is free
        return fh.write(data)

    def opener(path, mode, **kwargs):
        fh = open(path, mode, **kwargs)
        return WrappedFile(fh, write) if path.name.endswith(".raw.bin") else fh

    rec = recorder(tmp_path, queue_size=1, opener=opener)
    rec.start()
    try:
        assert rec.feed_raw(wire(0)) and entered.wait(2)
        assert rec.feed_raw(wire(1)) and not rec.feed_raw(wire(2))
        release.set()
        assert second_writing.wait(2) and rec.feed_raw(wire(3))
    finally:
        release.set()
        stopped = rec.stop()
    assert stopped
    loaded = replay(rec)
    assert [packet.raw.first_sample_sequence for packet in loaded] == [0, 1, 3]
    assert loaded.diagnostics["forward_gap_samples"] == 1
    meta = sidecar(rec)
    assert meta["complete"] and meta["raw"]["observed_before_queue"]["forward_gap_samples"] == 0
    assert_raw_balance(meta, enqueued=3, persisted=3, dropped=1, rows=3)


@pytest.mark.parametrize("suffix", [".raw.bin", ".raw.csv"])
def test_short_raw_write_is_visible_and_salvages_complete_wire(tmp_path, suffix):
    def write(fh, data):
        return fh.write(data[:7] if armed else data)

    def opener(path, mode, **kwargs):
        fh = open(path, mode, **kwargs)
        return WrappedFile(fh, write) if path.name.endswith(suffix) else fh

    armed = False
    rec = recorder(tmp_path, opener=opener)
    rec.start()
    armed = True
    assert rec.feed_raw(wire())
    assert not rec.stop()
    loaded = replay(rec)
    assert [packet.datagram() for packet in loaded] == ([] if suffix == ".raw.bin" else [wire()])
    assert bool(loaded.archive_problem) == (suffix == ".raw.bin")
    meta = sidecar(rec)
    assert "short RAW" in meta["writer_error"] and not meta["complete"]
    assert_raw_balance(meta, enqueued=1, persisted=int(suffix == ".raw.csv"), dropped=0, rows=0)


@pytest.mark.parametrize("damage", ["header", "body", "crc", "boundary"])
def test_replay_salvages_prefix_and_checks_archive_integrity(tmp_path, damage):
    rec = recorder(tmp_path)
    rec.start()
    first, second = wire(1), wire(2, 100)
    assert rec.feed_raw(first, 101) and rec.feed_raw(second, 102)
    assert rec.stop()
    archive = tmp_path / "capture.raw.bin"
    data = archive.read_bytes()
    record_size = len(data) - len(MAGIC) - len(first) - len(second)
    last_offset = len(MAGIC) + len(first) + record_size // 2
    damaged = {"header": data[:last_offset + 3], "body": data[:-1],
               "crc": data[:-1] + bytes((data[-1] ^ 0x80,)), "boundary": data[:last_offset]}
    archive.write_bytes(damaged[damage])
    loaded = replay(rec)
    assert [packet.datagram() for packet in loaded] == [first]
    expected = {"header": "truncated record header", "body": "truncated record body",
                "crc": "record CRC mismatch", "boundary": "archive size/SHA-256"}
    assert expected[damage] in loaded.archive_problem


@pytest.mark.parametrize("kind", ["missing", "invalid", "oversize"])
def test_replay_keeps_valid_records_without_usable_metadata(tmp_path, kind):
    rec = recorder(tmp_path)
    rec.start()
    assert rec.feed_raw(wire()) and rec.stop()
    if kind == "missing":
        rec.json_path.unlink()
    elif kind == "invalid":
        rec.json_path.write_text("[", encoding="utf-8")
    else:
        with rec.json_path.open("r+b") as fh:
            fh.truncate(8 * 1024 * 1024 + 1)
    loaded = replay(rec)
    assert [packet.datagram() for packet in loaded] == [wire()]
    assert loaded.meta is None and loaded.meta_problem and loaded.archive_problem is None


@pytest.mark.parametrize("suffix", [".raw.bin", ".raw.csv"])
def test_exclusive_raw_creation_preserves_competing_files(tmp_path, suffix):
    def opener(path, mode, **kwargs):
        if path.name.endswith(suffix):
            path.write_bytes(b"competing file")
        return open(path, mode, **kwargs)

    rec = recorder(tmp_path, opener=opener)
    with pytest.raises(FileExistsError):
        rec.start()
    assert (tmp_path / f"capture{suffix}").read_bytes() == b"competing file"
    assert not sidecar(rec)["complete"] and "FileExistsError" in sidecar(rec)["writer_error"]


def test_raw_process_exit_without_finalize_recovers_flushed_archive(tmp_path):
    code = """
import os, sys, time
from pathlib import Path
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.writer import Recorder
r = Recorder(sys.argv[1], load_geometry(sys.argv[2]), name='capture', record_raw=True)
r.start()
assert r.feed_raw(bytes.fromhex(sys.argv[3]), 25, 55)
until = time.monotonic() + 5
while r.metadata()['raw']['persisted_archive']['packets'] != 1:
    assert time.monotonic() < until
    time.sleep(.01)
os._exit(0)
"""
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (src, os.environ.get("PYTHONPATH"))))}
    subprocess.run([sys.executable, "-c", code, str(tmp_path), str(GEOMETRY_PATH), wire().hex()],
                   check=True, timeout=10, env=env)
    loaded = RawReplay(tmp_path / "capture.raw.bin")
    packet, = loaded
    assert packet.datagram() == wire() and (packet.host_rx_us, packet.observed_boot_id) == (25, 55)
    assert "incomplete" in loaded.meta_problem


def test_raw_provenance_unknowns_and_sha_source(tmp_path):
    with pytest.raises(ValueError, match="operator-provided"):
        recorder(tmp_path, firmware_sha="b" * 40, firmware_sha_source="device")
    rec = recorder(tmp_path, host_sha="a" * 40, firmware_sha="b" * 40)
    start = {"config": {"mode": "ENGINEERING", "fs_hz": 5, "led_current_ma": [30]}}
    rec.start(raw_context=start)
    start["config"]["led_current_ma"][0] = 0
    assert rec.feed_raw(wire(flags=0), 12)
    assert rec.stop()
    meta = sidecar(rec)
    assert meta["host_sha"] == "a" * 40 and meta["host_sha_source"] == "caller_supplied"
    assert meta["firmware"] == dict(declared_sha="b" * 40, sha_source="operator_provided",
                                    boot_id=None, observed_info=None)
    assert meta["evidence_class"] == "unknown" and meta["raw"]["context_at_end"] is None
    assert meta["raw"]["context_at_start"]["config"]["led_current_ma"] == [30]
