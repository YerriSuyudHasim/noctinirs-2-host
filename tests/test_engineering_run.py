"""Stage D workflow without Qt. UDP tests use only 127.0.0.1."""

import json
from pathlib import Path
from threading import Event
import time

import pytest

from calibration_fixture import CalibrationPeer, CalibrationReadyRun, seed_calibration
from helpers import build_envelope, build_raw
from noctinirs_host.control import protocol as p
from noctinirs_host.control.session import SessionController
from noctinirs_host.engineering import EngineeringRun, RecordingTarget
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import RawReplay
from noctinirs_host.record.writer import Recorder
from test_udp_session import Clock

GEOMETRY = Path(__file__).resolve().parents[1] / "config" / "optical_geometry_v1.json"


def poll(run, condition, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run.tick()
        if condition():
            return
        time.sleep(.005)  # Only for OS scheduling of the disk job or UDP peer.
    pytest.fail(f"Condition not met: {run.snapshot()}")


@pytest.fixture
def setup():
    peer, clock = CalibrationPeer(), Clock()
    peer.capability += ";frames=RAW_ENGINEERING;raw_packets_sent=0;raw_dropped_samples=0"
    runs = []

    def make(**kwargs):
        run = CalibrationReadyRun(load_geometry(GEOMETRY), "b" * 40, **kwargs,
                             controller_factory=lambda transport: SessionController(
                                 transport, clock, retries=1, timeout_s=.05))
        runs.append(run)
        run.connect(False, peer.endpoint[0], str(peer.endpoint[1]))
        return run

    try:
        yield peer, clock, make
    finally:
        try:
            peer.complete_stop()
            clock.now += 1
            for run in runs:
                run.shutdown()
                poll(run, lambda: run.finished, timeout=8)
        finally:
            peer.close()


def arm(run, tmp_path):
    target = RecordingTarget.parse(str(tmp_path), "run", "a" * 40)
    run.check_destination(target)
    poll(run, lambda: not run.snapshot()["disk_busy"])
    assert run.checked_target == target
    run.arm(target)
    return target


def test_udp_workflow_archives_wire_and_provenance(setup, tmp_path):
    peer, clock, make = setup
    run = make()
    with pytest.raises(ValueError, match="advertised"):
        run.configure(3)
    assert peer.mode == "ACQ" and peer.fs == 5
    run.configure(8)
    arm(run, tmp_path)
    poll(run, lambda: run.recorder_state == "ARMED")
    assert peer.executed_starts == 0
    run.start()
    assert run.snapshot()["device_state"] == "STARTING"
    peer.complete_start()
    clock.now += .5
    run.tick()
    assert run.snapshot()["device_state"] == "STREAMING"
    assert peer.requests[-1][1].opcode == p.OP_HEARTBEAT
    run.tick()  # verify APPLIED on the same owner session
    expected = [
        build_envelope(build_raw(first=10), session_id=peer.session, flags=2, ts=250),
        build_envelope(build_raw(first=13), session_id=peer.session, flags=2, ts=400),
    ]
    for datagram in expected:
        peer.sock.sendto(datagram, peer.owner)
    poll(run, lambda: run.snapshot()["raw"]["packets"] == 2)
    snap = run.snapshot()
    assert snap["raw"]["forward_gap_samples"] == 2
    assert snap["latest"]["first_device_us"] == 400 and snap["latest"]["host_us"] > 400
    run.stop()
    assert run.snapshot()["device_state"] == "STOPPING"
    with pytest.raises(ValueError, match="STOPPED"):
        run.finalize()
    peer.complete_stop()
    clock.now += .5
    run.tick()
    assert run.stopped and "reported STOPPED" in run.notice
    peer.capability = peer.capability.replace("sent=0", "sent=2")
    run.finalize()
    poll(run, lambda: run.recorder_state == "FINALIZED")
    replay = RawReplay(tmp_path / "run.raw.bin")
    assert [packet.datagram() for packet in replay] == expected
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["complete"] and meta["host_sha"] == "b" * 40
    assert meta["firmware"]["declared_sha"] == "a" * 40
    assert meta["firmware"]["sha_source"] == "operator_provided"
    begin, end = (meta["raw"][f"context_at_{key}"] for key in ("start", "end"))
    assert begin["source_endpoint"] == list(peer.endpoint)
    assert begin["observed_info"]["source"] == "INFO"
    assert begin["config"]["fs_hz"] == 8 and begin["config"]["mode"] == "ENGINEERING"
    assert end["firmware_raw_diagnostics"]["raw_packets_sent"] == 2
    assert end["firmware_status"]["state"] == "STOPPED"
    assert run.snapshot()["recording_meta"]["queue_drops"] == 0


def test_destination_conflict_created_after_check_is_preserved(setup, tmp_path):
    _, _, make = setup
    run = make()
    run.configure(5)
    target = RecordingTarget.parse(str(tmp_path), "run", "")
    run.check_destination(target)
    poll(run, lambda: run.checked_target == target)
    path = tmp_path / "run.raw.bin"
    path.write_bytes(b"existing")
    run.arm(target)
    poll(run, lambda: run.recorder_state == "INCOMPLETE / ERROR")
    assert "FileExistsError" in run.error and path.read_bytes() == b"existing"
    assert sorted(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("stem", ["", "../escape", "x ", "NUL", "COM1.log", "trailing."])
def test_invalid_target_is_rejected(stem, tmp_path):
    with pytest.raises(ValueError, match="stem"):
        RecordingTarget.parse(str(tmp_path), stem, "")


def test_disk_open_can_block_while_session_tick_and_disconnect_progress(setup, tmp_path):
    peer, clock, make = setup
    opening, release = Event(), Event()

    class SlowOpen(Recorder):
        def start(self, **kwargs):
            opening.set()
            assert release.wait(6)
            super().start(**kwargs)

    run = make(recorder_factory=SlowOpen)
    run.configure(5)
    arm(run, tmp_path)
    try:
        assert opening.wait(2)
        before = len(peer.requests)
        clock.now += 2.1
        run.tick()
        assert peer.requests[before][1].opcode == p.OP_STATUS
        assert run.recorder_state == "OPENING"
        with pytest.raises(ValueError, match="recorder"):
            run.start()
        run.disconnect()
        run.tick()
        assert not run.connected and run.snapshot()["closing"]
        assert "reported STOPPED" in run.cleanup
    finally:
        release.set()
    poll(run, lambda: not run.snapshot()["closing"])
    assert run.recorder_state == "FINALIZED" and peer.executed_starts == 0


def test_writer_error_keeps_acquisition_control_and_reports_incomplete(setup, tmp_path):
    peer, clock, make = setup
    writing, release = Event(), Event()

    class FailingWriter(Recorder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, opener=self.open_file, **kwargs)

        def open_file(self, path, *args, **kwargs):
            stream = open(path, *args, **kwargs)
            if not path.name.endswith(".raw.bin"):
                return stream
            write = stream.write

            def fail_after_header(data):
                if stream.tell() == 0:
                    return write(data)
                writing.set()
                assert release.wait(6)
                raise OSError("injected disk write failure")

            stream.write = fail_after_header
            return stream

    run = make(recorder_factory=FailingWriter)
    run.configure(5)
    arm(run, tmp_path)
    poll(run, lambda: run.recorder_state == "ARMED")
    run.start()
    peer.complete_start()
    clock.now += .5
    run.tick()
    run.tick()
    peer.sock.sendto(build_envelope(build_raw(), session_id=peer.session), peer.owner)
    try:
        poll(run, writing.is_set)
        clock.now += .5
        run.tick()
        assert run.recorder_state == "RECORDING"
        assert peer.requests[-1][1].opcode == p.OP_HEARTBEAT
    finally:
        release.set()
    poll(run, lambda: run.recorder_state == "INCOMPLETE / ERROR")
    assert "injected disk write failure" in run.error
    clock.now += .5
    run.tick()
    assert run.snapshot()["can_stop"] and peer.requests[-1][1].opcode == p.OP_HEARTBEAT
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert not meta["complete"] and meta["raw"]["persisted_archive"]["packets"] == 0
    run.stop()
    peer.complete_stop()
    clock.now += .5
    run.tick()
    assert run.stopped


def test_lost_start_is_visible_and_finalizes_without_unknown_raw(setup, tmp_path):
    peer, clock, make = setup
    run = make()
    run.configure(5)
    arm(run, tmp_path)
    poll(run, lambda: run.recorder_state == "ARMED")
    peer.filter = lambda _p, req, _wire, ack, _src: [] if req.opcode == p.OP_START else [ack]
    run.start()
    peer.sock.sendto(build_envelope(build_raw(), session_id=peer.session), peer.owner)
    before = len(peer.requests)
    clock.now += 2
    poll(run, lambda: run.recorder_state == "FINALIZED")
    assert run.snapshot()["start_uncertain"] and not run.stopped
    assert "START outcome unresolved" in run.error and peer.requests[before:] == []
    assert "run calibration again" in run.error and run.calibration is None
    assert list(RawReplay(tmp_path / "run.raw.bin")) == []
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    end = meta["raw"]["context_at_end"]
    assert end["start_outcome"] == "unresolved" and end["device_state"] is None
    run.disconnect()
    run.tick()
    assert "STOP is unconfirmed" in run.cleanup
    with pytest.raises(ValueError, match="original endpoint"):
        run.connect(True, "", "")
    run.safe_off()
    assert "reported SAFE_OFF" in run.notice and not run.snapshot()["start_uncertain"]
    assert not run.connected and peer.executed_starts == 1


@pytest.mark.parametrize("mode", ["reboot", "silence"])
def test_active_link_failure_is_visible_and_reconnect_never_starts(setup, mode):
    peer, clock, make = setup
    run = make()
    run.configure(5)
    run.start()
    if mode == "reboot":
        peer.complete_stop()
        peer.boot += 1
    else:
        peer.filter = lambda *_: []
    clock.now += .5
    run.tick()
    assert not run.connected and "STOP is unconfirmed" in run.cleanup
    peer.filter = lambda _p, _r, _w, ack, _s: [ack]
    peer.complete_stop()
    run.connect(False, peer.endpoint[0], str(peer.endpoint[1]))
    assert run.connected and not run.configured and peer.executed_starts == 1


def test_builtin_fake_is_simulated_and_produces_raw():
    run = EngineeringRun(load_geometry(GEOMETRY))
    try:
        run.connect(True, "", "")
        run.configure(10)
        seed_calibration(run)
        run.start()
        poll(run, lambda: run.snapshot()["raw"]["packets"] > 0)
        assert run.snapshot()["simulated"] and run.snapshot()["latest"]["simulated"]
        assert run.snapshot()["latest"]["estimated"]
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)
    assert "reported STOPPED" in run.cleanup
