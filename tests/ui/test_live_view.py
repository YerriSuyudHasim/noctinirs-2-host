"""S2 live view (offscreen). The device is the in-process simulator; nothing here is device data."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from noctinirs_host_ui.app import MainWindow, geometry_path  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._timer.stop()
    w._ui_timer.stop()
    clock = Clock()
    # drive the simulator and the controller with a virtual clock
    w._simulator._clock = clock
    w.controller._clock = clock
    w.clock = clock
    yield w
    w.close()


def run(w, seconds, step=0.05):
    for _ in range(round(seconds / step)):
        w.clock.t += step
        w.controller.tick()
        w.tick()


def stream(w, fs=10):
    assert w.controller.connect().ok and w.controller.set_fs(fs).ok and w.controller.start().ok


def test_geometry_file_is_found():
    assert geometry_path().is_file()


def test_starts_with_waiting_message_and_all_channels_listed(win):
    assert not win.live.empty_label.isHidden() or win.live.empty_label.isVisibleTo(win.live)
    assert win.live.channel_list.count() == len(win.geometry.channels) == 20
    assert len(win.live.overview_plots) == 20
    assert win.live.selected() == [0, 1, 2, 3]


def test_overview_draws_every_channel_with_quality_titles(win):
    stream(win)
    run(win, 3.0)
    assert win.receiver.counters["frames"] >= 25
    for curves in win.live.overview_curves:
        for c in curves.values():
            x, y = c.getData()
            assert len(x) > 20 and np.isfinite(y).all()
    assert "good" in win.live.overview_plots[0].titleLabel.text
    assert "frames" in win.live.status.text() and "lost 0" in win.live.status.text()


@pytest.mark.parametrize("n", [1, 7, 20])
def test_detail_shows_exactly_the_selected_channels(win, n):
    stream(win)
    win.live.set_selected(range(n))
    run(win, 1.0)
    assert len(win.live.detail_plots) == n == len(win.live.selected())
    assert [i for i, _, _ in win.live.detail_plots] == list(range(n))
    assert f"{n} of 20" in win.live.count_label.text()


def test_last_channel_cannot_be_unchecked(win):
    win.live.set_selected([5])
    item = win.live.channel_list.item(5)
    item.setCheckState(QtCore.Qt.CheckState.Unchecked)
    assert win.live.selected() == [5]


def test_lost_frames_show_as_a_break_not_a_line(win):
    stream(win)
    run(win, 1.0)
    win.controller._t.drop_next_frames = 3
    run(win, 1.5)
    assert win.receiver.counters["lost"] == 3
    x, y = win.live.overview_curves[0]["735"].getData()
    assert np.isnan(y).sum() == 1


def test_dead_led_channel_shows_gap_and_titles_it(win):
    stream(win)
    ch = win.receiver.channels[0]
    win.controller.stop()
    win.controller.set_led_current(ch.slots[0], 0)
    win.controller.start()
    run(win, 1.5)
    x, y = win.live.overview_curves[0]["735"].getData()
    assert np.isnan(y).all()
    assert "gap" in win.live.overview_plots[0].titleLabel.text
    other = win.live.overview_plots[19].titleLabel.text
    assert "good" in other


def test_pause_freezes_drawing_but_not_counting(win):
    stream(win)
    run(win, 1.0)
    win.live.pause_btn.setChecked(True)
    n = len(win.live.overview_curves[0]["735"].getData()[0])
    run(win, 1.0)
    assert len(win.live.overview_curves[0]["735"].getData()[0]) == n
    assert win.receiver.counters["frames"] > n
    win.live.pause_btn.setChecked(False)
    assert len(win.live.overview_curves[0]["735"].getData()[0]) > n


def test_window_choice_limits_the_drawn_time_span(win):
    stream(win)
    run(win, 15.0)
    win.live.window_combo.setCurrentIndex(0)  # 10 s
    x, _ = win.live.overview_curves[0]["735"].getData()
    assert x[-1] - x[0] <= 10.0 + 1e-6


def test_labels_say_simulated_and_geometry_is_draft(win):
    stream(win)
    run(win, 1.0)
    text = win.live.plot_note.text()
    assert "SIMULATED" in text and "draft" in text.lower()


def test_theme_switch_keeps_live_view_working(win):
    stream(win)
    run(win, 1.0)
    win.set_theme("dark")
    run(win, 0.5)
    assert win.live.theme.name == "dark"
    win.set_theme("light")
    assert win.live.overview.backgroundBrush().color().name().lower() == "#ffffff"
