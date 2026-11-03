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

SIM_BANNER = "SIMULATED DEVICE - no real device is connected"
REAL_DEVICE_NOTE = "Real device connection is not available in this version."

_STATE_STYLE = {
    "STOPPED": "color: #1b5e20;",
    "STREAMING": "color: #0d47a1; font-weight: bold;",
    "SAFE_OFF": "color: #b71c1c; font-weight: bold;",
    "FAULTED": "color: #b71c1c; font-weight: bold;",
}


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
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setObjectName("connect_button")
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("disconnect_button")
        note = QtWidgets.QLabel(REAL_DEVICE_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        conn = QtWidgets.QGroupBox("Connection")
        cl = QtWidgets.QGridLayout(conn)
        cl.addWidget(self.device_combo, 0, 0, 1, 2)
        cl.addWidget(self.connect_btn, 1, 0)
        cl.addWidget(self.disconnect_btn, 1, 1)
        cl.addWidget(note, 2, 0, 1, 2)

        # --- status ---
        self.state_label = QtWidgets.QLabel("NOT CONNECTED")
        self.state_label.setObjectName("state_label")
        self.detail_label = QtWidgets.QLabel("")
        self.detail_label.setObjectName("detail_label")
        self.detail_label.setWordWrap(True)
        status = QtWidgets.QGroupBox("Device status")
        sl = QtWidgets.QVBoxLayout(status)
        sl.addWidget(self.state_label)
        sl.addWidget(self.detail_label)

        # --- run control ---
        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setObjectName("start_button")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setObjectName("stop_button")
        self.clear_btn = QtWidgets.QPushButton("Clear fault")
        self.clear_btn.setObjectName("clear_fault_button")
        self.safe_btn = QtWidgets.QPushButton("SAFE OFF")
        self.safe_btn.setObjectName("safe_off_button")
        self.safe_btn.setMinimumHeight(44)
        self.safe_btn.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: bold; }"
            "QPushButton:disabled { background-color: #d9a5a5; color: #fff; }")
        run = QtWidgets.QGroupBox("Run")
        rl = QtWidgets.QGridLayout(run)
        rl.addWidget(self.start_btn, 0, 0)
        rl.addWidget(self.stop_btn, 0, 1)
        rl.addWidget(self.clear_btn, 1, 0, 1, 2)
        rl.addWidget(self.safe_btn, 2, 0, 1, 2)

        # --- settings ---
        self.fs_spin = QtWidgets.QSpinBox()
        self.fs_spin.setObjectName("fs_spin")
        self.fs_spin.setSuffix(" Hz")
        self.fs_apply = QtWidgets.QPushButton("Apply Fs")
        self.fs_apply.setObjectName("fs_apply_button")
        self.led_table = QtWidgets.QTableWidget(LED_CHANNELS, 2)
        self.led_table.setObjectName("led_table")
        self.led_table.setHorizontalHeaderLabels(["LED channel", "Current (mA)"])
        self.led_table.verticalHeader().setVisible(False)
        self.led_table.horizontalHeader().setStretchLastSection(True)
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
        self.all_fill = QtWidgets.QPushButton("Fill all")
        self.all_fill.setObjectName("led_fill_button")
        self.led_apply = QtWidgets.QPushButton("Apply LED currents")
        self.led_apply.setObjectName("led_apply_button")
        self.settings_hint = QtWidgets.QLabel("Settings can only be changed while STOPPED.")
        self.settings_hint.setStyleSheet("color: #555;")
        sets = QtWidgets.QGroupBox("Settings (applied to the device only when you press Apply)")
        gl = QtWidgets.QGridLayout(sets)
        gl.addWidget(QtWidgets.QLabel("Sampling rate"), 0, 0)
        gl.addWidget(self.fs_spin, 0, 1)
        gl.addWidget(self.fs_apply, 0, 2)
        gl.addWidget(self.led_table, 1, 0, 1, 3)
        gl.addWidget(QtWidgets.QLabel("All channels"), 2, 0)
        gl.addWidget(self.all_spin, 2, 1)
        gl.addWidget(self.all_fill, 2, 2)
        gl.addWidget(self.led_apply, 3, 0, 1, 3)
        gl.addWidget(self.settings_hint, 4, 0, 1, 3)

        # --- log ---
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("event_log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(120)
        logbox = QtWidgets.QGroupBox("Event log (every command and state change)")
        ll = QtWidgets.QVBoxLayout(logbox)
        ll.addWidget(self.log)

        self.led_table.setMinimumHeight(250)
        inner = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(inner)
        left.setContentsMargins(0, 0, 4, 0)
        left.addWidget(conn)
        left.addWidget(status)
        left.addWidget(run)
        left.addWidget(sets)
        scroll = QtWidgets.QScrollArea()  # keeps the panel usable on small screens
        scroll.setObjectName("panel_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(logbox)

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
        self.log.appendPlainText(f"{stamp} {tag}{ev.kind}: {ev.text}")

    def refresh(self) -> None:
        c = self.ctl
        connected = c.connected
        state = c.device_state
        stopped = connected and state == ST_STOPPED
        text = c.state_text
        self.state_label.setText(f"State: {text}" + ("  (SIMULATED)" if c.simulated else ""))
        self.state_label.setStyleSheet("font-size: 16px; " + _STATE_STYLE.get(text, "color: #444;"))
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

    def tick(self) -> None:
        """Called by the main window timer: heartbeat + refresh."""
        self.ctl.tick()
        self.refresh()

    def is_streaming(self) -> bool:
        return self.ctl.connected and self.ctl.device_state == ST_STREAMING
