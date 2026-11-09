"""Stage D2 Qt acceptance. All peers are local fixtures or UDP loopback (simulation only)."""

import json
import math
import os
from pathlib import Path
from threading import Event
import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets

from noctinirs_host.control import protocol as p
from noctinirs_host.control.session import SessionController
from noctinirs_host.engineering import EngineeringRun, default_recording_folder
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import RawReplay
from noctinirs_host.record.writer import Recorder
from noctinirs_host_ui.engineering import EngineeringWindow
from test_engineering_run import GEOMETRY
from test_udp_session import Peer


def wait_for(condition, seconds=8):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "Qt state did not converge"
        QtTest.QTest.qWait(15)


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    windows = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)

    def open_window(**kwargs):
        kwargs.setdefault("documents", tmp_path / "Documents")
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


def fake_window(window, **fake_options):
    return window(run_factory=lambda *a: EngineeringRun(*a, fake_options=fake_options))


def screenshot(w, filename):
    evidence = os.environ.get("NN2_D_SCREENSHOTS")
    if evidence:
        assert w.grab().save(str(Path(evidence) / filename), "PNG")


def to_device(w):
    w.next.click()
    assert w.flat == 1
    w.connect_button.click()
    wait_for(lambda: w.state.get("connected") and w.fs.findData(10) > 0)


def finish_device(w, rate=10):
    w.fs.setCurrentIndex(w.fs.findData(rate))
    w.apply.click()
    wait_for(lambda: w.state.get("configured") and w.next.isEnabled())


def to_session(w, rate=10, baseline_s=1.0):
    to_device(w)
    finish_device(w, rate)
    w.next.click()          # -> MBLL
    w.mbll_form.baseline.setValue(baseline_s)
    w.next.click()          # -> short separation
    w.next.click()          # -> session
    assert w.flat == 4


def to_live(w, rate=10, baseline_s=1.0):
    to_session(w, rate, baseline_s)
    wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
    w.next.click()
    assert w.flat == 5


def press_start(w):
    wait_for(lambda: w.start_button.isEnabled())
    w.start_button.click()


def record_and_stop(w, packets=2):
    press_start(w)
    wait_for(lambda: w.state["recording"] == "RECORDING")
    wait_for(lambda: w.state["raw"]["packets"] >= packets)
    w.stop_button.click()
    wait_for(lambda: w.state["recording"] == "FINALIZED", seconds=15)


def test_three_screens_keep_input_and_live_does_not_start(window):
    w = window()
    assert w.screens.count() == 3 and w.setup_steps.count() == 4
    banner = w.findChild(QtWidgets.QLabel, "banner")
    assert "ENGINEERING / NON-SCIENCE" in banner.text() and "PREVIEW" in banner.text()
    assert w.flat == 0 and w.screens.currentIndex() == 0
    w.next.click()
    assert w.screens.currentIndex() == 1 and w.setup_steps.currentIndex() == 0
    w.next.click()
    assert "Connect to a peer" in w.error.text()
    w.source.setCurrentIndex(1)
    w.address.setText("127.0.0.1")
    w.port.setText("invalid")
    w.connect_button.click()
    wait_for(lambda: "UDP port" in w.error.text())
    w.source.setCurrentIndex(0)
    w.connect_button.click()
    wait_for(lambda: w.state.get("connected") and w.fs.findData(5) > 0)
    finish_device(w, 5)
    w.next.click()
    assert w.setup_steps.currentIndex() == 1
    w.mbll_form.baseline.setValue(7.0)
    w.next.click()
    w.ssr_form.ssr_mode.setCurrentIndex(w.ssr_form.ssr_mode.findData("8mm"))
    w.next.click()
    assert w.setup_steps.currentIndex() == 3
    w.firmware.setText("a" * 40)
    w.back.click()
    w.back.click()
    assert w.mbll_form.baseline.value() == 7.0          # Back keeps input
    w.back.click()
    assert w.fs.currentData() == 5 and w.address.text() == "127.0.0.1" and w.port.text() == "invalid"
    w.next.click()
    w.next.click()
    w.next.click()
    assert w.ssr_form.ssr_mode.currentData() == "8mm" and w.firmware.text() == "a" * 40
    wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
    screenshot(w, "d2-setup-session.png")
    w.next.click()
    assert w.screens.currentIndex() == 2
    QtTest.QTest.qWait(400)
    assert w.state["device_state"] == "STOPPED" and not w.state["session"]
    assert w.state["raw"]["packets"] == 0 and w.start_button.isEnabled()
    assert w.preview_cfg.baseline_s == 7.0 and w.preview_cfg.ssr_mode == "8mm"


def test_default_folder_record_numbering_and_no_overwrite(window, tmp_path):
    w = fake_window(window)
    day = default_recording_folder(tmp_path / "Documents")
    assert w.folder_text() == str(day) and not day.exists()
    day.mkdir(parents=True)
    (day / "record_2.raw.bin").write_bytes(b"existing")
    (day / "record_2.json").write_text("{}")
    to_live(w)
    record_and_stop(w)
    assert (day / "record_3.raw.bin").exists() and (day / "record_3.json").exists()
    assert (day / "record_2.raw.bin").read_bytes() == b"existing"
    record_and_stop(w)
    assert (day / "record_4.raw.bin").exists()
    assert list(RawReplay(day / "record_4.raw.bin"))
    assert not (day / "record_1.raw.bin").exists()


def test_first_start_of_empty_day_is_record_1_and_folder_is_created(window, tmp_path):
    w = fake_window(window)
    to_live(w)
    day = default_recording_folder(tmp_path / "Documents")
    assert not day.exists()
    record_and_stop(w)
    assert (day / "record_1.raw.bin").exists()
    provenance = json.loads((day / "record_1.preview.json").read_text())
    assert provenance["raw_file_unchanged"] and "PREVIEW" in provenance["label"]
    sidecar = json.loads((day / "record_1.json").read_text())
    preview = sidecar["settings_at_start"]["preview"]
    assert preview["extinction"]["status"] == "unverified" and preview["dpf"]["values"]["735"] == 6.0


def test_browse_changes_folder(window, tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(chosen))
    w = fake_window(window)
    to_session(w)
    w.browse_button.click()
    assert w.folder_text() == str(chosen)
    wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
    w.next.click()
    record_and_stop(w)
    assert (chosen / "record_1.raw.bin").exists()


def test_long_text_is_not_clipped(window, tmp_path):
    long_token = "baseline=V4_IDF61_NN2_CTRL;" + "x" * 300
    deep = tmp_path / "Documents"
    for i in range(4):
        deep = deep / ("long-directory-name-" + str(i) * 30)
    w = fake_window(window, info_extra=";" + long_token)
    w.resize(900, 800)
    to_device(w)
    wait_for(lambda: "x" * 300 in w.info_view.toPlainText())
    assert "baseline=V4_IDF61_NN2_CTRL" in w.info_view.toPlainText()
    assert "frames=RAW_ENGINEERING" in w.info_view.toPlainText().splitlines()
    assert w.info_view.fully_visible()
    assert w.info_view.horizontalScrollBar().maximum() == 0
    w.folder.setPlainText(str(deep))
    assert w.folder.fully_visible() and w.folder_text() == str(deep)
    screenshot(w, "d2-device-long-info.png")


def test_sixteen_plots_fill_and_modes_switch(window):
    w = fake_window(window)
    to_live(w)
    assert len(w.panel.plots) == 16 and w.panel.shorts == ["SC1L", "SC2L", "SC1R", "SC2R"]
    press_start(w)
    wait_for(lambda: w.state.get("session"))
    wait_for(lambda: w.panel.finite_channels() == 16, seconds=15)
    w.panel.refresh()
    for key in ("Hb", "OD", "I"):
        w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData(key))
        w.panel.refresh()
        assert all(w.panel.finite_points(n) > 0 for n in w.panel.longs), key
    w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData("Hb"))
    w.panel.select("SC1L")
    labels = dict(w.panel.detail_rows())
    assert {"SC1L_I735", "SC1L_I850", "SC1L_OD735", "SC1L_OD850", "ΔHbO (µM)", "ΔHbR (µM)",
            "ΔHbT (µM)", "Short separation"} <= set(labels)
    w.panel.select("CH1")
    assert "PREVIEW" in w.panel.banner.text()
    screenshot(w, "d2-live-hb.png")
    w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData("OD"))
    w.panel.refresh()
    screenshot(w, "d2-live-od.png")
    w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData("I"))
    w.panel.refresh()
    screenshot(w, "d2-live-intensity.png")
    w.stop_button.click()
    wait_for(lambda: w.state["device_state"] == "STOPPED" and w.state["recording"] == "FINALIZED", seconds=15)


def test_invalid_channels_are_gaps_not_zero(window):
    # Slots 4,5 -> source L3 (CH3, CH4) never step. (6,3),(7,3) saturated -> CH5 (L4, AIN3).
    w = fake_window(window, dead_slots=frozenset({4, 5}), saturate=frozenset({(6, 3), (7, 3)}))
    to_live(w)
    press_start(w)
    wait_for(lambda: w.state.get("session"))
    wait_for(lambda: w.panel.finite_points("CH1") > 4, seconds=15)
    for name in ("CH3", "CH4", "CH5"):
        _, a, b = w.panel.series(name)
        assert len(a) > 3 and not (a == 0).any() and not (b == 0).any()
        assert all(math.isnan(v) for v in a) and all(math.isnan(v) for v in b)
    w.panel.select("CH3")
    state_row = dict(w.panel.detail_rows())["State"]
    assert state_row.startswith("INVALID") and "NO_LED_STEP" in state_row
    w.panel.select("CH5")
    assert "SATURATED" in dict(w.panel.detail_rows())["State"]
    w.panel.refresh()
    assert w.panel.finite_points("CH1") > 0 and w.panel.finite_points("CH6") > 0
    screenshot(w, "d2-live-invalid-gaps.png")
    w.stop_button.click()
    wait_for(lambda: w.state["device_state"] == "STOPPED", seconds=15)


def test_live_settings_change_and_baseline_reset(window):
    w = fake_window(window)
    to_live(w)
    day = Path(w.folder_text())
    press_start(w)
    wait_for(lambda: w.state.get("preview_active"))
    wait_for(lambda: w.panel.finite_points("CH1") > 2, seconds=15)
    w.live_form.ssr_mode.setCurrentIndex(w.live_form.ssr_mode.findData("both"))
    w.live_form.dpf_mode.setCurrentIndex(w.live_form.dpf_mode.findData("age"))
    w.live_form.age.setValue(28.0)
    assert "outside the published fit range" in w.live_form.dpf_info.text()
    w.apply_live_button.click()
    wait_for(lambda: w.state["preview_config"].ssr_mode == "both"
             and w.state["preview_config"].dpf.mode == "age")
    w.reset_baseline_button.click()
    wait_for(lambda: not w._pending)
    w.panel.select("CH2")
    assert w.state["preview_errors"] == 0
    w.stop_button.click()
    wait_for(lambda: w.state["recording"] == "FINALIZED", seconds=15)
    provenance = json.loads((day / "record_1.preview.json").read_text())
    assert len(provenance["config_changes"]) >= 2
    assert provenance["baseline_resets"][-1]["reason"] == "operator reset"
    assert list(RawReplay(day / "record_1.raw.bin"))


def test_disconnect_draws_a_gap(window):
    w = fake_window(window)
    to_live(w)
    press_start(w)
    wait_for(lambda: w.panel.finite_points("CH1") > 3, seconds=15)
    w.panel_disconnect.click()
    wait_for(lambda: not w.state["connected"], seconds=15)
    wait_for(lambda: all(math.isnan(v) for v in w.panel.series("CH1")[1][-1:]))  # explicit disconnect marker


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
        wait_for(lambda: w.state.get("connected") and w.fs.findData(5) > 0)
        finish_device(w, 5)
        w.folder.setPlainText(str(tmp_path / "rec"))
        for _ in range(3):
            w.next.click()
        wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
        w.next.click()
        assert w.flat == 5 and peer.executed_starts == 0
        press_start(w)
        wait_for(entered.is_set)
        pulses = []
        QtCore.QTimer.singleShot(0, lambda: pulses.append(True))
        wait_for(lambda: bool(pulses))
        release.set()
        wait_for(lambda: w.state.get("start_uncertain"))
        assert "START OUTCOME UNRESOLVED" in w.device.toPlainText()
        assert "Physical LED" in w.device.toPlainText()
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


def test_recorder_open_error_is_reported_and_does_not_start(window, tmp_path):
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
        wait_for(lambda: w.state.get("connected") and w.fs.findData(5) > 0)
        finish_device(w, 5)
        w.folder.setPlainText(str(tmp_path / "rec"))
        for _ in range(3):
            w.next.click()
        wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
        w.next.click()
        press_start(w)
        wait_for(lambda: "injected recorder open failure" in w.error.text())
        assert "STOPPED" in w.device.toPlainText() and peer.executed_starts == 0
        assert "INCOMPLETE / ERROR" in w.recording.toPlainText()
        wait_for(lambda: w.panel_disconnect.isEnabled())
        w.panel_disconnect.click()
        wait_for(lambda: not w.state["connected"])
    finally:
        peer.complete_stop()
        peer.close()


# ---- battery indicator ------------------------------------------------------------------------

def battery_text(w):
    return w.battery_label.text()


def test_battery_label_is_on_every_screen_and_updates(window):
    w = fake_window(window)
    assert w.battery_label.isVisible() and battery_text(w) == "Baterai —"       # Welcome, not connected
    to_device(w)
    wait_for(lambda: battery_text(w).startswith("Baterai 50,5 % · 3,99 V"))
    texts = {1: battery_text(w)}
    finish_device(w)
    for step in (2, 3, 4):
        w.next.click()
        assert w.flat == step and w.battery_label.isVisible()
        texts[step] = battery_text(w)
    wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
    w.next.click()
    assert w.flat == 5 and w.battery_label.isVisible()
    assert all(t.startswith("Baterai 50,5 %") for t in texts.values())
    shown = (battery_text(w) + w.battery_label.toolTip()).lower()
    assert "charging" not in shown and "sedang mengisi" not in shown
    assert "SIMULATED" in w.battery_label.toolTip()
    screenshot(w, "battery-live-ok.png")
    press_start(w)
    wait_for(lambda: "nilai cache sebelum akuisisi" in battery_text(w))
    w.stop_button.click()
    wait_for(lambda: w.state["recording"] == "FINALIZED", seconds=15)
    day = Path(w.folder_text())
    text = "".join(p.read_text(errors="ignore") for p in day.glob("record_1.json"))
    assert "battery" not in text.lower()                   # nothing about the battery in the sidecar
    w.panel_disconnect.click()
    wait_for(lambda: battery_text(w) == "Baterai —")


@pytest.mark.parametrize("options, expected", [
    (dict(battery=dict(valid=False, error=259)), "Baterai belum tersedia"),
    (dict(battery=dict(valid=True, uv=3987000, mperm=48300, error=263)), "refresh gagal (263)"),
    (dict(battery=dict(valid=True, uv=3987000, mperm=105250, error=0)), "105,2 %"),
    (dict(battery_mode="old"), "Baterai: tidak didukung firmware ini"),
    (dict(battery_mode="silent"), "Baterai: tidak ada jawaban"),
])
def test_battery_label_variants(window, options, expected):
    w = fake_window(window, **options)
    to_device(w)
    wait_for(lambda: expected in battery_text(w))
    shown = (battery_text(w) + w.battery_label.toolTip()).lower()
    assert "charging" not in shown and "0,0 %" not in battery_text(w)
    assert w.state["host_counters"]["timeouts"] == 0 and w.state["connected"]
    screenshot(w, "battery-" + expected.split(":")[-1].strip().replace(" ", "-").replace("(", "").replace(")", "")[:24] + ".png")
