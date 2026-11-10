"""Regression tests for the independent D4 review (docs/local-review/pr-d4-fix). One section per finding.

Every test here failed on the D4 code (de9f1b4) and passes on the fix. Host/unit and offscreen-simulation
evidence only: no device, no network.
"""

import math
import re
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from calibration_fixture import CalibrationReadyRun
from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.pipeline import ChannelPoint, FrameResult
from noctinirs_host_ui import engineering as eng
from noctinirs_host_ui.design import tokens as T
from noctinirs_host_ui.design.manager import ThemeManager
from noctinirs_host_ui.engineering import MIN_WINDOW, EngineeringWindow
from noctinirs_host_ui.preview_panel import DIVIDER_H, ROW_MIN_H, VISIBLE_ROWS, PreviewPanel, peak_decimate
from noctinirs_host_ui.widgets import FadeStack, Section, SmoothScrollArea, StatusChip
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


def ini(tmp_path, name="ui.ini"):
    return QtCore.QSettings(str(tmp_path / name), QtCore.QSettings.Format.IniFormat)


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    windows = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)

    def build(*, frozen_state=False, reduce_motion=False, settings=None):
        w = EngineeringWindow(load_geometry(GEOMETRY), documents=tmp_path / "Documents",
                              settings=settings or ini(tmp_path))
        windows.append(w)
        if reduce_motion:
            w.theme.set_reduce_motion(True)
        if frozen_state:                      # the test sets w.state itself: the worker must not overwrite it
            w.worker.updated.disconnect(w._update)
        w.show()
        return w

    yield build
    for w in windows:
        worker = w.worker                       # the worker is Python-owned: valid even if Qt deleted the window
        if worker.isRunning():
            worker.submit("shutdown")
        wait_for(lambda: not worker.isRunning(), seconds=12)
        try:
            w.close()
        except RuntimeError:                    # the window was closed (and deleted) by the test itself
            pass


def frame(geo, i, amp=1.0, state="VALID", scale=None):
    scale = scale or {}
    nan = math.nan if state != "VALID" else 1.0       # an invalid channel carries NaN, as in the real pipeline
    pts = {c.name: ChannelPoint(c.name, c.kind, state,
                                d_hbo=nan * amp * scale.get(c.name, 1.0) * (1 + c.ain) * math.sin(i / 7),
                                d_hbr=-nan * amp * scale.get(c.name, 1.0) * (1 + c.ain) * math.cos(i / 7),
                                od735=0.01 * scale.get(c.name, 1.0) * math.sin(i / 7), od850=0.02, i735=900.0, i850=950.0)
           for c in geo.channels}
    return FrameResult(i * 0.1, pts, True)


def panel_with(qapp, frames, width=1000, height=700, theme=None):
    geo = load_geometry(GEOMETRY)
    panel = PreviewPanel(geo, theme)
    panel.resize(width, height)
    panel.show()
    QtTest.QTest.qWait(80)
    panel.push([f(geo) if callable(f) else f for f in frames])
    return geo, panel


def visible_fraction(widget) -> float:
    """Share of the widget's rectangle that survives clipping by every ancestor (a scroll area's viewport,
    a splitter pane, the window)."""
    if not widget.isVisible():
        return 0.0
    frac = 1.0
    parent = widget.parentWidget()
    while parent is not None:
        top_left = widget.mapTo(parent, QtCore.QPoint(0, 0))
        rect = QtCore.QRect(top_left, widget.size())
        cut = rect.intersected(parent.rect())
        frac = min(frac, (cut.width() * cut.height()) / max(1, rect.width() * rect.height()))
        parent = parent.parentWidget()
    return frac


def fully_visible_rows(panel):
    """Rows whose whole rectangle lies inside the viewport (no tolerance)."""
    top = panel.scroll.verticalScrollBar().value()
    bottom = top + panel.scroll.viewport().height()
    return [n for n in panel.order if panel.rows[n].y() >= top and panel.rows[n].y() + panel.rows[n].height() <= bottom]


LONG = "Device reported a long message that has to wrap onto several lines to prove nothing is pushed away. " * 3
SIZES = [MIN_WINDOW, (1024, 600), (1280, 720)]


def synthetic_state(w, uncertain):
    w.state = {"connected": True, "start_uncertain": uncertain, "start_wait": 0.0, "device_state": "STOPPED",
               "notice": LONG if uncertain else "", "error": LONG if uncertain else "", "stopped": True}
    w._render()


# ---- D4-01 ----------------------------------------------------------------------------------------

def test_d4_01_the_worker_survives_the_deletion_of_its_window(make_window):
    w = make_window()
    worker = w.worker
    w.close()                                                    # not recording: STOP/finalize is a no-op
    wait_for(lambda: w._worker_finished)
    w.close()                                                    # the accepted final close
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    QtTest.QTest.qWait(50)
    assert not worker.isRunning()                                # the post-loop code of main() does exactly this
    eng.shutdown_worker(worker)                                  # and must not raise on a finished worker


def test_d4_01_main_returns_zero_after_closing_a_recording_session(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    seen = {}

    def drive(w):
        seen["worker"] = w.worker
        w.next.click()
        w.connect_button.click()
        wait_for(lambda: w.state.get("connected") and w.fs.findData(10) > 0)
        w.fs.setCurrentIndex(w.fs.findData(10))
        w.apply.click()
        wait_for(lambda: w.state.get("configured") and w.next.isEnabled())
        w.next.click()
        w.mbll_form.baseline.setValue(1.0)
        for _ in range(2):
            w.next.click()
        wait_for(lambda: "Ready to open the live panel" in w.summary.toPlainText() and w.next.isEnabled())
        w.next.click()
        wait_for(lambda: w.start_button.isEnabled())
        w.start_button.click()
        wait_for(lambda: w.state.get("recording") == "RECORDING", seconds=20)
        seen["recording_seen"] = True
        w.close()                                                # deferred: shows the cleanup result
        wait_for(lambda: w._worker_finished, seconds=20)
        w.close()                                                # accepted: Qt deletes the window and its children

    code = eng.main([], run_factory=CalibrationReadyRun, settings=ini(tmp_path),
                    documents=tmp_path / "Documents",
                    on_show=lambda w: QtCore.QTimer.singleShot(0, lambda: drive(w)))
    assert code == 0 and seen.get("recording_seen")
    assert not seen["worker"].isRunning()


# ---- D4-02 / D4-03 --------------------------------------------------------------------------------------

@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("sidebar", [True, False])
@pytest.mark.parametrize("size", SIZES)
def test_d4_02_action_bar_and_recovery_are_never_clipped_and_nothing_overflows(make_window, size, sidebar, uncertain):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*size)
    if w.sidebar_open != sidebar:
        w.toggle_sidebar()
    synthetic_state(w, uncertain)
    QtTest.QTest.qWait(150)
    buttons = [w.start_button, w.stop_button, w.panel_disconnect]
    if uncertain:
        check, safe = w._recovery_buttons[-1]
        buttons += [check, safe]
    for b in buttons:
        assert visible_fraction(b) == 1.0, (size, sidebar, uncertain, b.text())
    p = w.panel
    for widget in (p.collapse_button, p.banner, p.jump_combo, p.footer, p.legend):
        assert visible_fraction(widget) == 1.0, (size, sidebar, uncertain, widget.objectName() or widget)
    for name in ("CH1", "CH2", "CH3"):
        assert visible_fraction(p.rows[name].plot) == 1.0
    assert w.back.isVisible() and visible_fraction(w.back) == 1.0
    # the complete preview warning is shown (wrapped, not cut)
    assert p.banner.height() >= p.banner.heightForWidth(p.banner.width())
    assert "belum tervalidasi" in p.banner.text()
    right = p.mapTo(w, QtCore.QPoint(p.width(), 0)).x()
    assert right <= w.width()
    # long messages are capped and keep the full text in the tooltip
    if uncertain:
        assert w.feedback.toolTip() == w.feedback.text() and w.error.toolTip() == w.error.text()


@pytest.mark.parametrize("uncertain", [False, True])
def test_d4_02_recovery_buttons_work_from_the_keyboard(make_window, monkeypatch, uncertain):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*MIN_WINDOW)
    synthetic_state(w, uncertain)
    QtTest.QTest.qWait(100)
    sent = []
    monkeypatch.setattr(w, "_submit", lambda action, *a: sent.append(action))
    for b in (w.start_button, w.stop_button, w.panel_disconnect):
        assert b.focusPolicy() != QtCore.Qt.FocusPolicy.NoFocus
    if uncertain:
        check, safe = w._recovery_buttons[-1]
        assert check.isEnabled() and check.focusPolicy() != QtCore.Qt.FocusPolicy.NoFocus
        check.setFocus()
        QtTest.QTest.keyClick(check, QtCore.Qt.Key.Key_Space)        # keyboard activation
        assert sent == ["check_start_outcome"]
        assert safe.isEnabled() and safe.focusPolicy() != QtCore.Qt.FocusPolicy.NoFocus


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("sidebar", [True, False])
@pytest.mark.parametrize("size", SIZES)
def test_d4_03_three_complete_rows_are_visible_in_every_supported_state(make_window, size, sidebar, uncertain):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*size)
    if w.sidebar_open != sidebar:
        w.toggle_sidebar()
    synthetic_state(w, uncertain)
    QtTest.QTest.qWait(150)
    p = w.panel
    bar = p.scroll.verticalScrollBar()
    assert p.row_height() >= ROW_MIN_H
    assert p.scroll.viewport().height() >= DIVIDER_H + VISIBLE_ROWS * ROW_MIN_H     # the window minimum is budgeted
    assert fully_visible_rows(p)[:3] == ["CH1", "CH2", "CH3"], (size, sidebar, uncertain)
    for stop in p.row_stops():                                  # and at every settled (row-aligned) position
        p.scroll.scroll_to(stop)
        assert len(fully_visible_rows(p)) >= 3, (size, sidebar, uncertain, stop)
    p.scroll.scroll_to(bar.maximum())
    assert fully_visible_rows(p)[-3:] == p.order[-3:]


def test_d4_03_the_wheel_moves_one_row_at_a_time_and_never_splits_a_row(make_window):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*MIN_WINDOW)
    synthetic_state(w, False)
    QtTest.QTest.qWait(150)
    p = w.panel
    viewport = p.scroll.viewport()
    bar = p.scroll.verticalScrollBar()

    def notch(direction):
        centre = viewport.rect().center()
        ev = QtGui.QWheelEvent(QtCore.QPointF(centre), QtCore.QPointF(viewport.mapToGlobal(centre)), QtCore.QPoint(),
                               QtCore.QPoint(0, 120 * direction), QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(viewport, ev)

    seen = []
    for _ in range(6):
        notch(-1)                                               # wheel down
        seen.append(bar.value())
        assert bar.value() in p.row_stops() or bar.value() == bar.maximum()
        assert len(fully_visible_rows(p)) >= 3
    assert seen == sorted(seen) and len(set(seen)) == len(seen)
    notch(+1)
    assert bar.value() == seen[-2]


def test_d4_03_a_short_row_keeps_name_and_status_and_a_tall_row_shows_everything(make_window):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*MIN_WINDOW)
    synthetic_state(w, True)
    QtTest.QTest.qWait(150)
    row = w.panel.rows["CH1"]
    assert row.compact and not row.meta.isVisible() and row.name_button.isVisible() and row.status.isVisible()
    assert visible_fraction(row.status) == 1.0 and visible_fraction(row.name_button) == 1.0
    w.showMaximized()
    synthetic_state(w, False)
    QtTest.QTest.qWait(200)
    row = w.panel.rows["CH1"]
    assert not row.compact and row.meta.isVisible() and row.legend.isVisible()
    w.showNormal()


# ---- D4-04 ----------------------------------------------------------------------------------------------

def test_d4_04_a_single_invalid_sample_still_breaks_the_line():
    x = np.arange(12, dtype=float)
    y = np.array([1, 2, 3, 4, np.nan, 6, 7, 8, 9, 10, 11, 12], dtype=float)
    xs, ys = peak_decimate(x, y, 6)
    assert len(xs) <= 8 and np.isnan(ys).sum() == 1
    k = int(np.flatnonzero(np.isnan(ys))[0])
    assert 0 < k < len(ys) - 1
    assert np.all(xs[:k] < 4) and np.all(xs[k + 1:] > 4)         # nothing is drawn across the gap


def test_d4_04_the_minimum_and_maximum_keep_their_time_order_and_timestamps():
    x = np.arange(40, dtype=float)
    y = np.zeros(40)
    y[1], y[3] = 5.0, -5.0                                       # maximum first, then minimum, in one bin
    y[21], y[23] = -6.0, 7.0                                     # minimum first, then maximum, in another
    xs, ys = peak_decimate(x, y, 8)
    finite = np.isfinite(ys)
    assert np.all(np.diff(xs[finite]) >= 0)                      # time never goes backwards
    pairs = {(float(a), float(b)) for a, b in zip(xs, ys)}
    assert {(1.0, 5.0), (3.0, -5.0), (21.0, -6.0), (23.0, 7.0)} <= pairs
    order = [float(a) for a, b in zip(xs, ys) if (float(a), float(b)) in {(1.0, 5.0), (3.0, -5.0)}]
    assert order == [1.0, 3.0]                                   # the maximum (t=1) comes before the minimum (t=3)


@pytest.mark.parametrize("seed", range(12))
def test_d4_04_random_series_keep_every_gap_every_extreme_and_stay_on_the_data(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(300, 1500))
    x = np.sort(rng.uniform(0, 30, n))
    y = rng.normal(size=n).cumsum()
    for _ in range(int(rng.integers(1, 12))):                    # gaps of 1 to 40 samples, at any position
        a = int(rng.integers(1, n - 2))
        y[a:a + int(rng.integers(1, 40))] = np.nan
    y[int(rng.integers(0, n))] = np.nan
    budget = int(rng.integers(40, 200))
    xs, ys = peak_decimate(x, y, budget)
    finite_in = np.isfinite(y)
    lead = np.flatnonzero(finite_in)[0]
    internal = int(np.sum((np.diff(finite_in[lead:].astype(int)) == 1)))      # gaps that end with finite data again
    assert int(np.isnan(ys).sum()) == internal                    # one separator for every internal gap
    ok = np.isfinite(ys)
    lookup = dict(zip(x.tolist(), y.tolist()))
    assert all(lookup[a] == b for a, b in zip(xs[ok].tolist(), ys[ok].tolist()))     # only real samples
    assert np.all(np.diff(xs[ok]) >= 0)
    assert ys[ok].max() == np.nanmax(y) and ys[ok].min() == np.nanmin(y)              # extremes survive
    assert len(xs) <= budget + 2 * (internal + 1) + internal


def test_d4_04_the_plot_keeps_a_single_invalid_frame_as_a_gap(qapp):
    geo = load_geometry(GEOMETRY)
    panel = PreviewPanel(geo)
    panel.resize(360, 700)                                      # a narrow plot: 600 samples must be decimated
    panel.show()
    panel.window_combo.setCurrentIndex(panel.window_combo.findData(60))
    QtTest.QTest.qWait(80)
    frames = [frame(geo, i, state="INVALID" if i == 301 else "VALID") for i in range(600)]
    panel.fs = 10.0
    panel.push(frames)
    panel.refresh()
    xs, ys = panel.curves["CH1"][0].getData()
    assert len(xs) < 600 and np.isnan(ys).any()                # decimated, and the gap survived


# ---- D4-05 ----------------------------------------------------------------------------------------------

def _two_amplitudes(geo, i):
    return frame(geo, i, scale={"CH2": 1.5})


def _ranges(panel, names=("CH1", "CH2", "CH3")):
    return [tuple(round(v, 6) for v in panel.rows[n].plot.getPlotItem().getViewBox().viewRange()[1]) for n in names]


@pytest.mark.parametrize("scale", [None, "full", 1.0])
def test_d4_05_the_lock_gives_every_row_one_range_in_every_scale_policy(qapp, scale):
    geo, panel = panel_with(qapp, [lambda g, i=i: _two_amplitudes(g, i) for i in range(200)])
    panel.scale_combo.setCurrentIndex(panel.scale_combo.findData(scale))
    panel.refresh()
    own = _ranges(panel)
    panel.lock_check.setChecked(True)
    panel.refresh()
    QtTest.QTest.qWait(30)
    locked = _ranges(panel)
    assert len(set(locked)) == 1, (scale, locked)
    if scale is None or scale == "full":
        assert locked[0][1] - locked[0][0] >= max(r[1] - r[0] for r in own) - 1e-6
    panel.close()


def test_d4_05_unlocking_returns_every_row_to_its_own_range(qapp):
    geo, panel = panel_with(qapp, [lambda g, i=i: _two_amplitudes(g, i) for i in range(200)])
    panel.refresh()
    own = _ranges(panel)
    assert own[0] != own[1]
    panel.lock_check.setChecked(True)
    panel.refresh()
    assert len(set(_ranges(panel))) == 1
    panel.lock_check.setChecked(False)
    panel.refresh()
    QtTest.QTest.qWait(30)
    again = _ranges(panel)
    assert again[0] != again[1] and again == own                # not the shared range any more


def test_d4_05_a_mode_change_while_locked_drops_the_old_shared_range(qapp):
    geo, panel = panel_with(qapp, [lambda g, i=i: _two_amplitudes(g, i) for i in range(200)])
    panel.lock_check.setChecked(True)
    panel.refresh()
    hb = _ranges(panel)[0]
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("OD"))
    panel.refresh()
    od = _ranges(panel)
    assert len(set(od)) == 1 and (od[0][1] - od[0][0]) < 0.2 * (hb[1] - hb[0]), (hb, od)
    panel.close()


def test_d4_05_clear_forgets_the_ranges(qapp):
    geo, panel = panel_with(qapp, [lambda g, i=i: _two_amplitudes(g, i) for i in range(200)])
    panel.lock_check.setChecked(True)
    panel.refresh()
    panel.clear()
    assert panel._shared is None and all(row.ylim is None for row in panel.rows.values())
    panel.close()


# ---- D4-06 ----------------------------------------------------------------------------------------------

def _animations(w):
    return len(w.findChildren(QtCore.QAbstractAnimation))


def test_d4_06_repeated_interactions_do_not_accumulate_animation_objects(make_window):
    w = make_window()
    w._go(5)
    QtTest.QTest.qWait(80)

    def burst(n):
        for i in range(n):
            w._go(1 if i % 2 else 5)
            w.section_detail.button.click()
            w.toggle_sidebar()
            w.panel.scroll.scroll_to(0 if i % 2 else 10 ** 6)
            w.connection_chip.set_tone("danger" if i % 2 else "ok")
            w.recording_chip.set_tone("warn" if i % 2 else "neutral")
            QtTest.QTest.qWait(5)

    burst(2)
    warm = _animations(w)
    burst(16)
    assert _animations(w) == warm and warm <= 12, (warm, _animations(w))
    wait_for(lambda: not (w.screens.animating or w.sidebar_animating or w.section_detail.animating), seconds=5)


def test_d4_06_reduce_motion_settles_every_running_animation_immediately(make_window):
    w = make_window()
    w._go(5)
    QtTest.QTest.qWait(300)
    w.section_detail.button.click()                             # a fold
    w.toggle_sidebar()                                          # a sidebar slide (closing)
    w.panel.scroll.scroll_to(10 ** 6)                           # a scroll
    w.connection_chip.set_tone("danger")                        # a colour change
    w._go(0)                                                    # a page fade
    assert w.screens.animating and w.section_detail.animating and w.sidebar_animating
    assert w.panel.scroll._anim is not None and w.connection_chip.animating
    w.action_reduce_motion.setChecked(True)                     # takes effect now, not at the next start
    assert not w.screens.animating and not w.section_detail.animating and not w.sidebar_animating
    assert not w.connection_chip.animating
    assert w.panel.scroll._anim.state() != QtCore.QAbstractAnimation.State.Running
    assert w.screens.currentWidget().graphicsEffect() is None and w.side_scroll.graphicsEffect() is None
    assert not w.side_scroll.isVisible()                        # the closing slide ended in its final state
    assert not w.detail.isVisible()                             # the fold ended folded
    assert w.panel.scroll.verticalScrollBar().value() == w.panel.scroll.verticalScrollBar().maximum()
    QtTest.QTest.qWait(300)
    assert not any(a.state() == QtCore.QAbstractAnimation.State.Running for a in w.findChildren(QtCore.QAbstractAnimation))


# ---- D4-07 ----------------------------------------------------------------------------------------------

def _pixel(plot, t):
    vb = plot.getPlotItem().getViewBox()
    scene = vb.mapViewToScene(QtCore.QPointF(t, 0.0))
    return plot.mapToGlobal(plot.mapFromScene(scene)).x()


@pytest.mark.parametrize("width", [640, 1000, 1500])
def test_d4_07_the_footer_axis_maps_time_to_the_same_pixels_as_the_rows(qapp, width):
    geo, panel = panel_with(qapp, [lambda g, i=i: frame(g, i) for i in range(120)], width=width, height=700)
    for window in (30, 10):
        panel.window_combo.setCurrentIndex(panel.window_combo.findData(window))
        QtTest.QTest.qWait(120)
        for t in (0.0, window / 2, float(window)):
            footer = _pixel(panel.footer, t)
            for name in ("CH1", "CH7", "SC2R"):
                assert abs(_pixel(panel.rows[name].plot, t) - footer) <= 1, (width, window, t, name)
    panel.close()


# ---- D4-08 ----------------------------------------------------------------------------------------------

def test_d4_08_status_text_is_rebuilt_after_a_clear(qapp):
    geo, panel = panel_with(qapp, [lambda g, i=i: frame(g, i) for i in range(40)])
    assert "OK" in panel.rows["CH1"].status.text()
    panel.clear()
    assert panel.rows["CH1"].status.text() == "…" and panel.rows["CH1"].status_key is None
    panel.push([frame(geo, i) for i in range(40)])             # the same quality as before the clear
    assert "OK" in panel.rows["CH1"].status.text()
    panel.reset_for_epoch(("new", 1))                          # the epoch path clears the same way
    panel.push([frame(geo, i) for i in range(5)])
    assert "OK" in panel.rows["CH1"].status.text()
    panel.close()


# ---- D4-09 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tokens", [T.LIGHT, T.DARK], ids=lambda t: t.name)
def test_d4_09_legend_text_and_markers_meet_the_contrast_targets(qapp, tokens):
    geo = load_geometry(GEOMETRY)

    class FakeTheme(QtCore.QObject):
        changed = QtCore.Signal(object)
        motion_changed = QtCore.Signal(bool)
        reduce_motion = True

        def __init__(self, t):
            super().__init__()
            self.tokens = t

        def ms(self, d):
            return 0

    panel = PreviewPanel(geo, FakeTheme(tokens))
    surfaces = [tokens.surface, tokens.surface_raised, tokens.background, tokens.plot_background]
    html = panel.rows["CH1"].legend.text()
    parts = re.findall(r"<span style='color:(#[0-9A-Fa-f]{6})'><b>(━|&nbsp;[^<]+)</b>", html)
    swatches = [c for c, g in parts if g == "━"]
    labels = [c for c, g in parts if g != "━"]
    assert len(swatches) == 2 and len(labels) == 2
    for surface in surfaces:
        for c in labels:
            assert T.contrast(c, surface) >= 4.5, (tokens.name, "legend text", c, surface)
        for c in swatches:
            assert T.contrast(c, surface) >= 3.0, (tokens.name, "legend swatch", c, surface)
    for surface in surfaces:                                    # the warning triangle is drawn and also used as text
        assert T.contrast(tokens.marker_warning, surface) >= 4.5, (tokens.name, "warning marker", surface)
    for marker in (tokens.marker_uncorrected, tokens.marker_clamp):
        for surface in (tokens.surface, tokens.plot_background):
            assert T.contrast(marker, surface) >= 3.0
    for c in re.findall(r"color:(#[0-9A-Fa-f]{6})", panel.legend.text()):
        assert T.contrast(c, tokens.surface) >= 3.0
    panel.close()
