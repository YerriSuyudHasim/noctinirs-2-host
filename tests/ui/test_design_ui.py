"""D4 Qt acceptance: themes, motion, the stacked channel rows, sizes. Local fake only (no device)."""

import math
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.pipeline import ChannelPoint, FrameResult
from noctinirs_host_ui.design import tokens as T
from noctinirs_host_ui.design.manager import ThemeManager
from noctinirs_host_ui.engineering import MIN_WINDOW, EngineeringWindow
from noctinirs_host_ui.preview_panel import (DIVIDER_H, ROW_MIN_H, PreviewPanel, channel_order,
                                             peak_decimate)
from noctinirs_host_ui.widgets import ElidedLabel, FadeStack, Section, StatusChip
from test_engineering_run import GEOMETRY

EXPECTED_ORDER = ([f"CH{i}" for i in range(1, 9)] + ["SC1L", "SC2L"]
                  + [f"CH{i}" for i in range(9, 17)] + ["SC1R", "SC2R"])


def wait_for(condition, seconds=8):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "Qt state did not converge"
        QtTest.QTest.qWait(15)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    return app


def ini(tmp_path, name="ui.ini"):
    return QtCore.QSettings(str(tmp_path / name), QtCore.QSettings.Format.IniFormat)


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    windows = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)

    def build(settings=None):
        w = EngineeringWindow(load_geometry(GEOMETRY), documents=tmp_path / "Documents",
                              settings=settings or ini(tmp_path))
        windows.append(w)
        w.show()
        return w

    yield build
    for w in windows:
        if w.worker.isRunning():
            w.worker.submit("shutdown")
        wait_for(lambda: not w.worker.isRunning(), seconds=12)
        w.close()


def frame(geo, i, amp=1.0, state="VALID"):
    pts = {c.name: ChannelPoint(c.name, c.kind, state, d_hbo=amp * (1 + c.ain) * math.sin(i / 7),
                                d_hbr=-amp * (1 + c.ain) * math.cos(i / 7), od735=0.01, od850=0.02,
                                i735=900.0, i850=950.0) for c in geo.channels}
    return FrameResult(i * 0.1, pts, True)


def panel_with_data(qapp, n=200, theme=None, amp=1.0):
    geo = load_geometry(GEOMETRY)
    panel = PreviewPanel(geo, theme)
    panel.resize(1000, 700)
    panel.show()
    QtTest.QTest.qWait(80)
    panel.push([frame(geo, i, amp) for i in range(n)])
    return geo, panel


def inside(w, widget):
    top_left = widget.mapTo(w, QtCore.QPoint(0, 0))
    return (widget.isVisible() and top_left.x() >= 0 and top_left.y() >= 0
            and top_left.x() + widget.width() <= w.width() and top_left.y() + widget.height() <= w.height())


# ---- theme: live switch and persistence ----------------------------------------------------------

def test_theme_switch_is_live_and_colours_the_plots_and_the_application(make_window):
    w = make_window()
    assert w.theme.name == "light" and T.LIGHT.background in w.root.styleSheet()
    row = w.panel.rows["CH1"]
    assert row.plot.backgroundBrush().color().name().upper() == T.LIGHT.plot_background.upper()
    assert QtWidgets.QApplication.instance().palette().color(QtGui.QPalette.ColorRole.Window).name().upper() \
        == T.LIGHT.background.upper()
    w.action_dark.trigger()                                  # the header menu, not a restart
    assert w.theme.name == "dark" and T.DARK.background in w.root.styleSheet()
    assert T.LIGHT.background not in w.root.styleSheet()
    assert row.plot.backgroundBrush().color().name().upper() == T.DARK.plot_background.upper()
    assert QtWidgets.QApplication.instance().palette().color(QtGui.QPalette.ColorRole.Window).name().upper() \
        == T.DARK.background.upper()
    assert w.action_dark.isChecked() and not w.action_light.isChecked()
    assert w.panel.curves["CH1"][0].opts["pen"].color().name().upper() == T.DARK.hbo.upper()   # HbO
    assert w.panel.curves["CH1"][1].opts["pen"].color().name().upper() == T.DARK.hbr.upper()   # HbR
    w.set_theme("light")
    assert w.panel.curves["CH1"][0].opts["pen"].color().name().upper() == T.LIGHT.hbo.upper()
    assert w.battery_label.tone in StatusChip.TONES


def test_theme_and_reduce_motion_persist_between_sessions(make_window, tmp_path):
    settings = ini(tmp_path, "persist.ini")
    first = ThemeManager(settings)
    assert first.name == "light" and not first.reduce_motion          # defaults
    first.set_theme("dark")
    first.set_reduce_motion(True)
    settings.sync()
    again = ThemeManager(ini(tmp_path, "persist.ini"))
    assert again.name == "dark" and again.reduce_motion
    w = make_window(ini(tmp_path, "persist.ini"))                      # a whole window starts in dark
    assert w.theme.name == "dark" and T.DARK.background in w.root.styleSheet()
    assert w.action_reduce_motion.isChecked()
    with pytest.raises(ValueError):
        again.set_theme("pink")


# ---- motion ---------------------------------------------------------------------------------------

def test_transitions_run_and_reduce_motion_turns_every_animation_off(make_window):
    w = make_window()
    assert isinstance(w.screens, FadeStack) and not w.theme.reduce_motion
    before = w.screens.transitions_started
    w._go(1)
    assert w.screens.transitions_started == before + 1 and w.screens.animating
    wait_for(lambda: not w.screens.animating)
    assert w.screens.currentWidget().graphicsEffect() is None          # the effect is removed afterwards
    w.action_reduce_motion.setChecked(True)
    assert w.theme.reduce_motion and w.theme.ms(200) == 0
    started = w.screens.transitions_started
    w._go(2)
    w._go(0)
    assert w.screens.transitions_started == started and not w.screens.animating
    # folding a section and collapsing the panel are instant too
    w._go(5)
    w.section_detail.button.click()
    assert not w.section_detail.animating and not w.detail.isVisible()
    w.toggle_sidebar()
    assert not w.sidebar_animating and not w.side_scroll.isVisible()
    w.toggle_sidebar()
    assert w.side_scroll.isVisible() and w.side_scroll.maximumWidth() > 400


def test_motion_animates_when_enabled_and_status_chips_change_colour_smoothly(make_window):
    w = make_window()
    w._go(5)
    QtTest.QTest.qWait(50)
    w.section_detail.button.click()
    assert w.section_detail.animating
    wait_for(lambda: not w.section_detail.animating)
    assert not w.detail.isVisible()
    chip = StatusChip("x", w.theme)
    chip.show()
    chip.set_tone("danger")
    assert chip.animating
    wait_for(lambda: not chip.animating)
    w.toggle_sidebar()
    assert w.sidebar_animating
    wait_for(lambda: not w.sidebar_animating)
    assert not w.side_scroll.isVisible()
    # the Live page fades only its settings panel: the graph panel never gets an effect
    w._go(0)
    w._go(5)
    assert w.panel.graphicsEffect() is None and w.screens.currentWidget().fade_target is w.side_scroll


# ---- channel rows ----------------------------------------------------------------------------------

def test_channel_order_follows_the_geometry_and_dividers_separate_the_hemispheres(qapp):
    geo = load_geometry(GEOMETRY)
    groups = channel_order(geo)
    assert [g[0] for g in groups] == ["Left hemisphere", "Right hemisphere"]
    assert [c.name for _, chs in groups for c in chs] == EXPECTED_ORDER
    side = {s.id: s.side for s in geo.sources}                          # verified against the channel map
    assert all(side[c.source] == "L" for c in groups[0][1]) and all(side[c.source] == "R" for c in groups[1][1])
    panel = PreviewPanel(geo)
    assert panel.order == EXPECTED_ORDER
    layout = panel.rows_host.layout()
    seen = [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget()]
    labels = [w.text() if isinstance(w, QtWidgets.QLabel) else w.name for w in seen]
    assert labels[0] == "LEFT HEMISPHERE" and labels[labels.index("RIGHT HEMISPHERE") - 1] == "SC2L"
    assert labels[labels.index("RIGHT HEMISPHERE") + 1] == "CH9" and labels[-1] == "SC2R"
    for name in ("SC1L", "SC2L", "CH1", "CH9", "SC1R", "SC2R"):         # label: name, hemisphere, short or long
        meta = panel.rows[name].meta.text()
        assert meta.startswith("Left" if name in EXPECTED_ORDER[:10] else "Right")
        assert ("short" if name.startswith("SC") else "long") in meta
    panel.close()


@pytest.mark.parametrize("size", [(1280, 720), (1366, 768), (1920, 1080), MIN_WINDOW])
def test_exactly_three_rows_fill_the_visible_area_and_the_rest_scrolls(make_window, size):
    w = make_window()
    w._go(5)
    w.resize(*size)
    QtTest.QTest.qWait(150)
    p = w.panel
    visible = p.fully_visible_rows()
    if size != MIN_WINDOW:
        assert visible == ["CH1", "CH2", "CH3"]
        vh = p.scroll.viewport().height()
        assert abs(3 * p.row_height() + DIVIDER_H - vh) < 3 + DIVIDER_H  # three rows plus the divider fill it
    else:
        assert p.row_height() == ROW_MIN_H and len(visible) >= 1          # small window: the minimum height
    bar = p.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    p.scroll.scroll_to(bar.maximum())
    wait_for(lambda: bar.value() == bar.maximum())
    assert p.fully_visible_rows()[-1] == "SC2R"
    p.scroll.scroll_to(0)
    wait_for(lambda: bar.value() == 0)
    assert p.fully_visible_rows()[:1] == ["CH1"]


def test_every_row_has_both_traces_in_one_plot_and_a_legend(qapp):
    geo, panel = panel_with_data(qapp)
    panel.refresh()
    for name in panel.order:
        assert len(panel.curves[name]) == 4          # two traces, each with a current and a previous pass
        assert "ΔHbO" in panel.rows[name].legend.text() and "ΔHbR" in panel.rows[name].legend.text()
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("OD"))
    assert "OD 735" in panel.rows["CH1"].legend.text() and "OD 850" in panel.rows["CH1"].legend.text()
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("I"))
    assert "I 735" in panel.rows["CH5"].legend.text()
    panel.close()


def test_all_rows_share_one_linked_time_axis_and_only_the_footer_has_labels(qapp):
    geo, panel = panel_with_data(qapp)
    master_vb = panel.master.getPlotItem().getViewBox()
    for name in panel.order[1:]:
        assert panel.rows[name].plot.getPlotItem().getViewBox().linkedView(0) is master_vb
        assert not panel.rows[name].plot.getPlotItem().getAxis("bottom").isVisible()
    assert panel.footer.getPlotItem().getViewBox().linkedView(0) is master_vb
    assert panel.footer.getPlotItem().getAxis("bottom").isVisible()
    panel.window_combo.setCurrentIndex(panel.window_combo.findData(10))
    QtTest.QTest.qWait(60)
    ranges = {tuple(round(v, 3) for v in panel.rows[n].plot.getPlotItem().getViewBox().viewRange()[0])
              for n in panel.order}
    assert ranges == {(0.0, 10.0)}
    foot = panel.footer.getPlotItem().getViewBox().viewRange()[0]
    assert abs(foot[0]) < 0.3 and abs(foot[1] - 10.0) < 0.3      # the footer axis lines up with the rows
    panel.close()


def test_shared_scale_lock_gives_visible_rows_one_y_range(qapp):
    geo = load_geometry(GEOMETRY)
    panel = PreviewPanel(geo)
    panel.resize(1000, 700)
    panel.show()
    QtTest.QTest.qWait(80)

    def varied(i):
        f = frame(geo, i)
        for n, p in f.points.items():            # CH2 is much larger than CH1 and CH3
            k = 30.0 if n == "CH2" else 1.0
            f.points[n] = ChannelPoint(n, p.kind, "VALID", d_hbo=p.d_hbo * k, d_hbr=p.d_hbr * k)
        return f

    panel.push([varied(i) for i in range(200)])
    panel.refresh()
    own = [tuple(panel.rows[n].plot.getPlotItem().getViewBox().viewRange()[1]) for n in ("CH1", "CH2", "CH3")]
    assert own[0] != own[1]                      # autoscale per row by default
    panel.lock_check.setChecked(True)
    panel.refresh()
    locked = {tuple(round(v, 6) for v in panel.rows[n].plot.getPlotItem().getViewBox().viewRange()[1])
              for n in ("CH1", "CH2", "CH3")}
    assert len(locked) == 1
    lo, hi = next(iter(locked))
    assert hi - lo >= own[1][1] - own[1][0] - 1e-6   # the shared range covers the largest row
    panel.close()


def test_off_screen_rows_are_not_redrawn_until_scrolled_into_view(qapp):
    geo, panel = panel_with_data(qapp)
    panel.refresh()
    assert len(panel.curves["CH1"][0].getData()[0]) > 0
    assert panel.curves["SC2R"][0].getData()[0] is None or len(panel.curves["SC2R"][0].getData()[0]) == 0
    assert "SC2R" in panel._dirty_rows
    bar = panel.scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    panel.refresh()
    assert len(panel.curves["SC2R"][0].getData()[0]) > 0
    panel.close()


def test_peak_decimation_keeps_spikes_and_gaps_and_refresh_is_cheap(qapp):
    x = np.arange(5000, dtype=float)
    y = np.sin(x / 50)
    y[2500] = 40.0
    y[3000:3100] = np.nan
    xs, ys = peak_decimate(x, y, 400)
    assert len(xs) <= 400 and np.nanmax(ys) == 40.0 and np.isnan(ys).any()
    assert peak_decimate(x[:100], y[:100], 400)[0] is x[:100] or len(peak_decimate(x[:100], y[:100], 400)[0]) == 100
    geo, panel = panel_with_data(qapp, n=1300)
    panel.refresh()
    times = []
    for i in range(10):
        panel._dirty_rows = set(panel.order)
        t0 = time.perf_counter()
        panel.refresh()
        times.append((time.perf_counter() - t0) * 1000)
    assert sorted(times)[len(times) // 2] < 120        # a loose bound; the benchmark script reports the real numbers
    panel.close()


# ---- sizes: navigation and START/STOP stay reachable --------------------------------------------------

def test_navigation_and_start_stop_stay_visible_at_every_size_and_with_the_panel_collapsed(make_window):
    w = make_window()
    w.theme.set_reduce_motion(True)
    assert w.minimumSize().width() <= MIN_WINDOW[0] and w.minimumSizeHint().height() <= MIN_WINDOW[1]
    sizes = [(1280, 720), (1366, 768), MIN_WINDOW]
    for size in sizes:
        w.showNormal()
        w.resize(*size)
        QtTest.QTest.qWait(80)
        for flat in range(6):
            w._go(flat)
            QtTest.QTest.qWait(30)
            assert inside(w, w.back) and (flat == 5 or inside(w, w.next)), (size, flat)
        w._go(5)
        for button in (w.start_button, w.stop_button, w.panel_disconnect):
            assert inside(w, button), (size, button.text())
        w.toggle_sidebar()                                        # collapsed: the action bar stays
        QtTest.QTest.qWait(40)
        for button in (w.start_button, w.stop_button):
            assert inside(w, button), (size, "collapsed", button.text())
        assert w.panel.width() > w.width() * 0.9
        w.toggle_sidebar()
    for mode in (w.showMaximized, w.showFullScreen):
        mode()
        QtTest.QTest.qWait(80)
        for flat in range(6):
            w._go(flat)
            assert inside(w, w.back) and inside(w, w.start_button) or flat != 5
    w.showNormal()


def test_long_text_is_elided_with_a_tooltip_and_chips_do_not_overflow(make_window):
    w = make_window()
    w.resize(*MIN_WINDOW)
    QtTest.QTest.qWait(100)
    assert isinstance(w.banner, ElidedLabel) and w.banner.is_elided()
    w.banner.repaint()
    assert "PREVIEW" in w.banner.toolTip()
    label = ElidedLabel("x" * 300)
    label.resize(120, 20)
    label.show()
    label.repaint()
    assert label.is_elided() and label.toolTip() == "x" * 300 and label.full_text() == "x" * 300
    for chip in (w.connection_chip, w.recording_chip, w.battery_label):
        assert chip.x() >= 0 and chip.x() + chip.width() <= w.width()


def test_status_chips_follow_the_session_state(make_window):
    w = make_window()
    assert w.connection_chip.tone == "neutral" and "Tidak terhubung" in w.connection_chip.text()
    w.next.click()
    w.connect_button.click()
    wait_for(lambda: w.state.get("connected"))
    wait_for(lambda: w.connection_chip.tone == "ok" and "STOPPED" in w.connection_chip.text())
    assert w.recording_chip.tone == "neutral"
    wait_for(lambda: w.battery_label.text().startswith("Baterai 50,5"))
