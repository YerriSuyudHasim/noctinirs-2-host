"""Stage D Qt acceptance. All peers are local fixtures or UDP loopback."""

import os
from pathlib import Path
from threading import Event
import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets

from noctinirs_host.control import protocol as p
from noctinirs_host.control.session import SessionController
from noctinirs_host.engineering import EngineeringRun
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import RawReplay
from noctinirs_host.record.writer import Recorder
from noctinirs_host_ui.engineering import EngineeringWindow
from test_engineering_run import GEOMETRY
from test_udp_session import Peer


def wait_for(condition, seconds=6):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "Qt state did not converge"
        QtTest.QTest.qWait(15)


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(qapp):
    windows = []

    def open_window(**kwargs):
        w = EngineeringWindow(load_geometry(GEOMETRY), **kwargs)
        windows.append(w)
        w.show()
        return w

    yield open_window
    for w in windows:
        if w.worker.isRunning():
            w.worker.submit("shutdown")
        wait_for(lambda: not w.worker.isRunning(), seconds=12)
        wait_for(lambda: w._worker_finished)
        w.close()


def screenshot(w, filename):
    evidence = os.environ.get("NN2_D_SCREENSHOTS")
    if evidence:
        assert w.grab().save(str(Path(evidence) / filename), "PNG")


def finish_setup(w, tmp_path, stem):
    wait_for(lambda: w.state.get("connected") and w.fs.findData(5) > 0)
    w.next.click()
    w.fs.setCurrentIndex(w.fs.findData(5))
    w.apply.click()
    wait_for(lambda: w.state.get("configured") and w.next.isEnabled())
    w.next.click()
    w.folder.setText(str(tmp_path))
    w.stem.setText(stem)
    w.firmware.setText("a" * 40)
    w.next.click()
    wait_for(lambda: w.state.get("checked_target") and w.next.isEnabled())
    assert "Ready to open acquisition panel" in w.summary.text()


def test_wizard_navigation_recording_and_periodic_heartbeat(window, tmp_path):
    w = window()
    banner = w.findChild(QtWidgets.QLabel, "banner")
    assert "ENGINEERING / NON-SCIENCE" in banner.text()
    assert any("not applied to RAW ENGINEERING" in label.text() for label in
               w.findChildren(QtWidgets.QLabel))
    w.next.click()
    w.next.click()
    assert "Connect to a peer" in w.error.text()
    w.source.setCurrentIndex(1)
    w.address.setText("127.0.0.1")
    w.port.setText("invalid")
    w.connect_button.click()
    wait_for(lambda: "UDP port" in w.error.text())
    w.source.setCurrentIndex(0)
    w.connect_button.click()
    finish_setup(w, tmp_path, "fake")
    w.back.click()
    assert w.stem.text() == "fake" and w.firmware.text() == "a" * 40
    w.back.click()
    assert w.fs.currentData() == 5
    w.back.click()
    assert w.address.text() == "127.0.0.1" and w.port.text() == "invalid"
    w.next.click()
    w.next.click()
    w.stem.setText("../invalid")
    w.next.click()
    assert "recording stem" in w.error.text()
    w.stem.setText("fake")
    w.next.click()
    wait_for(lambda: w.state.get("checked_target") and w.next.isEnabled())
    screenshot(w, "stage-d-readiness-fake.png")
    w.next.click()
    QtTest.QTest.qWait(250)
    assert w.state["device_state"] == "STOPPED" and not w.state["session"]
    w.arm_button.click()
    wait_for(lambda: w.state["recording"] == "ARMED")
    assert w.state["device_state"] == "STOPPED"
    w.start_button.click()
    wait_for(lambda: w.state["raw"]["packets"] > 0)
    sent = w.state["host_counters"]["sent"]
    wait_for(lambda: w.state["host_counters"]["sent"] > sent)
    assert "SIMULATED" in w.raw.text() and "Recorder  RECORDING" in w.recording.text()
    screenshot(w, "stage-d-acquisition-fake.png")
    w.stop_button.click()
    wait_for(lambda: w.finalize_button.isEnabled())
    w.finalize_button.click()
    wait_for(lambda: w.state["recording"] == "FINALIZED")
    assert list(RawReplay(tmp_path / "fake.raw.bin"))
    w.panel_disconnect.click()
    wait_for(lambda: not w.state["connected"])
    assert "reported STOPPED" in w.feedback.text()


def test_udp_worker_timeout_keeps_gui_responsive_and_recovery_explicit(window, tmp_path, monkeypatch):
    peer = Peer()
    entered, release = Event(), Event()
    peer.capability += ";frames=RAW_ENGINEERING"

    def hold_start(_peer, req, _wire, reply, _source):
        if req.opcode == p.OP_START:
            entered.set()
            release.wait(3)
            return []
        return [reply]

    peer.filter = hold_start
    w = window(run_factory=lambda *args: EngineeringRun(*args, controller_factory=lambda tr:
                                                       SessionController(tr, timeout_s=.2)))
    try:
        w.next.click()
        w.source.setCurrentIndex(1)
        w.address.setText(peer.endpoint[0])
        w.port.setText(str(peer.endpoint[1]))
        w.connect_button.click()
        finish_setup(w, tmp_path, "timeout")
        w.next.click()
        assert peer.executed_starts == 0
        w.start_button.click()
        wait_for(entered.is_set)
        pulses = []
        QtCore.QTimer.singleShot(0, lambda: pulses.append(True))
        wait_for(lambda: bool(pulses))
        release.set()
        wait_for(lambda: w.state.get("start_uncertain"))
        assert "START OUTCOME UNRESOLVED" in w.device.text()
        assert "Physical LED" in w.device.text()
        assert not w.start_button.isEnabled() and not w.stop_button.isEnabled()
        _, safe_button = w._recovery_buttons[-1]
        warning = []

        def confirm(_parent, _title, text, *_args):
            warning.append(text)
            return QtWidgets.QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QtWidgets.QMessageBox, "question", confirm)
        safe_button.click()
        wait_for(lambda: not w.state["start_uncertain"])
        assert "another host" in warning[0] and "reported SAFE_OFF" in w.feedback.text()
        assert peer.executed_starts == 1
        w.close()
        wait_for(lambda: w._worker_finished)
        assert not w.worker.isRunning() and not w.state["connected"]
    finally:
        release.set()
        peer.close()


def test_udp_async_states_and_writer_open_error_are_distinct(window, tmp_path):
    peer = Peer()
    peer.capability += ";frames=RAW_ENGINEERING"

    class BrokenRecorder(Recorder):
        def start(self, **_kwargs):
            raise OSError("injected recorder open failure")

    w = window(run_factory=lambda *args: EngineeringRun(*args, recorder_factory=BrokenRecorder))
    try:
        w.next.click()
        w.source.setCurrentIndex(1)
        w.address.setText(peer.endpoint[0])
        w.port.setText(str(peer.endpoint[1]))
        w.connect_button.click()
        finish_setup(w, tmp_path, "writer-error")
        w.next.click()
        w.arm_button.click()
        wait_for(lambda: "injected recorder open failure" in w.error.text())
        assert "STOPPED" in w.device.text() and "INCOMPLETE / ERROR" in w.recording.text()
        w.back.click()
        w.back.click()
        w.next.click()  # No file was created, so the same destination can be rechecked.
        wait_for(lambda: w.state.get("checked_target") and w.next.isEnabled())
        w.next.click()
        w.start_button.click()
        wait_for(lambda: "STARTING" in w.device.text())
        assert not w.start_button.isEnabled() and w.stop_button.isEnabled()
        peer.complete_start()
        wait_for(lambda: "STREAMING" in w.device.text())
        w.stop_button.click()
        wait_for(lambda: "STOPPING" in w.device.text())
        assert not w.stop_button.isEnabled() and not w.finalize_button.isEnabled()
        peer.complete_stop()
        wait_for(lambda: "STOPPED" in w.device.text())
        w.panel_disconnect.click()
        wait_for(lambda: not w.state["connected"])
    finally:
        peer.complete_stop()
        peer.close()
