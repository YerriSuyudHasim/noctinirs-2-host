"""Stage D2 Qt acceptance. All peers are local fixtures or UDP loopback (simulation only)."""

import json
import math
import os
from pathlib import Path
from threading import Event
import time

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from noctinirs_host.control import protocol as p
from noctinirs_host.control.session import SessionController
from noctinirs_host.engineering import EngineeringRun, default_recording_folder
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import RawReplay
from noctinirs_host.record.writer import Recorder
from noctinirs_host_ui.engineering import EngineeringWindow
from test_engineering_run import GEOMETRY
from calibration_fixture import CalibrationPeer as Peer, CalibrationReadyRun


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
        kwargs.setdefault("run_factory", CalibrationReadyRun)
        kwargs.setdefault("documents", tmp_path / "Documents")
        kwargs.setdefault("settings", QtCore.QSettings(str(tmp_path / "ui.ini"), QtCore.QSettings.Format.IniFormat))
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
    return window(run_factory=lambda *a: CalibrationReadyRun(*a, fake_options=fake_options))


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
    if not w.state.get("calibration_ready"):
        w.worker.submit("seed_calibration")
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
    w.ssr_form.ssr_mode.setCurrentIndex(w.ssr_form.ssr_mode.findData("5mm"))
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
    assert w.ssr_form.ssr_mode.currentData() == "5mm" and w.firmware.text() == "a" * 40
    wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
    screenshot(w, "d2-setup-session.png")
    w.next.click()
    assert w.screens.currentIndex() == 2
    QtTest.QTest.qWait(400)
    assert w.state["device_state"] == "STOPPED" and not w.state["session"]
    assert w.state["raw"]["packets"] == 0 and w.start_button.isEnabled()
    assert w.preview_cfg.baseline_s == 7.0 and w.preview_cfg.ssr_mode == "5mm"


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
    assert len(w.panel.plots) == 20 and len(w.panel.longs) == 16 and w.panel.shorts == ["SC1L", "SC2L", "SC1R", "SC2R"]
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
    # Slots 4,5 -> source L3 (CH3, CH4) have no signal. (6,3),(7,3) saturated -> CH5 (L4, AIN3).
    w = fake_window(window, dead_slots=frozenset({4, 5}), saturate=frozenset({(6, 3), (7, 3)}))
    to_live(w)
    press_start(w)
    wait_for(lambda: w.state.get("session"))
    wait_for(lambda: w.panel.finite_points("CH1") > 4, seconds=15)
    _, a, b = w.panel.series("CH5")                           # saturated: a pure gap, never zero
    assert len(a) > 3 and all(math.isnan(v) for v in a) and all(math.isnan(v) for v in b)
    for name in ("CH3", "CH4"):                              # no signal: gap, or a value that is flagged
        _, a, b = w.panel.series(name)
        assert not (a == 0).any() and not (b == 0).any()
        assert all(p.state != "VALID" or "LOW_SIGNAL" in p.warnings for p in w.panel._pts[name])
    w.panel.select("CH5")
    assert "SATURATED" in dict(w.panel.detail_rows())["State"]
    w.panel.refresh()
    assert "✕" in w.panel.rows["CH5"].status.text()
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
    w.live_form.ssr_mode.setCurrentIndex(w.live_form.ssr_mode.findData("5mm"))
    w.live_form.dpf_mode.setCurrentIndex(w.live_form.dpf_mode.findData("age"))
    w.live_form.age.setValue(28.0)
    assert "outside the published fit range" in w.live_form.dpf_info.text()
    w.apply_live_button.click()
    wait_for(lambda: w.state["preview_config"].ssr_mode == "5mm"
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
    w = window(run_factory=lambda *args: CalibrationReadyRun(*args, controller_factory=lambda tr:
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
        assert "another host" in warning[0]
        # durable record, not the transient notice (a recorder-finalization notice may replace it; review race)
        wait_for(lambda: any("reported SAFE_OFF" in line for line in w.state["log"]))
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

    w = window(run_factory=lambda *args: CalibrationReadyRun(*args, recorder_factory=BrokenRecorder))
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


# ---- D3: window sizes, layout, quality markers ---------------------------------------------

def nav_visible(w):
    """Back (and Continue before Live) are shown and lie inside the window."""
    QtTest.QTest.qWait(40)
    for button in (w.back,) + ((w.next,) if w.flat < 5 else ()):
        assert button.isVisible(), (w.flat, button.text())
        top_left = button.mapTo(w, QtCore.QPoint(0, 0))
        assert top_left.y() >= 0 and top_left.y() + button.height() <= w.height(), (w.flat, button.text())
        assert top_left.x() >= 0 and top_left.x() + button.width() <= w.width(), (w.flat, button.text())


def test_navigation_row_stays_visible_on_every_screen_and_window_size(window):
    w = fake_window(window)
    to_live(w)
    hint = w.minimumSizeHint()
    assert hint.height() <= 560 and hint.width() <= 1024      # no page forces a big minimum on the rest
    for size in ((1280, 720), (1366, 768), (1024, 600)):
        w.showNormal()
        w.resize(*size)
        for flat in (0, 1, 2, 3, 4, 5):
            w._go(flat)
            nav_visible(w)
            assert w.height() <= size[1] + 2                  # the window did not grow past the screen size
    for mode in (w.showMaximized, w.showFullScreen):
        mode()
        for flat in (0, 1, 2, 3, 4, 5):
            w._go(flat)
            nav_visible(w)
    w.showNormal()


def test_live_layout_controls_left_graphs_right_and_plots_follow_the_window(window):
    w = fake_window(window)
    to_live(w)
    assert w.live_split.widget(0) is w.side_scroll and w.live_split.widget(1) is w.panel
    assert w.side_scroll.minimumWidth() != w.side_scroll.maximumWidth()      # no fixed width
    w.showNormal()
    w.resize(1366, 768)
    QtTest.QTest.qWait(100)
    small = w.panel.plots["CH1"].height()
    assert w.side_scroll.width() <= 480 and w.panel.width() > 2 * w.side_scroll.width()
    assert small >= 40                                       # not the old 52 px minimum, but usable
    w.resize(1920, 1080)
    QtTest.QTest.qWait(100)
    assert w.panel.plots["CH1"].height() > small * 1.25      # plots stretch with the window
    for name in ("Sesi dan status", "Tampilan", "Pratinjau: DPF, SCR, baseline", "Detail channel"):
        assert any(b.text().startswith(name) for b in w.findChildren(QtWidgets.QToolButton)), name
    before = w.panel.plots["CH1"].height()
    w.section_detail.button.click()                          # fold the detail away (animated)
    wait_for(lambda: not w.section_detail.animating)
    assert not w.detail.isVisible()
    w.section_detail.button.click()
    wait_for(lambda: not w.section_detail.animating)
    QtTest.QTest.qWait(50)
    assert w.panel.plots["CH1"].height() == before           # the detail never shrinks the graphs for good
    assert not w.section_diag.expanded
    screenshot(w, "d3-live-layout.png")


def test_click_opens_channel_detail_and_markers_differ_from_gaps(window, monkeypatch):
    # CH1 (source L2) gets heavy noise on its ON samples: LOW_SIGNAL, a warning, the line stays.
    # CH5 is saturated: a gap. SC1L/SC2L are dead, so the left long channels go uncorrected with 5 mm SCR.
    w = fake_window(window, noisy={2: 4000.0, 3: 4000.0}, saturate=frozenset({(6, 3), (7, 3)}), dead_slots=frozenset({0, 1}))
    to_live(w)
    w.live_form.ssr_mode.setCurrentIndex(w.live_form.ssr_mode.findData("5mm"))
    wait_for(lambda: w.apply_live_button.isEnabled())
    w.apply_live_button.click()
    wait_for(lambda: w.state["preview_config"].ssr_mode == "5mm")
    press_start(w)
    wait_for(lambda: w.panel.finite_points("CH1") > 8 and w.panel.marker_counts("CH1")[0] > 2, seconds=20)
    warn, unc = w.panel.marker_counts("CH1")
    assert warn > 0 and unc > 0 and w.panel.finite_points("CH1") > 0     # flagged but drawn
    assert w.panel.marker_counts("CH5") == (0, 0) and w.panel.finite_points("CH5") == 0   # a gap
    assert "▲" in w.panel.rows["CH1"].status.text()            # warning priority: a channel with both flags shows ▲
    assert "✕" in w.panel.rows["CH5"].status.text()
    only_uncorrected = [n for n in w.panel.longs if w.panel._pts[n] and w.panel._pts[n][-1].state == "VALID"
                        and w.panel._pts[n][-1].ssr_state == "UNCORRECTED" and not w.panel._pts[n][-1].warnings]
    assert only_uncorrected                                    # the uncorrected state is asserted on its own (D4-10)
    assert all("◆" in w.panel.rows[n].status.text() for n in only_uncorrected)
    shown = []                                                 # the real Qt tooltip event path, not tip_provider
    monkeypatch.setattr(QtWidgets.QToolTip, "showText", lambda *args, **kwargs: shown.append(args[1]))
    for name, expected in (("CH1", "LOW_SIGNAL"), (only_uncorrected[0], "tanpa koreksi")):
        row = w.panel.rows[name]
        point = QtCore.QPoint(10, 10)
        event = QtGui.QHelpEvent(QtCore.QEvent.Type.ToolTip, point, row.mapToGlobal(point))
        QtWidgets.QApplication.sendEvent(row, event)
        assert expected in shown[-1], (name, shown[-1])
    w.panel.rows["SC1L"].name_button.click()                   # click opens the channel detail
    assert w.panel.current == "SC1L" and "SC1L_I735" in w.detail.toPlainText()
    w.panel.plots["CH1"].scene().sigMouseClicked.emit(None)    # a click on a plot selects it as well
    w.panel.select("CH1")
    text = w.detail.toPlainText()
    assert "CH1_OD735" in text and "tanpa koreksi SCR" in text and "I/σ" in text
    screenshot(w, "d3-live-markers.png")
    w.stop_button.click()
    wait_for(lambda: w.state["device_state"] == "STOPPED", seconds=15)


def test_packet_loss_counter_is_shown(window):
    w = fake_window(window, drop_cycles=frozenset({20, 21}))
    to_live(w)
    press_start(w)
    wait_for(lambda: "400 sampel" in w.loss_label.toPlainText(), seconds=20)
    assert "gap_before dari perangkat: 0" in w.loss_label.toPlainText()
    w.stop_button.click()
    wait_for(lambda: w.state["device_state"] == "STOPPED", seconds=15)


def test_robust_auto_scale_clamps_and_marks_spikes_but_full_range_does_not(qapp):
    from noctinirs_host.preview.pipeline import ChannelPoint, FrameResult
    from noctinirs_host_ui.preview_panel import PreviewPanel

    geo = load_geometry(GEOMETRY)
    panel = PreviewPanel(geo)
    panel.show()

    def frame(i, value):
        pts = {c.name: ChannelPoint(c.name, c.kind, "VALID", d_hbo=value, d_hbr=-value) for c in geo.channels}
        return FrameResult(i * 0.1, pts, True)

    panel.push([frame(i, 0.1 * math.sin(i / 5)) for i in range(120)] + [frame(120, 500.0)])
    panel.refresh()
    assert panel.clamped_points("CH1") >= 1                    # the spike is marked, not silently cut
    lo, hi = panel.plots["CH1"].getPlotItem().viewRange()[1]
    assert hi < 5 and lo > -5                                   # the signal itself stays readable
    panel.scale_combo.setCurrentIndex(panel.scale_combo.findData("full"))
    panel.refresh()
    QtTest.QTest.qWait(80)                                      # pyqtgraph applies auto range on the next event
    assert panel.clamped_points("CH1") == 0
    assert panel.plots["CH1"].getPlotItem().viewRange()[1][1] > 100
    panel.close()


def test_real_wizard_gates_start_before_and_after_a_fake_sweep(window):
    from noctinirs_host.calibration import Assumptions
    from noctinirs_host.calibration_sweep import SweepPlan

    from test_udp_session import Clock

    clock = Clock()

    class TickClockRun(EngineeringRun):
        def tick(self):
            clock.now += .1
            super().tick()

    w = window(run_factory=lambda *args: TickClockRun(
        *args, controller_factory=lambda transport: SessionController(transport, clock),
        fake_options={"clock": clock}, calibration_params=Assumptions(min_occurrences=4, visit_target_occurrences=5),
        sweep_plan=SweepPlan((8, 67, 133), (4, 16, 67), 1)))
    to_live(w)
    assert not w.start_button.isEnabled()
    w.record_check.setChecked(False)
    start_calibration = w._calibration_controls[-1][0]
    wait_for(start_calibration.isEnabled)
    start_calibration.click()
    wait_for(lambda: w.start_button.isEnabled(), seconds=15)
    w.start_button.click()
    wait_for(lambda: w.state.get("preview_active"), seconds=8)
    w.stop_button.click()
    wait_for(lambda: w.state.get("stopped") and start_calibration.isEnabled())
    assert not w.start_button.isEnabled()
