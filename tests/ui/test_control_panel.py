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
    assert len(lines) >= 4 and all("[SIM]" in l for l in lines)  # every line, from the first


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


@pytest.mark.parametrize("state_setup", ["stopped", "streaming", "safe_off", "faulted"])
def test_safe_off_enabled_and_dispatched_in_every_connected_state(win, state_setup):
    from noctinirs_host.control import protocol as P
    btn(win, "connect_button").click()
    if state_setup == "streaming":
        btn(win, "start_button").click()
    elif state_setup == "safe_off":
        btn(win, "safe_off_button").click()
    elif state_setup == "faulted":
        win._simulator.state = P.ST_FAULTED
        win.controller.refresh_status()
        win.panel.refresh()
    assert win.controller.state_text.replace("_", "") == state_setup.replace("_", "").upper()
    b = btn(win, "safe_off_button")
    assert b.isEnabled()
    sent = win.controller.counters["sent"]
    b.click()
    assert win.controller.counters["sent"] > sent
    assert win._simulator.state == P.ST_SAFE_OFF


def test_safe_off_dispatched_even_when_the_reply_is_lost(win):
    btn(win, "connect_button").click()
    win.controller._t.drop_next_replies = 2
    btn(win, "safe_off_button").click()
    assert win._simulator.state == 7  # SAFE_OFF reached the device despite lost replies


def test_led_current_limit_is_the_protocol_maximum_and_says_so(win):
    from noctinirs_host.control.protocol import LED_MAX_MA
    assert all(s.maximum() == LED_MAX_MA == 50 for s in win.panel._led_spins)
    assert "protocol maximum" in win.panel.led_table.toolTip().lower()
    assert "does not report" in win.panel.led_table.toolTip().lower()


def test_theme_switch_changes_style_plot_and_keeps_behaviour(win):
    from noctinirs_host_ui.theme import DARK, LIGHT
    app = QtWidgets.QApplication.instance()
    assert win.theme_name == "light" and LIGHT.surface in app.styleSheet()
    win.set_theme("dark")
    assert win.theme_name == "dark" and DARK.surface in app.styleSheet()
    assert win.plot.backgroundBrush().color().name().lower() == DARK.plot_bg.lower()
    btn(win, "connect_button").click()
    btn(win, "start_button").click()
    assert "STREAMING" in win.panel.state_label.text()
    win.set_theme("light")  # leave the shared application in the default theme
    assert win.plot.backgroundBrush().color().name().lower() == LIGHT.plot_bg.lower()


def test_view_menu_offers_both_themes(win):
    menus = {a.text().replace("&", ""): a for a in win.menuBar().actions()}
    assert "View" in menus
    theme_menu = [a for a in menus["View"].menu().actions() if a.menu()][0].menu()
    assert [a.text().replace("&", "") for a in theme_menu.actions()] == ["Light", "Dark"]
    theme_menu.actions()[1].trigger()
    assert win.theme_name == "dark"
    theme_menu.actions()[0].trigger()
    assert win.theme_name == "light"


def test_collapsible_sections_start_collapsed_and_toggle(win):
    p = win.panel
    assert not p.led_section.body_widget.isVisibleTo(p) and not p.log_section.body_widget.isVisibleTo(p)
    p.led_section.toggle.setChecked(True)
    assert p.led_section.body_widget.isVisibleTo(p)
    assert "LED CURRENT" in p.led_section.toggle.text()


def test_header_keeps_the_always_visible_labels(win):
    banner = win.findChild(QtWidgets.QLabel, "banner")
    notice = win.findChild(QtWidgets.QLabel, "synthetic_notice")
    assert "non-clinical" in banner.text().lower() and "SIMULATED" in notice.text()
    assert banner.isVisibleTo(win) and notice.isVisibleTo(win)


def test_dark_mode_button_toggles_and_stays_in_step_with_the_menu(win):
    b = win.dark_btn
    assert b.text() == "Dark mode: Off" and not b.isChecked() and win.theme_name == "light"
    b.click()
    assert win.theme_name == "dark" and b.text() == "Dark mode: On" and b.isChecked()
    win._theme_actions["light"].trigger()  # menu changes it back; button follows
    assert win.theme_name == "light" and b.text() == "Dark mode: Off" and not b.isChecked()
    b.click()
    b.click()
    assert win.theme_name == "light"


S1_OBJECT_NAMES = {
    QtWidgets.QPushButton: ["connect_button", "disconnect_button", "start_button", "stop_button",
                            "clear_fault_button", "safe_off_button", "fs_apply_button",
                            "led_fill_button", "led_apply_button"],
    QtWidgets.QLabel: ["state_label", "detail_label", "banner", "synthetic_notice", "plot_note"],
    QtWidgets.QSpinBox: ["fs_spin"],
    QtWidgets.QComboBox: ["device_combo"],
    QtWidgets.QTableWidget: ["led_table"],
    QtWidgets.QPlainTextEdit: ["event_log"],
    QtWidgets.QScrollArea: ["panel_scroll"],
}


def test_s1_object_names_are_preserved(win):
    for cls, names in S1_OBJECT_NAMES.items():
        for name in names:
            assert win.findChild(cls, name) is not None, f"{cls.__name__} #{name} missing"


def test_every_object_name_selector_in_the_style_sheet_matches_a_widget(win):
    import re
    from noctinirs_host_ui.theme import LIGHT, build_qss
    wanted = set(re.findall(r"\bQ[A-Za-z]+#([A-Za-z_][A-Za-z0-9_]*)", build_qss(LIGHT, "x", "y")))
    present = {w.objectName() for w in win.findChildren(QtWidgets.QWidget)} | {win.objectName()}
    missing = wanted - present
    assert not missing, f"style sheet selectors without a widget: {sorted(missing)}"
