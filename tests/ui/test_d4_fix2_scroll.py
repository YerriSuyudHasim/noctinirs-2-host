"""H-03 (Codex host review 2026-11-10): direct scroll-bar use must settle on row-aligned stops.

At the accepted 900x600 minimum, single steps, page steps, keyboard steps and the end of a slider drag
must leave three complete rows in view, including when the step crosses the hemisphere divider. Offscreen
Qt only (no native Windows evidence here).
"""

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets

from noctinirs_host_ui.engineering import MIN_WINDOW
from test_d4_fix import fully_visible_rows, make_window, qapp, synthetic_state  # noqa: F401 (fixtures)

A = QtWidgets.QAbstractSlider.SliderAction


@pytest.fixture(params=[False, True], ids=["ordinary", "lost_start"])
def panel(request, make_window):
    w = make_window(frozen_state=True, reduce_motion=True)
    w._go(5)
    w.resize(*MIN_WINDOW)
    synthetic_state(w, request.param)
    QtTest.QTest.qWait(150)
    p = w.panel
    assert p.scroll.verticalScrollBar().maximum() > 0
    return p


def stops(p):
    """Row-aligned positions computed here from the panel (not from the widget under test): the top, every
    row top that the bar can reach, and the bottom-aligned maximum."""
    bar = p.scroll.verticalScrollBar()
    return sorted({min(bar.maximum(), v) for v in p.row_stops()} | {0, bar.maximum()})


def settled(p):
    bar = p.scroll.verticalScrollBar()
    return bar.value() in stops(p) and len(fully_visible_rows(p)) >= 3


def divider_gap_stop(p):
    """The first row whose top is further than one row pitch from the previous row: the row after the
    hemisphere divider band."""
    tops = [p.rows[n].y() for n in p.order]
    pitch = min(b - a for a, b in zip(tops, tops[1:]))
    for a, b in zip(tops, tops[1:]):
        if b - a > pitch + 4:
            return a, b
    pytest.fail("no hemisphere divider between rows")


def test_h03_single_steps_visit_every_row_stop_and_cross_the_divider(panel):
    p = panel
    bar = p.scroll.verticalScrollBar()
    bar.setValue(0)
    seen = [0]
    while bar.value() < bar.maximum():
        bar.triggerAction(A.SliderSingleStepAdd)
        assert settled(p), (bar.value(), fully_visible_rows(p))
        assert bar.value() > seen[-1]
        seen.append(bar.value())
    before, after = divider_gap_stop(p)
    expected = stops(p)
    assert seen == expected and before in seen and after in seen
    back = [bar.value()]
    while bar.value() > 0:
        bar.triggerAction(A.SliderSingleStepSub)
        assert settled(p)
        back.append(bar.value())
    assert back == seen[::-1]                                  # one stop at a time, back to the top


def test_h03_page_steps_settle_on_stops(panel):
    p = panel
    bar = p.scroll.verticalScrollBar()
    bar.setValue(0)
    last = 0
    while bar.value() < bar.maximum():
        bar.triggerAction(A.SliderPageStepAdd)
        assert settled(p) and bar.value() > last
        assert bar.value() - last <= max(bar.pageStep(), p.row_height() + 64)   # never skips more than a page
        last = bar.value()
    while bar.value() > 0:
        bar.triggerAction(A.SliderPageStepSub)
        assert settled(p) and bar.value() < last
        last = bar.value()


@pytest.mark.parametrize("key", [QtCore.Qt.Key.Key_Down, QtCore.Qt.Key.Key_PageDown])
def test_h03_keyboard_on_the_scroll_bar_and_the_area_settles_on_stops(panel, key):
    p = panel
    bar = p.scroll.verticalScrollBar()
    for target in (bar, p.scroll):
        bar.setValue(0)
        for _ in range(3):
            QtTest.QTest.keyClick(target, key)
            assert settled(p), (target, key, bar.value())
        assert bar.value() > 0
        QtTest.QTest.keyClick(target, QtCore.Qt.Key.Key_Up if key == QtCore.Qt.Key.Key_Down else QtCore.Qt.Key.Key_PageUp)
        assert settled(p)


def test_h03_a_slider_drag_ends_on_the_nearest_stop_including_across_the_divider(panel):
    p = panel
    bar = p.scroll.verticalScrollBar()
    before, after = divider_gap_stop(p)
    h = p.row_height()
    for position in (h // 2, h // 2 + 3, before + (after - before) // 2 - 1, before + (after - before) // 2 + 1,
                     after + h // 3, bar.maximum() - 3):
        position = max(0, min(bar.maximum(), position))
        bar.setSliderDown(True)
        bar.setSliderPosition(position)
        assert bar.value() == position                     # tracking: the view follows the drag
        bar.setSliderDown(False)                           # release
        nearest = min(stops(p), key=lambda v: (abs(v - position), v))
        assert bar.value() == nearest and settled(p), (position, bar.value(), fully_visible_rows(p))


def test_h03_without_stops_the_scroll_bar_keeps_its_plain_steps(qapp):   # CONTROL
    from noctinirs_host_ui.widgets import SmoothScrollArea
    area = SmoothScrollArea()
    inner = QtWidgets.QWidget()
    inner.setMinimumHeight(2000)
    area.setWidget(inner)
    area.resize(200, 300)
    area.show()
    QtTest.QTest.qWait(50)
    bar = area.verticalScrollBar()
    bar.triggerAction(A.SliderSingleStepAdd)
    assert bar.value() == 48
    area.close()
