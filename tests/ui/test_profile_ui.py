"""The LED calibration profile as the firmware reports it is shown in the Live status area (read-only)."""

import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets

from noctinirs_host.engineering import EngineeringRun
from noctinirs_host.geometry import load_geometry
from noctinirs_host_ui.engineering import EngineeringWindow
from test_engineering_run import GEOMETRY


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "Qt state did not converge"
        QtTest.QTest.qWait(15)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    return app


def open_window(tmp_path, **fake):
    settings = QtCore.QSettings(str(tmp_path / "ui.ini"), QtCore.QSettings.Format.IniFormat)
    return EngineeringWindow(load_geometry(GEOMETRY), documents=tmp_path / "Documents", settings=settings,
                             run_factory=lambda *a: EngineeringRun(*a, fake_options=fake))


def close(w):
    worker = w.worker
    if worker.isRunning():
        worker.submit("shutdown")
    wait_for(lambda: not worker.isRunning(), seconds=12)
    try:
        w.close()
    except RuntimeError:
        pass


@pytest.mark.parametrize("fake,expected", [
    ({}, "not available"),                                                 # firmware without get-profile
    ({"profile_model": True}, "state NONE · source NONE"),                 # FIX1 firmware, nothing started yet
])
def test_the_status_area_shows_what_the_firmware_reports_about_the_profile(qapp, tmp_path, monkeypatch, fake,
                                                                           expected):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    w = open_window(tmp_path, **fake)
    w.show()
    try:
        w.next.click()
        w.connect_button.click()
        wait_for(lambda: w.state.get("connected") and expected in w.profile_line.toPlainText())
        w._go(5)
        assert expected in w.profile_line.toPlainText() and w.profile_line.fully_visible()
        assert "Physical LED current is not measured" in w.profile_line.toPlainText() or "not available" in expected
    finally:
        close(w)
