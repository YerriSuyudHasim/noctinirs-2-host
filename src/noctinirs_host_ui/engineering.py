"""Stage D desktop wizard and session worker."""

from __future__ import annotations

import argparse
from queue import Empty, Queue
import re
import sys
import time

import PySide6
from PySide6 import QtCore, QtWidgets

from noctinirs_host import __version__
from noctinirs_host.engineering import EngineeringRun, RecordingTarget, UNKNOWN
from noctinirs_host.geometry import load_geometry

from .credits import APP_NAME, about_text
from .resources import geometry_path

PAGES = ("Welcome", "Connection", "ENGINEERING configuration", "Recording & provenance",
         "Readiness", "Acquisition")
STYLE = """
QWidget { color: #102f4b; font-size: 14px; }
QWidget#root, QScrollArea { background: #f4f8fc; border: 0; }
QWidget#page { background: #ffffff; border: 1px solid #cfe0ef; border-radius: 10px; }
QLabel#banner, QLabel#step, QLabel#detail, QLabel#feedback {
    background: #eef6ff; border: 1px solid #cfe0ef; border-radius: 8px; padding: 8px;
}
QLabel#banner { font-size: 17px; font-weight: bold; }
QLabel#title { font-size: 24px; font-weight: bold; }
QLabel#error { color: #992231; }
QLineEdit, QComboBox, QPlainTextEdit, QPushButton {
    background: #ffffff; border: 1px solid #cfe0ef; border-radius: 6px; padding: 6px 10px;
}
QPushButton#primary { background: #1975cf; color: #ffffff; font-weight: bold; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 2px solid #1975cf; }
QPushButton:hover { border-color: #1975cf; }
QPushButton:disabled, QPushButton#primary:disabled { background: #eef6ff; color: #526a80; }
"""


class SessionWorker(QtCore.QThread):
    """A queue gives the session one owner, including periodic work and cleanup."""

    updated = QtCore.Signal(object, str)

    def __init__(self, geometry, host_sha=None, run_factory=EngineeringRun, parent=None):
        super().__init__(parent)
        self._run_args = (geometry, host_sha)
        self._run_factory = run_factory
        self._commands = Queue()

    def submit(self, action, *args):
        self._commands.put((action, args))

    def run(self):
        run = self._run_factory(*self._run_args)
        next_update = 0.0
        while not run.finished:
            action = ""
            try:
                action, args = self._commands.get(timeout=0.02)
            except Empty:
                pass
            if action:
                run.error = ""
                try:
                    getattr(run, action)(*args)
                except (ValueError, OSError) as exc:
                    run.fail(str(exc))
            run.tick()
            now = time.monotonic()
            if action or now >= next_update or run.finished:
                self.updated.emit(run.snapshot(), action)
                next_update = now + 0.2


def _label(text="", name="detail"):
    label = QtWidgets.QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _value(value):
    return UNKNOWN if value is None else str(value)


class EngineeringWindow(QtWidgets.QMainWindow):
    def __init__(self, geometry, host_sha=None, *, run_factory=EngineeringRun):
        super().__init__()
        self.setWindowTitle("NoctiNIRS 2 Host — RAW Engineering")
        self.resize(880, 750)
        self.state = {}
        self._pending = ""
        self._closing_window = False
        self._worker_finished = False
        self._desired_fs = None
        self._rates = ()
        self._target = None
        self._last_log = ()
        self._recovery_buttons = []
        self.worker = SessionWorker(geometry, host_sha, run_factory, self)
        self.worker.updated.connect(self._update)
        self.worker.finished.connect(self._closed_worker)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self._about)

        root = QtWidgets.QWidget(objectName="root")
        root.setStyleSheet(STYLE)
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(_label("NoctiNIRS 2   |   ENGINEERING / NON-SCIENCE", "banner"))
        self.step = _label(name="step")
        layout.addWidget(self.step)
        self.pages = QtWidgets.QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.feedback = _label(name="feedback")
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.error = _label(name="error")
        self.error.hide()
        layout.addWidget(self.error)
        self._form_error = ""
        nav = QtWidgets.QHBoxLayout()
        layout.addLayout(nav)
        self.back = self._button("Back", self._back)
        nav.addWidget(self.back)
        nav.addStretch()
        self.next = self._button("Continue", self._advance, primary=True)
        nav.addWidget(self.next)

        page = self._page(PAGES[0])
        page.addWidget(_label("Set up a UDP session, view RAW ADC counts and optionally archive the "
                              "original datagrams. Use the local RAW fake to practise offline."))
        page.addWidget(_label("Future processing · MBLL, DPF and short separation\n"
                              "Inactive — not applied to RAW ENGINEERING."))
        self._connection_page()
        self._config_page()
        self._recording_page(host_sha, geometry)
        page = self._page(PAGES[4])
        self.summary = _label()
        page.addWidget(self.summary)
        page.addWidget(_label("Opening the panel sends no START. Arm RAW recording before pressing "
                              "Start acquisition if you want to archive the entire run."))
        self._acquisition_page()
        self.pages.setCurrentIndex(0)
        self._render()
        self.worker.start()

    def _page(self, title):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        page = QtWidgets.QWidget(objectName="page")
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.addWidget(_label(title, "title"))
        body = QtWidgets.QVBoxLayout()
        outer.addLayout(body)
        outer.addStretch()
        return body

    @staticmethod
    def _button(text, slot, *, primary=False):
        button = QtWidgets.QPushButton(text)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(slot)
        return button

    def _connection_page(self):
        page = self._page(PAGES[1])
        page.addWidget(_label("For a physical device, connect the PC to its Wi-Fi manually and enter "
                              "the IPv4 endpoint supplied for the bench."))
        form = QtWidgets.QFormLayout()
        page.addLayout(form)
        self.source = QtWidgets.QComboBox()
        self.source.addItems(["Local RAW fake (simulation)", "UDP device endpoint"])
        self.source.currentIndexChanged.connect(self._render)
        self.address = QtWidgets.QLineEdit()
        self.port = QtWidgets.QLineEdit()
        form.addRow("Source", self.source)
        form.addRow("Device IPv4 address", self.address)
        form.addRow("UDP port", self.port)
        row = QtWidgets.QHBoxLayout()
        self.connect_button = self._button("Connect", self._connect, primary=True)
        self.disconnect_button = self._button("Disconnect", self._disconnect)
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        page.addLayout(row)
        self.connection = _label()
        page.addWidget(self.connection)
        self._recovery_row(page)

    def _config_page(self):
        page = self._page(PAGES[2])
        self.capability = _label()
        page.addWidget(self.capability)
        form = QtWidgets.QFormLayout()
        page.addLayout(form)
        self.fs = QtWidgets.QComboBox()
        self.fs.addItem("Choose an advertised rate", None)
        self.fs.currentIndexChanged.connect(self._select_fs)
        form.addRow("Nominal optical Fs", self.fs)
        self.apply = self._button("Apply ENGINEERING and Fs",
                                  lambda: self._submit("configure", self._desired_fs), primary=True)
        page.addWidget(self.apply)
        page.addWidget(_label("ENGINEERING and Fs can be set while STOPPED. The rate list comes "
                              "from the connected peer. Optical Fs and RAW ADC rate are separate."))

    def _recording_page(self, host_sha, geometry):
        page = self._page(PAGES[3])
        form = QtWidgets.QFormLayout()
        page.addLayout(form)
        self.folder = QtWidgets.QLineEdit()
        self.stem = QtWidgets.QLineEdit()
        self.firmware = QtWidgets.QLineEdit()
        self.firmware.setPlaceholderText("Optional · 40 hexadecimal characters")
        browse = self._button("Browse…", self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow("Existing folder", row)
        form.addRow("New file stem", self.stem)
        form.addRow("Operator-provided firmware SHA", self.firmware)
        self.identity = _label()
        page.addWidget(self.identity)
        page.addWidget(_label(f"Host {__version__} · supplied host SHA {_value(host_sha)}\n"
                              f"Geometry map {geometry.geometry_map_id} · {geometry.status}\n"
                              "A declared SHA is saved separately from the peer's reported INFO. "
                              "Each recording reserves a new set of files."))

    def _acquisition_page(self):
        page = self._page(PAGES[5])
        self.device = _label()
        self.recording = _label()
        page.addWidget(self.device)
        page.addWidget(self.recording)
        row = QtWidgets.QHBoxLayout()
        self.start_button = self._button("Start acquisition", lambda: self._submit("start"), primary=True)
        self.stop_button = self._button("Stop acquisition", lambda: self._submit("stop"))
        self.arm_button = self._button("Arm RAW recording", lambda: self._submit("arm", self._target))
        self.finalize_button = self._button("Finalize recording", lambda: self._submit("finalize"))
        for button in (self.arm_button, self.start_button, self.stop_button, self.finalize_button):
            row.addWidget(button)
        page.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.diag_button = self._button("Refresh firmware diagnostics", lambda: self._submit("diagnostics"))
        self.panel_disconnect = self._button("Disconnect", self._disconnect)
        row.addWidget(self.diag_button)
        row.addWidget(self.panel_disconnect)
        row.addStretch()
        page.addLayout(row)
        self._recovery_row(page)
        self.raw = _label()
        self.diagnostics = _label()
        page.addWidget(self.raw)
        page.addWidget(self.diagnostics)
        self.log_view = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log_view.setMinimumHeight(112)
        self.log_view.setAccessibleName("Recent session log, UTC")
        page.addWidget(self.log_view)

    def _recovery_row(self, page):
        row = QtWidgets.QHBoxLayout()
        check = self._button("Check START outcome", lambda: self._submit("check_start_outcome"))
        safe = self._button("SAFE_OFF…", self._safe_off)
        row.addWidget(check)
        row.addWidget(safe)
        row.addStretch()
        page.addLayout(row)
        self._recovery_buttons.append((check, safe))

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Recording folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _about(self):
        QtWidgets.QMessageBox.about(self, f"About {APP_NAME}",
                                    about_text(__version__, QtCore.qVersion(), PySide6.__version__))

    def _connect(self):
        self._submit("connect", self.source.currentIndex() == 0, self.address.text(), self.port.text())

    def _disconnect(self):
        if self.state.get("session") or self.state.get("recording_open"):
            answer = QtWidgets.QMessageBox.question(
                self, "Disconnect session",
                "Disconnect will request STOP for the owned session and finalize the recorder. "
                "If the peer cannot confirm STOP, the cleanup result will say so. Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._submit("disconnect")

    def _safe_off(self):
        answer = QtWidgets.QMessageBox.question(
            self, "Request SAFE_OFF",
            "SAFE_OFF can interrupt another host's acquisition. It can affect the device "
            "regardless of session owner and can latch SAFE_OFF. A device reply reports firmware "
            "state. Physical LED state requires separate verification. Send SAFE_OFF?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._submit("safe_off")

    def _submit(self, action, *args):
        if self._pending or self._closing_window:
            return
        self._form_error = ""
        self._pending = action
        self.worker.submit(action, *args)
        self._render()

    def _select_fs(self):
        self._desired_fs = self.fs.currentData()
        self._form_error = ""
        self._render()

    def _config_ready(self):
        s = self.state
        return s.get("stopped") and s.get("configured") and s.get("mode") == "ENGINEERING" \
            and self._desired_fs in s.get("allowed", ()) and self._desired_fs == s.get("fs")

    def _blockers(self):
        s = self.state
        problems = []
        if s.get("start_uncertain"):
            problems.append("START outcome unresolved. Use the recovery controls.")
        elif not s.get("connected"):
            problems.append("Connect to a peer.")
        elif not s.get("stopped"):
            problems.append("The device must report STOPPED with no owned stream.")
        elif not self._config_ready():
            problems.append("Apply ENGINEERING and a currently advertised Fs.")
        if s.get("checked_target") != self._target or self._target is None:
            problems.append("Check an unused recording destination.")
        return problems

    def _advance(self):
        if self._worker_finished:
            self.close()
            return
        index = self.pages.currentIndex()
        self._form_error = ""
        try:
            if index == 1 and not self.state.get("connected"):
                raise ValueError("Connect to a peer before continuing.")
            if index == 2 and not self._config_ready():
                raise ValueError("Choose an advertised Fs and apply ENGINEERING before continuing.")
            if index == 3:
                self._target = RecordingTarget.parse(self.folder.text(), self.stem.text(), self.firmware.text())
                self._submit("check_destination", self._target)
            if index == 4 and self._blockers():
                raise ValueError("Resolve the readiness issues shown above.")
        except ValueError as exc:
            self._form_error = str(exc)
        else:
            self.pages.setCurrentIndex(index + 1)
        self._render()

    def _back(self):
        self._form_error = ""
        self.pages.setCurrentIndex(self.pages.currentIndex() - 1)
        self._render()

    @QtCore.Slot(object, str)
    def _update(self, state, action):
        self.state = state
        if action and self._pending == action:
            self._pending = ""
        self._render()

    def _render(self, *_):
        s = self.state
        connected = s.get("connected", False)
        uncertain = s.get("start_uncertain", False)
        busy = bool(self._pending or s.get("closing") or self._closing_window)
        disk = s.get("disk_busy", False)
        recorder_open = s.get("recording_open", False)
        index = self.pages.currentIndex()
        self.step.setText(f"{index + 1} / {len(PAGES)}   •   {PAGES[index]}")
        self.back.setEnabled(index > 0 and not (busy or disk or recorder_open or s.get("session")))
        self.next.setVisible(index < 5 or self._worker_finished)
        self.next.setEnabled(self._worker_finished or not (busy or disk))
        self.next.setText("Close application" if self._worker_finished else
                          ("Open acquisition panel" if index == 4 else "Continue"))
        feedback = "Working: " + self._pending.replace("_", " ") if self._pending else s.get("notice", "")
        if s.get("cleanup"):
            feedback = "\n".join(part for part in (s["cleanup"], feedback) if part and part != "")
        self.feedback.setText(feedback)
        self.feedback.setVisible(bool(feedback))
        errors = "\n".join(part for part in (self._form_error, s.get("error")) if part)
        self.error.setText(errors)
        self.error.setVisible(bool(errors))

        source = "Peer reports SIMULATED" if s.get("simulated") else (
            "Peer reports non-simulated" if s.get("simulated") is False else "Peer type unknown")
        report = s.get("device_state", "DISCONNECTED")
        device = (f"{s.get('endpoint') or 'Endpoint not connected'}  •  {source}\n"
                  f"Last firmware state  {report}  |  Boot {_value(s.get('boot'))}"
                  f"  |  Owned session {_value(s.get('session'))}  |  Fault {_value(s.get('fault'))}\n"
                  "Physical LED state is not measured by the host.")
        if uncertain:
            wait = s["start_wait"]
            device += ("\nSTART OUTCOME UNRESOLVED · lease-refresh traffic suspended. "
                       "The last report does not establish the START outcome.\n")
            device += f"Quiet window: {wait:.1f} s before an explicit check." if wait > 0 else (
                "An explicit check is available. Elapsed time alone does not confirm STOP.")
        self.connection.setText(device)
        self.device.setText(device)
        self.source.setEnabled(not (connected or busy or recorder_open))
        fields_enabled = self.source.currentIndex() == 1 and self.source.isEnabled()
        self.address.setEnabled(fields_enabled)
        self.port.setEnabled(fields_enabled)
        self.connect_button.setEnabled(not (connected or uncertain or busy or recorder_open or disk))
        for button in (self.disconnect_button, self.panel_disconnect):
            button.setEnabled(connected and not busy)
        for check, safe in self._recovery_buttons:
            check.setVisible(uncertain)
            check.setEnabled(uncertain and not busy and s.get("start_wait", 0) <= 0)
            safe.setEnabled((connected or uncertain) and not busy)

        rates = s.get("allowed", ())
        if rates != self._rates:
            self._rates = rates
            with QtCore.QSignalBlocker(self.fs):
                self.fs.clear()
                placeholder = ("Choose an advertised rate" if self._desired_fs is None else
                               f"Previously selected {self._desired_fs} Hz is unavailable")
                self.fs.addItem(placeholder, None)
                for rate in rates:
                    self.fs.addItem(f"{rate} Hz", rate)
                self.fs.setCurrentIndex(max(0, self.fs.findData(self._desired_fs)))
        capable = s.get("capable", False)
        self.capability.setText(f"RAW_ENGINEERING capability  {'Advertised' if capable else UNKNOWN}\n"
                                f"INFO fs_allowed  {', '.join(map(str, rates)) or UNKNOWN}\n"
                                f"Device mode  {_value(s.get('mode'))}   |   Nominal optical Fs  "
                                f"{_value(s.get('fs'))} Hz")
        self.fs.setEnabled(capable and bool(rates) and not busy)
        self.apply.setEnabled(bool(s.get("stopped") and capable and not busy and self._desired_fs in rates))
        self._render_capture()

    def _render_capture(self):
        s = self.state
        context = s.get("context", {})
        info = context.get("observed_info") or {}
        self.identity.setText(f"Reported firmware INFO ({info.get('source', UNKNOWN)})\n"
                              f"{info.get('ascii') or UNKNOWN}")
        destination = str(self._target.folder / self._target.name) if self._target else UNKNOWN
        blockers = self._blockers()
        self.summary.setText(
            f"Endpoint  {s.get('endpoint') or UNKNOWN}\n"
            f"Device  {s.get('device_state', 'DISCONNECTED')}\n"
            f"Mode  {_value(s.get('mode'))}   |   Nominal optical Fs  {_value(s.get('fs'))} Hz\n"
            f"Recording stem  {destination}\n"
            f"Operator firmware SHA  {_value(self._target.firmware_sha if self._target else None)}\n\n"
            + ("\n".join(f"• {item}" for item in blockers) if blockers else "Ready to open acquisition panel."))
        recorder_state = s.get("recording", "IDLE")
        self.recording.setText(f"Recorder  {recorder_state}   |   {_value(s.get('recording_path'))}\n"
                               "Stop acquisition, then finalize the recording.")
        ready = bool(self._config_ready())
        ready_target = self._target is not None and self._target == s.get("checked_target")
        busy = bool(self._pending or s.get("disk_busy") or s.get("closing") or self._closing_window)
        self.arm_button.setEnabled(ready and ready_target and not busy and not s.get("recording_open"))
        self.start_button.setEnabled(ready and not busy and (
            ready_target or recorder_state == "ARMED" and not s.get("recording_used")))
        self.stop_button.setEnabled(bool(s.get("can_stop") and not self._pending
                                         and not s.get("closing") and not self._closing_window))
        self.finalize_button.setEnabled(bool(s.get("stopped") and not busy
                                            and recorder_state in ("ARMED", "RECORDING")))
        self.diag_button.setEnabled(bool(s.get("stopped") and not busy))
        self._render_diagnostics(context)

    def _render_diagnostics(self, context):
        s = self.state
        raw, latest = s.get("raw", {}), s.get("latest", {})
        sample = latest.get("sample")
        sample_text = (f"Latest sample {sample.sample_sequence} · AIN counts {sample.ain} · "
                       f"phase {sample.phase_class} · slot {sample.slot_index} · "
                       f"epoch {latest['epoch']} · {'SIMULATED' if latest['simulated'] else 'device frame'}"
                       if sample else "Waiting for RAW samples.")
        rate = raw.get("adc_rate_ts_estimated", {})
        unflagged = raw.get("adc_rate_ts_not_flagged", {})

        def estimate(observation):
            hz = observation.get("hz")
            return UNKNOWN if hz is None else f"{hz:.2f} SPS ({observation['anchor_pairs']} anchor pairs)"

        self.raw.setText(
            f"{sample_text}\n"
            f"Host receipt (Unix µs)  {_value(latest.get('host_us'))}\n"
            f"Device packet first-sample anchor (µs)  {_value(latest.get('first_device_us'))}  "
            f"|  TS_ESTIMATED  {_value(latest.get('estimated'))}\n"
            f"Nominal optical Fs  {_value(s.get('fs'))} Hz  |  Measured optical Fs  {UNKNOWN}\n"
            f"RAW ADC anchor estimate · flagged  {estimate(rate)}  |  unflagged  {estimate(unflagged)}\n"
            f"Observed stream · {raw.get('packets', 0)} packets / {raw.get('samples', 0)} samples\n"
            f"Forward gap samples {raw.get('forward_gap_samples', 0)} · still missing within history "
            f"{raw.get('forward_gap_samples_not_seen_late', 0)} · GAP_BEFORE packets "
            f"{raw.get('gap_before_packets', 0)}")
        meta = s.get("recording_meta") or {}
        fw_raw = context.get("firmware_raw_diagnostics") or {}
        fw_status = context.get("firmware_status") or {}
        host = s.get("host_counters", {})
        self.diagnostics.setText(
            f"Recorder · RAW queue drops {_value(meta.get('queue_drops'))} · "
            f"binary-confirmed packets {_value(meta.get('persisted_archive', {}).get('packets'))} · "
            f"enqueued/unconfirmed {_value(meta.get('enqueued_not_confirmed_in_archive'))}\n"
            f"Host controller · timeouts {host.get('timeouts', 0)} · malformed {host.get('malformed', 0)} "
            f"· wrong session {host.get('wrong_session', 0)}\n"
            f"Firmware last INFO snapshot (reply µs {_value(fw_raw.get('device_reply_us'))}) · "
            f"RAW sent {_value(fw_raw.get('raw_packets_sent'))} · buffer dropped samples "
            f"{_value(fw_raw.get('raw_dropped_samples'))} · high water "
            f"{_value(fw_raw.get('raw_buffer_highwater'))}\n"
            f"Firmware FRAME send errors {UNKNOWN} in this UI · "
            f"STATUS counters {_value(fw_status.get('counters'))}\n"
            "Observed sample gaps alone do not identify a cause.")
        log = s.get("log", ())
        if log != self._last_log:
            self._last_log = log
            bar = self.log_view.verticalScrollBar()
            at_end, position = bar.value() == bar.maximum(), bar.value()
            self.log_view.setPlainText("\n".join(log))
            bar.setValue(bar.maximum() if at_end else position)

    def closeEvent(self, event):
        if self._worker_finished:
            event.accept()
            return
        event.ignore()
        if self._closing_window:
            return
        if self.state.get("connected") or self._pending or self.state.get("recording_open"):
            answer = QtWidgets.QMessageBox.question(
                self, "Close NoctiNIRS 2 Host",
                "Closing will request STOP for an owned session, close the link and finalize "
                "any recording. Keep this window open to read the cleanup result.",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._closing_window = True
        self.worker.submit("shutdown")
        self._render()

    @QtCore.Slot()
    def _closed_worker(self):
        self._worker_finished = True
        self._render()


def main(argv=None):
    parser = argparse.ArgumentParser(description="NoctiNIRS 2 Host RAW Engineering")
    parser.add_argument("--host-sha", help="Optional caller-supplied host build SHA")
    args = parser.parse_args(argv)
    if args.host_sha is not None and not re.fullmatch(r"[a-fA-F0-9]{40}", args.host_sha):
        parser.error("--host-sha must contain 40 hexadecimal characters")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    window = EngineeringWindow(load_geometry(geometry_path()), args.host_sha)
    window.show()
    code = app.exec()
    if window.worker.isRunning():
        window.worker.submit("shutdown")
    window.worker.wait()
    return code
