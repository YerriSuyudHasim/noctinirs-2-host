"""S1 control panel tests (offscreen). The device is the in-process simulator."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from noctinirs_host_ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._timer.stop()
    w._ui_timer.stop()  # tests drive everything explicitly
    yield w
    w.close()


def btn(w, name):
    return w.findChild(QtWidgets.QPushButton, name)


def test_initial_state_everything_off_but_connect(win):
    assert btn(win, "connect_button").isEnabled()
    for n in ("disconnect_button", "start_button", "stop_button", "safe_off_button",
              "fs_apply_button", "led_apply_button"):
        assert not btn(win, n).isEnabled(), n
    assert "NOT CONNECTED" in win.panel.state_label.text()


def test_connect_loads_device_values_and_limits(win):
    btn(win, "connect_button").click()
    p = win.panel
    assert "STOPPED" in p.state_label.text() and "SIMULATED" in p.state_label.text()
    assert (p.fs_spin.minimum(), p.fs_spin.maximum(), p.fs_spin.value()) == (2, 10, 5)
    assert all(s.value() == 30 for s in p._led_spins)
    assert btn(win, "start_button").isEnabled() and btn(win, "safe_off_button").isEnabled()
    assert not btn(win, "stop_button").isEnabled()


def test_start_stop_and_settings_lock_while_streaming(win):
    btn(win, "connect_button").click()
    btn(win, "start_button").click()
    p = win.panel
    assert "STREAMING" in p.state_label.text()
    assert btn(win, "stop_button").isEnabled() and not btn(win, "start_button").isEnabled()
    assert not p.fs_apply.isEnabled() and not p.led_table.isEnabled()
    assert p.settings_hint.isVisibleTo(p)
    btn(win, "stop_button").click()
    assert "STOPPED" in p.state_label.text() and p.fs_apply.isEnabled()


def test_apply_fs_changes_device_and_logs_old_and_new(win):
    btn(win, "connect_button").click()
    win.panel.fs_spin.setValue(9)
    btn(win, "fs_apply_button").click()
    assert win.controller.settings.fs_hz == 9 and win._simulator.fs_hz == 9
    assert "set_fs 5 -> 9 Hz" in win.panel.log.toPlainText()


def test_apply_led_sends_only_changed_channels(win):
    btn(win, "connect_button").click()
    sent = win.controller.counters["sent"]
    win.panel._led_spins[4].setValue(11)
    win.panel._led_spins[7].setValue(0)
    btn(win, "led_apply_button").click()
    assert win.controller.counters["sent"] == sent + 2
    assert win._simulator.current_ma[4] == 11 and win._simulator.current_ma[7] == 0
    assert win._simulator.current_ma[0] == 30


def test_fill_all_only_edits_the_table_until_applied(win):
    btn(win, "connect_button").click()
    sent = win.controller.counters["sent"]
    win.panel.all_spin.setValue(20)
    btn(win, "led_fill_button").click()
    assert all(s.value() == 20 for s in win.panel._led_spins)
    assert win.controller.counters["sent"] == sent  # nothing sent yet
    assert win._simulator.current_ma == [30] * 20


def test_safe_off_latches_until_clear_fault(win):
    btn(win, "connect_button").click()
    btn(win, "start_button").click()
    btn(win, "safe_off_button").click()
    p = win.panel
    assert "SAFE_OFF" in p.state_label.text()
    assert btn(win, "clear_fault_button").isEnabled() and not btn(win, "start_button").isEnabled()
    btn(win, "clear_fault_button").click()
    assert "STOPPED" in p.state_label.text()


def test_link_loss_is_shown_to_the_user(win):
    btn(win, "connect_button").click()
    btn(win, "start_button").click()
    now = [win._simulator._clock()]
    win._simulator._clock = lambda: now[0]
    win.controller._clock = lambda: now[0]
    now[0] += 2.0  # past owner silence
    win._simulator.poll()
    now[0] += 4.0  # past grace -> simulator ends the stream (LINK_LOSS)
    win.panel.tick()  # heartbeat is due; device answers INVALID_STATE
    assert "STOPPED" in win.panel.state_label.text()
    assert "LINK_LOSS" in win.panel.detail_label.text()
    assert "no longer streaming" in win.panel.log.toPlainText()


def test_disconnect_returns_to_initial_state(win):
    btn(win, "connect_button").click()
    btn(win, "disconnect_button").click()
    assert btn(win, "connect_button").isEnabled() and not btn(win, "start_button").isEnabled()
    assert "NOT CONNECTED" in win.panel.state_label.text()


def test_every_log_line_is_marked_simulated(win):
    btn(win, "connect_button").click()
    btn(win, "start_button").click()
    lines = [l for l in win.panel.log.toPlainText().splitlines() if l.strip()]
    assert lines and all("[SIM]" in l for l in lines[2:])


def test_demo_plot_moves_only_while_streaming(win):
    btn(win, "connect_button").click()
    win._on_timer()
    assert len(win._t) == 0
    btn(win, "start_button").click()
    win._on_timer()
    win._on_timer()
    assert len(win._t) == 2


def test_real_device_is_not_offered(win):
    assert win.panel.device_combo.count() == 1
    assert "Simulated" in win.panel.device_combo.itemText(0)
    texts = [l.text() for l in win.panel.findChildren(QtWidgets.QLabel)]
    assert any("Real device connection is not available" in t for t in texts)
