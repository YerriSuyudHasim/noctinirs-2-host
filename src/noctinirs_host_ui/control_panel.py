"""S1 control panel: connect, start/stop, Safe Off, Fs, LED current, status, event log.

Talks only to a `SessionController` (UI-independent core). In S1 the controller is
connected to the in-process simulator, so everything shown here is SIMULATED.
"""

from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from noctinirs_host.control.protocol import (
    LED_CHANNELS, LED_MAX_MA, ST_FAULTED, ST_SAFE_OFF, ST_STOPPED, ST_STREAMING,
)
from noctinirs_host.control.session import Event, SessionController

from .theme import SPACE

SIM_BANNER = "SIMULATED DEVICE - no real device is connected"
REAL_DEVICE_NOTE = "Real device connection is not available in this version."

_STATE_TONE = {"STOPPED": "ok", "STREAMING": "info", "SAFE_OFF": "danger", "FAULTED": "danger"}


def _button(text: str, name: str, role: str | None = None) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setObjectName(name)
    b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    if role:
        b.setProperty("role", role)
    return b


def _muted(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _card(title: str) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    frame = QtWidgets.QFrame()
    frame.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(frame)
    lay.setContentsMargins(SPACE + 4, SPACE, SPACE + 4, SPACE + 2)
    lay.setSpacing(10)
    head = QtWidgets.QLabel(title.upper())
    head.setObjectName("sectionTitle")
    lay.addWidget(head)
    return frame, lay


class _Collapsible(QtWidgets.QWidget):
    """A quiet section header that shows or hides its body."""

    def __init__(self, title: str, *, expanded: bool, summary: QtWidgets.QLabel | None = None) -> None:
        super().__init__()
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._title = title
        self.body_widget = QtWidgets.QWidget()
        self.body = QtWidgets.QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(0, 4, 0, 0)
        self.body.setSpacing(8)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.toggle)
        if summary is not None:
            summary.setWordWrap(False)
            head.addWidget(summary, 1)
        else:
            head.addStretch(1)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.body_widget)
        self.toggle.toggled.connect(self.set_expanded)
        self.toggle.setChecked(expanded)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        self.toggle.setText(("\u25BE " if on else "\u25B8 ") + self._title)
        self.body_widget.setVisible(on)


class ControlPanel(QtWidgets.QWidget):
    changed = QtCore.Signal()  # emitted after every refresh (UI can follow the state)

    def __init__(self, controller: SessionController, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl = controller
        self.ctl.listeners.append(self._on_event)

        # --- connection ---
        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.setObjectName("device_combo")
        self.device_combo.addItem("Simulated device (built-in)")
        self.connect_btn = _button("Connect", "connect_button", "primary")
        self.disconnect_btn = _button("Disconnect", "disconnect_button")
        note = _muted(REAL_DEVICE_NOTE)
        conn, cl = _card("Connection")
        cl.addWidget(self.device_combo)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.connect_btn, 1)
        row.addWidget(self.disconnect_btn, 1)
        cl.addLayout(row)
        cl.addWidget(note)

        # --- status ---
        self.state_label = QtWidgets.QLabel("NOT CONNECTED")
        self.state_label.setObjectName("state_label")  # S1 name, kept
        self.detail_label = _muted("")
        self.detail_label.setObjectName("detail_label")
        status, sl = _card("Device status")
        sl.addWidget(self.state_label)
        sl.addWidget(self.detail_label)

        # --- run control ---
        self.start_btn = _button("Start", "start_button", "primary")
        self.stop_btn = _button("Stop", "stop_button")
        self.clear_btn = _button("Clear fault", "clear_fault_button")
        self.safe_btn = _button("SAFE OFF", "safe_off_button", "danger")
        self.safe_btn.setMinimumHeight(46)
        run, rl = _card("Run")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.start_btn, 1)
        row.addWidget(self.stop_btn, 1)
        rl.addLayout(row)
        rl.addWidget(self.clear_btn)
        rl.addSpacing(4)
        rl.addWidget(self.safe_btn)

        # --- settings ---
        self.fs_spin = QtWidgets.QSpinBox()
        self.fs_spin.setObjectName("fs_spin")
        self.fs_spin.setSuffix(" Hz")
        self.fs_apply = _button("Apply Fs", "fs_apply_button")
        self.led_table = QtWidgets.QTableWidget(LED_CHANNELS, 2)
        self.led_table.setObjectName("led_table")
        self.led_table.setHorizontalHeaderLabels(["LED channel", "Current (mA)"])
        self.led_table.verticalHeader().setVisible(False)
        self.led_table.horizontalHeader().setStretchLastSection(True)
        self.led_table.setShowGrid(False)
        self.led_table.setAlternatingRowColors(True)
        self.led_table.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.led_table.verticalHeader().setDefaultSectionSize(36)
        self._led_spins: list[QtWidgets.QSpinBox] = []
        for ch in range(LED_CHANNELS):
            item = QtWidgets.QTableWidgetItem(str(ch))
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.led_table.setItem(ch, 0, item)
            spin = QtWidgets.QSpinBox()
            spin.setRange(0, LED_MAX_MA)
            self.led_table.setCellWidget(ch, 1, spin)
            self._led_spins.append(spin)
        # The protocol (ADR-0004) fixes 0..50 mA. The device does not report a tighter LED
        # limit yet (INFO/get_config carry none), so none can be applied here; if a future
        # firmware reports one, the core must expose it first. The Fs limits, by contrast,
        # do come from the device (INFO fs_min/fs_max).
        self.led_table.setToolTip(
            f"Range 0..{LED_MAX_MA} mA is the protocol maximum. The device does not report a "
            "lower limit yet; it rejects values it cannot accept.")
        self.all_spin = QtWidgets.QSpinBox()
        self.all_spin.setRange(0, LED_MAX_MA)
        self.all_fill = _button("Fill all", "led_fill_button")
        self.led_apply = _button("Apply LED currents", "led_apply_button")
        self.settings_hint = _muted("Settings can only be changed while STOPPED.")
        sets, gl = _card("Settings")
        fs_row = QtWidgets.QHBoxLayout()
        fs_row.setSpacing(8)
        fs_row.addWidget(QtWidgets.QLabel("Sampling rate"))
        fs_row.addStretch(1)
        fs_row.addWidget(self.fs_spin)
        fs_row.addWidget(self.fs_apply)
        gl.addLayout(fs_row)
        self.led_section = _Collapsible("SIMULATED LED CURRENT (DEMO)", expanded=False)
        self.led_section.body.addWidget(self.led_table)
        self.led_table.setMinimumHeight(260)
        fill_row = QtWidgets.QHBoxLayout()
        fill_row.setSpacing(8)
        fill_row.addWidget(QtWidgets.QLabel("All channels"))
        fill_row.addStretch(1)
        fill_row.addWidget(self.all_spin)
        fill_row.addWidget(self.all_fill)
        self.led_section.body.addLayout(fill_row)
        self.led_section.body.addWidget(self.led_apply)
        gl.addWidget(self.led_section)
        gl.addWidget(self.settings_hint)

        # --- log ---
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("event_log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(120)
        self.last_event = _muted("No events yet")
        self.last_event.setObjectName("last_event")
        self.last_event.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self.log_section = _Collapsible("EVENT LOG", expanded=False, summary=self.last_event)
        self.log_section.body.addWidget(self.log)

        inner = QtWidgets.QWidget()
        inner.setObjectName("root")
        left = QtWidgets.QVBoxLayout(inner)
        left.setContentsMargins(0, 0, 6, 0)
        left.setSpacing(SPACE)
        for w in (conn, status, run, sets):
            left.addWidget(w)
        left.addStretch(1)
        scroll = QtWidgets.QScrollArea()  # keeps the panel usable on small screens
        scroll.setObjectName("panel_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(self.log_section)

        self.connect_btn.clicked.connect(self.on_connect)
        self.disconnect_btn.clicked.connect(self.on_disconnect)
        self.start_btn.clicked.connect(lambda: self._run(self.ctl.start))
        self.stop_btn.clicked.connect(lambda: self._run(self.ctl.stop))
        self.clear_btn.clicked.connect(lambda: self._run(self.ctl.clear_fault))
        self.safe_btn.clicked.connect(lambda: self._run(self.ctl.safe_off))
        self.fs_apply.clicked.connect(self.on_apply_fs)
        self.all_fill.clicked.connect(self.on_fill_all)
        self.led_apply.clicked.connect(self.on_apply_led)
        self._settings_loaded = False
        self.refresh()

    # --- actions ----------------------------------------------------------
    def _run(self, fn) -> None:
        fn()
        self.refresh()

    def on_connect(self) -> None:
        self.ctl.connect()
        self._settings_loaded = False
        self.refresh()

    def on_disconnect(self) -> None:
        self.ctl.disconnect()
        self._settings_loaded = False
        self.refresh()

    def on_apply_fs(self) -> None:
        self.ctl.set_fs(self.fs_spin.value())
        self._settings_loaded = False  # re-read what the device really holds
        self.refresh()

    def on_fill_all(self) -> None:
        for spin in self._led_spins:
            spin.setValue(self.all_spin.value())

    def on_apply_led(self) -> None:
        current = self.ctl.settings.current_ma
        for ch, spin in enumerate(self._led_spins):
            if ch < len(current) and spin.value() != current[ch]:
                self.ctl.set_led_current(ch, spin.value())
        self._settings_loaded = False
        self.refresh()

    # --- refresh from controller ---------------------------------------------
    def _on_event(self, ev: Event) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime()) + f".{int(ev.t * 1000) % 1000:03d}"
        tag = "[SIM] " if ev.simulated else ""
        line = f"{stamp} {tag}{ev.kind}: {ev.text}"
        self.log.appendPlainText(line)
        self.last_event.setText(f"{ev.kind}: {ev.text}")

    def refresh(self) -> None:
        c = self.ctl
        connected = c.connected
        state = c.device_state
        stopped = connected and state == ST_STOPPED
        text = c.state_text
        self.state_label.setText(text + ("  \u00B7  SIMULATED" if c.simulated else ""))
        self._set_tone(self.state_label, _STATE_TONE.get(text, "muted"))
        bits = []
        if connected:
            bits.append(f"session {c.session_id}" if c.session_id else "no active session")
            if c.fault_code:
                bits.append(f"fault code 0x{c.fault_code:04X}")
            n = c.counters
            bits.append(f"sent {n['sent']}, retries {n['retries']}, timeouts {n['timeouts']}")
        if c.last_end and state == ST_STOPPED:
            bits.append(f"last run ended: {c.last_end}")
        self.detail_label.setText("\n".join(bits))

        self.device_combo.setEnabled(not connected)
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.start_btn.setEnabled(stopped)
        self.stop_btn.setEnabled(connected and state == ST_STREAMING)
        self.clear_btn.setEnabled(connected and state in (ST_SAFE_OFF, ST_FAULTED))
        self.safe_btn.setEnabled(connected)
        for w in (self.fs_spin, self.fs_apply, self.led_table, self.all_spin, self.all_fill,
                  self.led_apply):
            w.setEnabled(stopped)
        self.settings_hint.setVisible(connected and not stopped)

        if connected and not self._settings_loaded:
            s = c.settings
            if s.fs_min is not None and s.fs_max is not None:
                self.fs_spin.setRange(s.fs_min, s.fs_max)
            if s.fs_hz is not None:
                self.fs_spin.setValue(s.fs_hz)
            for ch, v in enumerate(s.current_ma):
                self._led_spins[ch].setValue(v)
            self._settings_loaded = True
        self.changed.emit()

    @staticmethod
    def _set_tone(label: QtWidgets.QLabel, tone: str) -> None:
        if label.property("tone") != tone:
            label.setProperty("tone", tone)
            label.style().unpolish(label)
            label.style().polish(label)

    def tick(self) -> None:
        """Called by the main window timer: heartbeat + refresh."""
        self.ctl.tick()
        self.refresh()

    def is_streaming(self) -> bool:
        return self.ctl.connected and self.ctl.device_state == ST_STREAMING
