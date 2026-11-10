"""Stage D2 desktop: Welcome -> staged Setup -> Live panel, and the session worker."""

from __future__ import annotations

import argparse
from pathlib import Path
from queue import Empty, Queue
import re
import sys
import time

import PySide6
from PySide6 import QtCore, QtGui, QtWidgets

from noctinirs_host import __version__
from noctinirs_host.engineering import EngineeringRun, UNKNOWN, default_recording_folder
from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview import PREVIEW_LABEL
from noctinirs_host.preview.params import PreviewConfig

from .battery_text import format_battery
from .credits import APP_NAME, about_text
from .design import tokens as T
from .design.manager import ThemeManager, build_palette
from .preview_panel import PreviewPanel, PreviewSettingsForm
from .resources import geometry_path
from .widgets import AutoText, ElidedLabel, FadeStack, Section, SmoothScrollArea, StatusChip

SCREENS = ("Welcome", "Setup", "Live")
BACKGROUND = ("preview_destination",)  # housekeeping: never shown as "Working", never clears errors
SETUP_STEPS = ("Device", "MBLL", "Short separation", "Session")
MIN_WINDOW = (900, 600)       # smallest supported window: START/STOP/recovery, Back and three full channel rows
                              # stay visible (height budget: tests/ui/test_design_ui.py)
SIDEBAR_W = 340               # default width of the settings panel (300-480 px, a splitter moves it)
SIDEBAR_MIN, SIDEBAR_MAX = 300, 480
FEEDBACK_MAX_H = 44           # px: two lines each for feedback and error (full text in the tooltip)


class SessionWorker(QtCore.QThread):
    """A queue gives the session one owner, including periodic work and cleanup."""

    updated = QtCore.Signal(object, str)

    def __init__(self, geometry, host_sha=None, run_factory=EngineeringRun, parent=None):
        super().__init__(parent)      # parent=None: the window must not own (and delete) a running thread
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
                if action not in BACKGROUND:
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


def info_lines(ascii_text: str | None) -> str:
    """INFO is a `;`-separated string with no spaces to wrap on: show one field per line."""
    if not ascii_text:
        return UNKNOWN
    return "\n".join(part.strip() for part in ascii_text.split(";") if part.strip())


class EngineeringWindow(QtWidgets.QMainWindow):
    def __init__(self, geometry, host_sha=None, *, run_factory=EngineeringRun, documents=None,
                 settings=None):
        super().__init__()
        # A closed window is destroyed; a lingering one would be re-styled on every theme change.
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.theme = ThemeManager(settings, self)
        self.setWindowTitle("NoctiNIRS 2 Host — RAW Engineering")
        avail = (QtGui.QGuiApplication.primaryScreen().availableGeometry()
                 if QtGui.QGuiApplication.primaryScreen() else None)
        self.setMinimumSize(*MIN_WINDOW)
        self.resize(min(1320, avail.width()) if avail else 1320, min(900, avail.height()) if avail else 900)
        self.geometry = geometry
        self.state = {}
        self.flat = 0
        self._pending = ""
        self._closing_window = False
        self._worker_finished = False
        self._desired_fs = None
        self._rates = ()
        self._last_log = ()
        self._previewed = None
        self._recovery_buttons = []
        self._default_folder = str(default_recording_folder(documents))
        self.preview_cfg = PreviewConfig()
        self.worker = SessionWorker(geometry, host_sha, run_factory)   # no Qt parent: see SessionWorker
        self.worker.updated.connect(self._update)
        self.worker.finished.connect(self._closed_worker)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self._about)

        root = QtWidgets.QWidget(objectName="root")
        self.root = root
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(T.SPACE_M, T.SPACE_S + 4, T.SPACE_M, T.SPACE_S + 4)
        layout.setSpacing(T.SPACE_S)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(T.SPACE_S)
        layout.addLayout(header)
        self.banner = ElidedLabel("NoctiNIRS 2 · ENGINEERING / NON-SCIENCE · " + PREVIEW_LABEL)
        self.banner.setObjectName("banner")
        header.addWidget(self.banner, 1)
        self.connection_chip = StatusChip("Tidak terhubung", self.theme)
        self.recording_chip = StatusChip("Rekaman · tidak aktif", self.theme)
        self.battery_label = StatusChip("Baterai —", self.theme)
        self.battery_label.setMaximumWidth(380)
        for chip in (self.connection_chip, self.recording_chip):
            chip.setWordWrap(False)
        for chip in (self.connection_chip, self.recording_chip, self.battery_label):
            header.addWidget(chip)
        self._build_view_menu(header)
        self.step = _label(name="step")
        self.step.setVisible(False)         # the step chips of the setup screen already show the position
        layout.addWidget(self.step)
        self.screens = FadeStack(self.theme)
        layout.addWidget(self.screens, 1)
        self.feedback = _label(name="feedback")
        self.feedback.setMaximumHeight(FEEDBACK_MAX_H)   # the navigation row must always keep its room
        self.feedback.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        self.feedback.hide()
        self.error = _label(name="error")
        self.error.setMaximumHeight(FEEDBACK_MAX_H)
        self.error.hide()
        self._form_error = ""
        # One row at the bottom: Back, the messages (feedback and error) in the middle, Continue. The
        # messages share the row with the buttons instead of taking two extra rows of height from the graphs.
        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(T.SPACE_S)
        layout.addLayout(nav)
        self.back = self._button("Back", self._back)
        nav.addWidget(self.back, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        messages = QtWidgets.QVBoxLayout()
        messages.setSpacing(2)
        messages.addWidget(self.feedback)
        messages.addWidget(self.error)
        nav.addLayout(messages, 1)
        self.next = self._button("Continue", self._advance, primary=True)
        nav.addWidget(self.next, 0, QtCore.Qt.AlignmentFlag.AlignBottom)

        self._welcome()
        self._setup()
        self._live()
        self.folder.setPlainText(self._default_folder)
        self.theme.changed.connect(self._on_theme_changed)
        self._on_theme_changed(self.theme.tokens)
        self._go(0)
        self.worker.start()

    # ---- theme and view menu ------------------------------------------------------------------
    def _build_view_menu(self, header):
        self.view_button = QtWidgets.QToolButton()
        self.view_button.setText("Tampilan  ▾")
        self.view_button.setToolTip("Tema terang/gelap dan gerakan")
        self.view_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(self.view_button)
        group = QtGui.QActionGroup(menu)
        self.action_light = menu.addAction("Tema terang")
        self.action_dark = menu.addAction("Tema gelap")
        for action, name in ((self.action_light, "light"), (self.action_dark, "dark")):
            action.setCheckable(True)
            action.setActionGroup(group)
            action.setChecked(self.theme.name == name)
            action.triggered.connect(lambda _c=False, n=name: self.theme.set_theme(n))
        menu.addSeparator()
        self.action_reduce_motion = menu.addAction("Kurangi gerakan")
        self.action_reduce_motion.setCheckable(True)
        self.action_reduce_motion.setChecked(self.theme.reduce_motion)
        self.action_reduce_motion.toggled.connect(self.theme.set_reduce_motion)
        self.view_button.setMenu(menu)
        header.addWidget(self.view_button)

    def set_theme(self, name: str) -> None:
        self.theme.set_theme(name)

    def _on_theme_changed(self, tokens):
        self.root.setStyleSheet(self.theme.qss())
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(tokens))
        self.action_light.setChecked(tokens.name == "light")
        self.action_dark.setChecked(tokens.name == "dark")

    # ---- building blocks -------------------------------------------------
    @staticmethod
    def _scroll(widget):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _page(self, title, parent_stack):
        page = QtWidgets.QWidget(objectName="page")
        parent_stack.addWidget(self._scroll(page))
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(20, 14, 20, 14)
        if title:
            outer.addWidget(_label(title, "title"))
        body = QtWidgets.QVBoxLayout()
        outer.addLayout(body)
        outer.addStretch()
        return body

    @staticmethod
    def _button(text, slot, *, primary=False, name=""):
        button = QtWidgets.QPushButton(text)
        if primary or name:
            button.setObjectName(name or "primary")
        button.clicked.connect(slot)
        return button

    # ---- screen 1: Welcome -------------------------------------------------
    def _welcome(self):
        page = self._page("", self.screens)
        page.addWidget(_label("NoctiNIRS 2 Host", "title"))
        page.addWidget(_label("RAW ENGINEERING · sesi UDP · pratinjau Hb langsung · rekaman RAW otomatis",
                              "subtitle"))
        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(T.SPACE_M)
        page.addLayout(cards)
        for title, text in (
                ("1 · Setup bertahap", "Device (koneksi dan ENGINEERING), MBLL, Short separation, dan "
                                       "Session (rekaman). Back/Continue mempertahankan isian Anda."),
                ("2 · Live", "Satu baris per channel, ΔHbO dan ΔHbR dalam satu grafik, tiga baris "
                             "terlihat sekaligus. Membuka panel Live tidak mengirim START."),
                ("3 · Rekaman", "File RAW tersimpan otomatis di Documents\\noctinirs\\<tanggal> sebagai "
                                "record_X, tanpa menimpa. Preview tidak mengubah file RAW.")):
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            card.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Maximum)
            card.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            col = QtWidgets.QVBoxLayout(card)
            col.setContentsMargins(T.SPACE_M, T.SPACE_M, T.SPACE_M, T.SPACE_M)
            col.addWidget(_label(title, "sectiontitle"))
            col.addWidget(_label(text, "muted"))
            col.addStretch()
            cards.addWidget(card, 1)
        page.addWidget(_label(PREVIEW_LABEL + ". Tampilan Hb dan OD dihitung host dari hitungan RAW. "
                              "Nilainya perubahan dari baseline (Δ), bukan nilai mutlak.", "previewbanner"))
        self.welcome_geometry = _label(
            f"Geometry map {self.geometry.geometry_map_id} · status {self.geometry.status}"
            + (" · DRAFT: jarak dan pemetaan channel belum final." if self.geometry.is_draft else ""), "muted")
        page.addWidget(self.welcome_geometry)

    # ---- screen 2: staged Setup --------------------------------------------
    def _setup(self):
        holder = QtWidgets.QWidget()
        self.screens.addWidget(holder)
        col = QtWidgets.QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        chips = QtWidgets.QHBoxLayout()
        col.addLayout(chips)
        self.chips = []
        for i, name in enumerate(SETUP_STEPS):
            chip = QtWidgets.QLabel(f"{i + 1}  {name}")
            chip.setObjectName("chip")
            chips.addWidget(chip)
            self.chips.append(chip)
        chips.addStretch()
        self.setup_steps = FadeStack(self.theme)
        col.addWidget(self.setup_steps, 1)
        self._device_step()
        self._mbll_step()
        self._ssr_step()
        self._session_step()

    def _device_step(self):
        page = self._page("Device · connection and ENGINEERING", self.setup_steps)
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
        row.addStretch()
        page.addLayout(row)
        self.connection = AutoText(min_lines=3)
        page.addWidget(self.connection)
        self._recovery_row(page)
        self.capability = AutoText(min_lines=3)
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
        page.addWidget(_label("ENGINEERING and Fs can be set while STOPPED. The rate list comes from "
                              "the connected peer. Optical Fs and RAW ADC rate are separate."))
        page.addWidget(_label("Reported firmware INFO (one field per line)"))
        self.info_view = AutoText(min_lines=2)
        page.addWidget(self.info_view)

    def _mbll_step(self):
        page = self._page("MBLL · PREVIEW parameters", self.setup_steps)
        page.addWidget(_label(PREVIEW_LABEL + "\nValues below are injected into the core with their "
                              "source and status 'unverified'. They are saved with the session as "
                              "provenance. Wavelengths 735/850 are nominal labels."))
        self.mbll_form = PreviewSettingsForm(("mbll",), self.preview_cfg)
        page.addWidget(self.mbll_form)

    def _ssr_step(self):
        page = self._page("Short separation · PREVIEW", self.setup_steps)
        self.ssr_geometry = _label()
        page.addWidget(self.ssr_geometry)
        self.ssr_form = PreviewSettingsForm(("ssr",), self.preview_cfg)
        page.addWidget(self.ssr_form)

    def _session_step(self):
        page = self._page("Session · recording and readiness", self.setup_steps)
        self.record_check = QtWidgets.QCheckBox("Record RAW automatically when I press Start")
        self.record_check.setChecked(True)
        self.record_check.toggled.connect(self._render)
        page.addWidget(self.record_check)
        page.addWidget(_label("Recording folder (created automatically when needed)"))
        self.folder = AutoText(readonly=False, min_lines=2)
        self.folder.setAccessibleName("Recording folder")
        self.folder.textChanged.connect(self._folder_edited)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.folder, 1)
        col = QtWidgets.QVBoxLayout()
        self.browse_button = self._button("Browse…", self._browse)
        self.default_button = self._button("Default", lambda: self.folder.setPlainText(self._default_folder))
        col.addWidget(self.browse_button)
        col.addWidget(self.default_button)
        col.addStretch()
        row.addLayout(col)
        page.addLayout(row)
        self.next_file = AutoText(min_lines=1)
        page.addWidget(self.next_file)
        form = QtWidgets.QFormLayout()
        page.addLayout(form)
        self.firmware = QtWidgets.QLineEdit()
        self.firmware.setPlaceholderText("Optional · 40 hexadecimal characters")
        form.addRow("Operator-provided firmware SHA", self.firmware)
        self.identity = AutoText(min_lines=1)
        page.addWidget(self.identity)
        page.addWidget(_label("Readiness"))
        self.summary = AutoText(min_lines=4)
        page.addWidget(self.summary)

    # ---- screen 3: Live ------------------------------------------------------
    def _live(self):
        """Top: an action bar that never collapses (START, STOP, SAFE_OFF, Disconnect). Below it a settings
        panel (left, foldable sections, collapsible) and the stacked channel rows (right)."""
        page = QtWidgets.QWidget()
        page.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(T.SPACE_S)
        bar = QtWidgets.QFrame()
        bar.setObjectName("card")
        bar.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(T.SPACE_M, T.SPACE_S, T.SPACE_M, T.SPACE_S)
        row.setSpacing(T.SPACE_S)
        self.start_button = self._button("Start acquisition", self._start, primary=True)
        self.stop_button = self._button("Stop acquisition", lambda: self._submit("stop"), name="danger")
        self.panel_disconnect = self._button("Disconnect", self._disconnect)
        for b in (self.start_button, self.stop_button, self.panel_disconnect):
            row.addWidget(b)
        row.addStretch()
        self._add_recovery(row)
        bar.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        outer.addWidget(bar)                  # pinned above the splitter: never scrolls, never clipped
        self.action_bar = bar

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setObjectName("livesplit")
        outer.addWidget(split, 1)
        self.live_split = split

        side_widget = QtWidgets.QWidget(objectName="page")
        side = QtWidgets.QVBoxLayout(side_widget)
        side.setContentsMargins(T.SPACE_S, T.SPACE_S, T.SPACE_S, T.SPACE_S)
        side.setSpacing(2)

        def block(title, expanded=True):
            body = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(body)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(T.SPACE_S // 2)
            sec = Section(title, body, expanded=expanded, theme=self.theme)
            side.addWidget(sec)
            return lay, sec

        lay, self.section_session = block("Sesi dan status")
        self.device = AutoText(min_lines=3)
        lay.addWidget(self.device)
        self.diag_button = self._button("Refresh diagnostics", lambda: self._submit("diagnostics"))
        lay.addWidget(self.diag_button)
        self.recording = AutoText(min_lines=2)
        lay.addWidget(self.recording)
        self.loss_label = AutoText(min_lines=2)
        lay.addWidget(self.loss_label)
        self.profile_line = AutoText(min_lines=3)        # LED calibration profile as the firmware reports it
        self.profile_line.setAccessibleName("LED calibration profile reported by the firmware")
        lay.addWidget(self.profile_line)

        self.panel = PreviewPanel(self.geometry, self.theme)
        self.panel.detail_changed.connect(self._show_detail)
        self.panel.sidebar_toggle_requested.connect(self.toggle_sidebar)
        lay, self.section_display = block("Tampilan")
        lay.addWidget(self.panel.controls)

        lay, self.section_preview = block("Pratinjau: DPF, SCR, baseline")
        self.live_form = PreviewSettingsForm(("mbll", "ssr"), self.preview_cfg)
        lay.addWidget(self.live_form)
        buttons = QtWidgets.QHBoxLayout()
        self.apply_live_button = self._button("Apply live settings", self._apply_live, primary=True)
        self.reset_baseline_button = self._button("Reset baseline", lambda: self._submit("reset_preview_baseline"))
        buttons.addWidget(self.apply_live_button)
        buttons.addWidget(self.reset_baseline_button)
        lay.addLayout(buttons)

        lay, self.section_detail = block("Detail channel (klik baris)")
        self.detail = AutoText(min_lines=12)
        lay.addWidget(self.detail)

        lay, self.section_diag = block("Diagnostik", expanded=False)
        self.raw = AutoText(min_lines=4)
        self.diagnostics = AutoText(min_lines=4)
        self.log_view = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log_view.setAccessibleName("Recent session log, UTC")
        self.log_view.setMinimumHeight(120)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._scroll(self.raw), "RAW")
        tabs.addTab(self._scroll(self.diagnostics), "Recorder / firmware")
        tabs.addTab(self.log_view, "Log")
        tabs.setMinimumHeight(220)
        lay.addWidget(tabs)
        side.addStretch()

        side_scroll = self._scroll(side_widget)
        side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setMinimumWidth(SIDEBAR_MIN)
        side_scroll.setMaximumWidth(SIDEBAR_MAX)
        self.side_scroll = side_scroll
        self.sidebar_open = True
        self._side_anim = None
        self.theme.motion_changed.connect(self._sidebar_motion_changed)
        split.addWidget(side_scroll)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([SIDEBAR_W, 960])
        page.fade_target = side_scroll         # only the settings panel fades in; the graphs never animate
        self.live_page = page
        self.screens.addWidget(page)

    @property
    def sidebar_animating(self) -> bool:
        return self._side_anim is not None and self._side_anim.state() == QtCore.QAbstractAnimation.State.Running

    def _settle_sidebar(self) -> None:
        scroll = self.side_scroll
        if self.sidebar_open:
            scroll.setMinimumWidth(SIDEBAR_MIN)
            scroll.setMaximumWidth(SIDEBAR_MAX)
        else:
            scroll.setVisible(False)

    def _sidebar_motion_changed(self, reduced: bool) -> None:
        if reduced and self.sidebar_animating:
            self._side_anim.stop()
            self._settle_sidebar()

    def toggle_sidebar(self) -> None:
        """Collapse or restore the settings panel. START/STOP live in the action bar, so they stay."""
        opening = not self.sidebar_open
        self.sidebar_open = opening
        self.panel.collapse_button.setText("◀" if opening else "▶")
        if self._side_anim is not None:
            self._side_anim.stop()
        scroll, ms = self.side_scroll, self.theme.ms(200)
        if opening:
            scroll.setVisible(True)
            scroll.setMinimumWidth(0)
            if ms == 0:
                self._settle_sidebar()
                return
            start = scroll.width() if scroll.maximumWidth() < SIDEBAR_MAX else 0
            scroll.setMaximumWidth(start)
            end = SIDEBAR_W
        else:
            scroll.setMinimumWidth(0)
            if ms == 0:
                self._settle_sidebar()
                return
            start, end = scroll.width(), 0
            scroll.setMaximumWidth(start)
        if self._side_anim is None:            # one animation object, reused by every toggle
            self._side_anim = QtCore.QPropertyAnimation(scroll, b"maximumWidth", self)
            self._side_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._side_anim.finished.connect(self._settle_sidebar)
        self._side_anim.setStartValue(start)
        self._side_anim.setEndValue(end)
        self._side_anim.setDuration(ms)
        self._side_anim.start()

    def _add_recovery(self, row):
        check = self._button("Check START outcome", lambda: self._submit("check_start_outcome"))
        safe = self._button("SAFE_OFF…", self._safe_off)
        row.addWidget(check)
        row.addWidget(safe)
        self._recovery_buttons.append((check, safe))

    def _recovery_row(self, page):
        row = QtWidgets.QHBoxLayout()
        check = self._button("Check START outcome", lambda: self._submit("check_start_outcome"))
        safe = self._button("SAFE_OFF…", self._safe_off)
        row.addWidget(check)
        row.addWidget(safe)
        row.addStretch()
        page.addLayout(row)
        self._recovery_buttons.append((check, safe))

    # ---- actions ---------------------------------------------------------------
    def folder_text(self) -> str:
        return self.folder.toPlainText().replace("\n", "").replace("\r", "").strip()

    def _folder_edited(self):
        self._form_error = ""
        self._render()

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Recording folder", self.folder_text())
        if folder:
            self.folder.setPlainText(str(Path(folder)))

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

    def _start(self):
        if self.record_check.isChecked():
            self._submit("start_recorded", self.folder_text(), self.firmware.text().strip())
        else:
            self._submit("start")

    def _apply_live(self):
        try:
            self.preview_cfg = self.live_form.apply_to(self.preview_cfg)
        except ValueError as exc:
            self._form_error = str(exc)
            self._render()
            return
        self.mbll_form.set_config(self.preview_cfg)
        self.ssr_form.set_config(self.preview_cfg)
        self._submit("set_preview_config", self.preview_cfg)

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

    def _folder_problem(self):
        text = self.folder_text()
        if not self.record_check.isChecked():
            return ""
        if not text or not Path(text).is_absolute():
            return "Enter an absolute recording folder, or press Default."
        return ""

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
        folder = self._folder_problem()
        if folder:
            problems.append(folder)
        return problems

    # ---- navigation ------------------------------------------------------------
    def _go(self, flat):
        self.flat = flat
        screen = 0 if flat == 0 else (2 if flat == 5 else 1)
        self.screens.setCurrentIndex(screen)
        self.step.setVisible(False)
        if screen == 1:
            self.setup_steps.setCurrentIndex(flat - 1)
        if flat == 5:
            self.live_form.set_config(self.preview_cfg)
        self._render()

    def _advance(self):
        if self._worker_finished:
            self.close()
            return
        f = self.flat
        self._form_error = ""
        try:
            if f == 1:
                if not self.state.get("connected"):
                    raise ValueError("Connect to a peer before continuing.")
                if not self._config_ready():
                    raise ValueError("Choose an advertised Fs and apply ENGINEERING before continuing.")
            elif f == 2:
                self.preview_cfg = self.mbll_form.apply_to(self.preview_cfg)
            elif f == 3:
                self.preview_cfg = self.ssr_form.apply_to(self.preview_cfg)
            elif f == 4:
                if self._blockers():
                    raise ValueError("Resolve the readiness issues shown above.")
                self._submit("set_preview_config", self.preview_cfg)
        except ValueError as exc:
            self._form_error = str(exc)
        else:
            self._go(f + 1)
            return
        self._render()

    def _back(self):
        self._form_error = ""
        self._go(self.flat - 1)

    @QtCore.Slot(object, str)
    def _update(self, state, action):
        self.state = state
        if action and self._pending == action:
            self._pending = ""
        self.panel.reset_for_epoch(state.get("preview_epoch"))
        self.panel.set_fs(state.get("fs"))
        self.panel.push(state.get("preview_new", ()))
        self._render()

    # ---- rendering -------------------------------------------------------------
    def _render(self, *_):
        s = self.state
        connected = s.get("connected", False)
        uncertain = s.get("start_uncertain", False)
        busy = bool(self._pending or s.get("closing") or self._closing_window)
        disk = s.get("disk_busy", False)
        recorder_open = s.get("recording_open", False)
        f = self.flat
        if f == 0:
            self.step.setText("Welcome")
        elif f == 5:
            self.step.setText("Live panel   •   " + PREVIEW_LABEL)
        else:
            self.step.setText(f"Setup {f} / 4   •   {SETUP_STEPS[f - 1]}")
        for i, chip in enumerate(self.chips):
            chip.setProperty("active", f == i + 1)
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self.back.setEnabled(f > 0 and not (busy or disk or recorder_open or s.get("session")))
        self.next.setVisible(f < 5 or self._worker_finished)
        self.next.setEnabled(self._worker_finished or not (busy or disk))
        self.next.setText("Close application" if self._worker_finished else
                          ("Open live panel" if f == 4 else "Continue"))
        feedback = "Working: " + self._pending.replace("_", " ") if self._pending else s.get("notice", "")
        if s.get("cleanup"):
            feedback = "\n".join(part for part in (s["cleanup"], feedback) if part)
        self.feedback.setText(feedback)
        self.feedback.setToolTip(feedback)
        self.feedback.setVisible(bool(feedback))
        errors = "\n".join(part for part in (self._form_error, s.get("error")) if part)
        self.error.setText(errors)
        self.error.setToolTip(errors)
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
        self.connection.set_text(device)
        self.device.set_text(device)
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
        self.capability.set_text(f"RAW_ENGINEERING capability  {'Advertised' if capable else UNKNOWN}\n"
                                 f"INFO fs_allowed  {', '.join(map(str, rates)) or UNKNOWN}\n"
                                 f"Device mode  {_value(s.get('mode'))}   |   Nominal optical Fs  "
                                 f"{_value(s.get('fs'))} Hz")
        self.fs.setEnabled(capable and bool(rates) and not busy)
        self.apply.setEnabled(bool(s.get("stopped") and capable and not busy and self._desired_fs in rates))
        self._render_battery()
        self._render_capture()

    def _render_battery(self):
        info = format_battery(self.state.get("battery"))
        if self.battery_label.text() != info.text:
            self.battery_label.setText(info.text)
        self.battery_label.setToolTip(info.tooltip)
        self.battery_label.set_tone({"low": "warn", "problem": "warn"}.get(info.level, "neutral"))
        self._render_status_chips()

    def _render_status_chips(self):
        s = self.state
        if s.get("start_uncertain"):
            text, tone = "START tidak pasti", "danger"
        elif s.get("connected"):
            text, tone = f"Terhubung · {s.get('device_state', '')}", "ok"
        else:
            text, tone = "Tidak terhubung", "neutral"
        if self.connection_chip.text() != text:
            self.connection_chip.setText(text)
        self.connection_chip.set_tone(tone)
        rec = s.get("recording", "IDLE")
        path = s.get("recording_path")
        stem = Path(str(path)).name.split(".")[0] if path else ""
        text = f"Rekaman · {rec}" + (f" · {stem}" if stem and rec in ("RECORDING", "ARMED") else "")
        tone = {"RECORDING": "danger", "ARMED": "warn", "OPENING": "warn", "FINALIZING": "warn",
                "FINALIZED": "ok", "INCOMPLETE / ERROR": "danger"}.get(rec, "neutral")
        if rec == "IDLE":
            text = "Rekaman · tidak aktif"
        if self.recording_chip.text() != text:
            self.recording_chip.setText(text)
        self.recording_chip.set_tone(tone)

    def _sync_destination(self):
        """Ask the worker which record_X the next START would use (no files created)."""
        s = self.state
        if self.flat not in (4, 5) or self._folder_problem() or not s:
            return
        key = (self.folder_text(), self.firmware.text().strip(), s.get("recording"))
        if key != self._previewed and not s.get("disk_busy") and not s.get("recording_open"):
            self._previewed = key
            self.worker.submit("preview_destination", key[0], key[1])

    def _render_capture(self):
        s = self.state
        context = s.get("context", {})
        info = context.get("observed_info") or {}
        self.info_view.set_text(info_lines(info.get("ascii")))
        self.identity.set_text(f"Reported firmware INFO source: {info.get('source', UNKNOWN)}")
        folder_problem = self._folder_problem()
        note = s.get("destination_note")
        if not self.record_check.isChecked():
            self.next_file.set_text("RAW recording is off. Start will run without an archive.")
        elif folder_problem:
            self.next_file.set_text(folder_problem)
        else:
            self.next_file.set_text("Next recording: " + (note or f"{self.folder_text()}\\record_? "
                                                          "(numbered when you press Start)"))
        blockers = self._blockers()
        self.summary.set_text(
            f"Endpoint  {s.get('endpoint') or UNKNOWN}\n"
            f"Device  {s.get('device_state', 'DISCONNECTED')}\n"
            f"Mode  {_value(s.get('mode'))}   |   Nominal optical Fs  {_value(s.get('fs'))} Hz\n"
            f"Recording  {'on · ' + self.folder_text() if self.record_check.isChecked() else 'off'}\n"
            f"PREVIEW  DPF {self.preview_cfg.dpf.mode} · short separation {self.preview_cfg.ssr_mode}"
            f" · baseline {self.preview_cfg.baseline_s:g} s · parameters unverified\n"
            f"Geometry  map {self.geometry.geometry_map_id} · {self.geometry.status}\n\n"
            + ("\n".join(f"• {item}" for item in blockers) if blockers else "Ready to open the live panel."))
        self.ssr_geometry.setText(
            "Short channels (from the geometry file): "
            + ", ".join(f"{c.name} {c.sd_mm:g} mm ({c.source}–{c.pd})"
                        for c in self.geometry.channels if c.kind == "short")
            + (". Geometry is DRAFT; the 5/8 mm labels are unverified (SC2 saturated on 9 Nov)."
               if self.geometry.is_draft else "."))
        recorder_state = s.get("recording", "IDLE")
        loss = s.get("preview_loss") or {}
        self.loss_label.set_text(
            "Paket hilang: " + (
                f"{loss.get('samples_missing', 0)} sampel ({loss.get('samples_missing_percent', 0.0):.1f} %) "
                f"dalam {loss.get('sample_jumps', 0)} lompatan · ±{loss.get('msg_missing', 0)} pesan · "
                f"gap_before dari perangkat: {loss.get('gap_before_packets', 0)}" if loss else "belum ada data")
            + "\nGap yang dilaporkan perangkat tidak menangkap kehilangan di jaringan; host menghitung lompatan sendiri.")
        self.profile_line.set_text((s.get("profile") or {}).get("summary", ""))
        self.recording.set_text(f"Recorder  {recorder_state}   |   {_value(s.get('recording_path'))}\n"
                                f"Preview errors (never affect the RAW file): {s.get('preview_errors', 0)}")
        ready = bool(self._config_ready())
        busy = bool(self._pending or s.get("disk_busy") or s.get("closing") or self._closing_window)
        self.start_button.setEnabled(ready and not busy and not s.get("recording_open")
                                     and not s.get("session") and not self._folder_problem())
        self.stop_button.setEnabled(bool(s.get("can_stop") and not self._pending
                                         and not s.get("closing") and not self._closing_window))
        self.diag_button.setEnabled(bool(s.get("stopped") and not busy))
        self.reset_baseline_button.setEnabled(bool(s.get("preview_active")) and not self._pending)
        self.apply_live_button.setEnabled(not self._pending and not self._closing_window)
        self.panel.set_geometry_note(
            ("Geometry DRAFT: distances and mapping are not final. " if s.get("geometry_draft") else "")
            + ("Label 5/8 mm pada short channel belum terverifikasi: pada bench 9 Nov SC2 (8 mm) "
               "jenuh dan SC1 (5 mm) tidak, mungkin tertukar. " if s.get("geometry_draft") else "")
            + "ON/OFF dari fase terjadwal (ON_STABLE/OFF_STABLE), bukan status LED yang diverifikasi "
              "perangkat. Ambang kualitas dipilih host dan σ derau diestimasi online.")
        self._render_diagnostics(context)
        self._sync_destination()

    def _show_detail(self):
        self.detail.set_text("\n".join(f"{k}: {v}" for k, v in self.panel.detail_rows()))

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

        self.raw.set_text(
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
        self.diagnostics.set_text(
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


def shutdown_worker(worker: SessionWorker) -> None:
    """Ask a still-running worker to shut down and wait for it. The worker is owned by Python, so this is
    safe after the window (a C++ object that Qt deletes on close) is gone."""
    if worker.isRunning():
        worker.submit("shutdown")
    worker.wait()


def main(argv=None, *, run_factory=EngineeringRun, settings=None, documents=None, on_show=None):
    parser = argparse.ArgumentParser(description="NoctiNIRS 2 Host RAW Engineering")
    parser.add_argument("--host-sha", help="Optional caller-supplied host build SHA")
    args = parser.parse_args(argv)
    if args.host_sha is not None and not re.fullmatch(r"[a-fA-F0-9]{40}", args.host_sha):
        parser.error("--host-sha must contain 40 hexadecimal characters")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    app.setStyle("Fusion")      # one consistent base style under the generated style sheet
    window = EngineeringWindow(load_geometry(geometry_path()), args.host_sha, run_factory=run_factory,
                               settings=settings, documents=documents)
    worker = window.worker      # keep our own reference: after the close the window object is deleted by Qt
    window.show()
    if on_show is not None:     # test hook: called once the window is shown, inside the event loop's start-up
        on_show(window)
    code = app.exec()
    shutdown_worker(worker)
    return code
