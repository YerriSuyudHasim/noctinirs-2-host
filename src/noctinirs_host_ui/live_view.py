"""Live view (S2): Overview of every channel and a Detail tab with 1..N chosen channels.

Display only. All numbers come from `noctinirs_host.live` (which takes science values
from `Opt2Frame.science_sample()` only). A missing value is drawn as a break in the line:
nothing is interpolated, filled or smoothed. Quality words are a host heuristic.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from noctinirs_host.live.model import GAP, GOOD, MARGINAL, WAVELENGTHS
from noctinirs_host.live.receiver import FrameReceiver

from .theme import Theme

WINDOWS_S = (10, 30, 60, 120)
DEFAULT_WINDOW_S = 30
OVERVIEW_COLUMNS = 4
DEFAULT_DETAIL = 4  # channels selected when the app starts
WAITING_TEXT = "No data yet. Connect, then press Start stream."


def _arrays(xs: list[float], ys: list[float | None]) -> tuple[np.ndarray, np.ndarray]:
    return (np.asarray(xs, dtype=float),
            np.asarray([math.nan if v is None else v for v in ys], dtype=float))


class LiveView(QtWidgets.QWidget):
    def __init__(self, receiver: FrameReceiver, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.receiver = receiver
        self.theme = theme
        self.paused = False
        self.window_s = DEFAULT_WINDOW_S
        self._dirty = True
        self._seen_frames = -1
        n = len(receiver.channels)

        # --- top row: note + controls ---
        self.plot_note = QtWidgets.QLabel()
        self.plot_note.setObjectName("plot_note")
        self.plot_note.setWordWrap(True)
        self.pause_btn = QtWidgets.QPushButton("Pause display")
        self.pause_btn.setObjectName("live_pause_button")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip("Freeze what is drawn. Data keeps being received and counted.")
        self.pause_btn.toggled.connect(self._on_pause)
        self.window_combo = QtWidgets.QComboBox()
        self.window_combo.setObjectName("live_window_combo")
        for s in WINDOWS_S:
            self.window_combo.addItem(f"{s} s", s)
        self.window_combo.setCurrentIndex(WINDOWS_S.index(DEFAULT_WINDOW_S))
        self.window_combo.currentIndexChanged.connect(self._on_window)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.plot_note, stretch=1)
        top.addWidget(QtWidgets.QLabel("Window"))
        top.addWidget(self.window_combo)
        top.addWidget(self.pause_btn)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("live_status")
        self.status.setWordWrap(True)

        # --- Overview: one small plot per channel ---
        self.overview = pg.GraphicsLayoutWidget()
        self.overview.setObjectName("live_overview")
        self.overview_plots: list[pg.PlotItem] = []
        self.overview_curves: list[dict[str, pg.PlotDataItem]] = []
        for ch in receiver.channels:
            row, col = divmod(ch.index, OVERVIEW_COLUMNS)
            p = self.overview.addPlot(row=row, col=col)
            self._style_plot(p, small=True)
            self.overview_plots.append(p)
            self.overview_curves.append({wl: p.plot([], []) for wl in WAVELENGTHS})

        # --- Detail: channel picker + stacked plots ---
        self.channel_list = QtWidgets.QListWidget()
        self.channel_list.setObjectName("live_channel_list")
        self.channel_list.setMinimumWidth(190)
        self.channel_list.setMaximumWidth(210)
        self.channel_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for ch in receiver.channels:
            it = QtWidgets.QListWidgetItem(f"{ch.name}  {ch.kind} {ch.sd_mm:g} mm")
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if ch.index < DEFAULT_DETAIL
                             else QtCore.Qt.CheckState.Unchecked)
            self.channel_list.addItem(it)
        self.channel_list.itemChanged.connect(self._on_item_changed)
        self.select_all_btn = QtWidgets.QPushButton("All")
        self.select_all_btn.setObjectName("live_select_all")
        self.select_all_btn.clicked.connect(lambda: self.set_selected(range(n)))
        self.select_one_btn = QtWidgets.QPushButton("Only first")
        self.select_one_btn.setObjectName("live_select_one")
        self.select_one_btn.clicked.connect(lambda: self.set_selected([0]))
        self.count_label = QtWidgets.QLabel()
        self.count_label.setObjectName("live_count_label")
        picker = QtWidgets.QVBoxLayout()
        picker.addWidget(QtWidgets.QLabel("Channels (1 to %d)" % n))
        picker.addWidget(self.channel_list, stretch=1)
        row_btn = QtWidgets.QHBoxLayout()
        row_btn.addWidget(self.select_all_btn)
        row_btn.addWidget(self.select_one_btn)
        picker.addLayout(row_btn)
        picker.addWidget(self.count_label)
        self.detail = pg.GraphicsLayoutWidget()
        self.detail.setObjectName("live_detail")
        self.detail_plots: list[tuple[int, pg.PlotItem, dict[str, pg.PlotDataItem]]] = []
        detail_page = QtWidgets.QWidget()
        dl = QtWidgets.QHBoxLayout(detail_page)
        dl.setContentsMargins(0, 8, 0, 0)
        dl.addLayout(picker)
        dl.addWidget(self.detail, stretch=1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("live_tabs")
        ov_page = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(ov_page)
        ol.setContentsMargins(0, 8, 0, 0)
        ol.addWidget(self.overview)
        self.tabs.addTab(ov_page, "Overview")
        self.tabs.addTab(detail_page, "Detail")

        self.empty_label = QtWidgets.QLabel(WAITING_TEXT)
        self.empty_label.setObjectName("live_empty")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.empty_label)
        lay.addWidget(self.tabs, stretch=1)

        self._rebuild_detail()
        self.apply_theme(theme)

    # ---- selection -------------------------------------------------------
    def selected(self) -> list[int]:
        return [i for i in range(self.channel_list.count())
                if self.channel_list.item(i).checkState() == QtCore.Qt.CheckState.Checked]

    def set_selected(self, indexes) -> None:
        wanted = set(indexes) or {0}
        self.channel_list.blockSignals(True)
        for i in range(self.channel_list.count()):
            self.channel_list.item(i).setCheckState(
                QtCore.Qt.CheckState.Checked if i in wanted else QtCore.Qt.CheckState.Unchecked)
        self.channel_list.blockSignals(False)
        self._rebuild_detail()

    def _on_item_changed(self, item) -> None:
        if not self.selected():  # at least one channel always stays selected
            self.channel_list.blockSignals(True)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.channel_list.blockSignals(False)
        self._rebuild_detail()

    def _rebuild_detail(self) -> None:
        self.detail.clear()
        self.detail_plots = []
        sel = self.selected()
        first: pg.PlotItem | None = None
        for k, idx in enumerate(sel):
            p = self.detail.addPlot(row=k, col=0)
            self._style_plot(p, small=False)
            p.setLabel("left", self.receiver.channels[idx].name)
            if first is None:
                first = p
            else:
                p.setXLink(first)
            if k < len(sel) - 1:
                p.getAxis("bottom").setStyle(showValues=False)
            else:
                p.setLabel("bottom", "Time", units="s")
            curves = {wl: p.plot([], [], name=f"{wl} nm") for wl in WAVELENGTHS}
            self.detail_plots.append((idx, p, curves))
        self.count_label.setText(f"{len(sel)} of {len(self.receiver.channels)} selected")
        self.apply_theme(self.theme)
        self._dirty = True
        self.refresh()

    # ---- look ---------------------------------------------------------------
    def _style_plot(self, p: pg.PlotItem, small: bool) -> None:
        p.showGrid(x=False, y=True, alpha=0.18)
        p.hideButtons()
        p.setMenuEnabled(False)
        p.setMouseEnabled(x=False, y=False)
        if small:
            p.getAxis("bottom").setStyle(showValues=False)
            p.getAxis("left").setStyle(showValues=False)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        pens = {"735": pg.mkPen(theme.wl_a, width=1.6), "850": pg.mkPen(theme.wl_b, width=1.6)}
        axis = pg.mkPen(theme.plot_axis)
        for w in (self.overview, self.detail):
            w.setBackground(theme.plot_bg)
        plots = list(self.overview_plots) + [p for _, p, _ in self.detail_plots]
        for p in plots:
            for side in ("left", "bottom"):
                ax = p.getAxis(side)
                ax.setPen(axis)
                ax.setTextPen(axis)
        for cs in self.overview_curves + [c for _, _, c in self.detail_plots]:
            for wl, c in cs.items():
                c.setPen(pens[wl])
        self._dirty = True
        self.refresh()

    # ---- controls ---------------------------------------------------------------
    def _on_pause(self, on: bool) -> None:
        self.paused = on
        self.pause_btn.setText("Resume display" if on else "Pause display")
        self._dirty = True
        self.refresh()

    def _on_window(self) -> None:
        self.window_s = int(self.window_combo.currentData())
        self._dirty = True
        self.refresh()

    # ---- drawing --------------------------------------------------------------------
    def _tier_color(self, tier: str) -> str:
        t = self.theme
        return {GOOD: t.ok, MARGINAL: t.tier_marginal, GAP: t.plot_axis}[tier]

    def _title(self, ch, tier: str) -> str:
        return (f'<span style="color:{self.theme.plot_axis}">{ch.name}</span> '
                f'<span style="color:{self._tier_color(tier)}">{tier}</span>')

    def _note_text(self) -> str:
        r = self.receiver
        who = "SIMULATED DEVICE - SYNTHETIC DATA" if r.simulated is not False \
            else "DEVICE DATA (not simulated)"
        geo = r.geometry.status
        return (f"{who} - intensity = LED-on minus LED-off, ADC counts, nominal 735 / 850 nm. "
                f"Channel list from the geometry file (status: {geo}; positions and distances "
                "not final). Gaps are drawn as gaps. Quality words are a host heuristic.")

    def status_text(self) -> str:
        c = self.receiver.counters
        return (f"frames {c['frames']} | lost {c['lost']} | duplicate {c['duplicate']} | "
                f"late {c['late_recovered']} | rejected {c['rejected']} | "
                f"warm-up/non-science {c['non_science']} | geometry mismatch {c['geometry_mismatch']}")

    def refresh(self) -> None:
        r = self.receiver
        frames = r.counters["frames"]
        self.plot_note.setText(self._note_text())
        self.status.setText(self.status_text())
        self.empty_label.setVisible(frames == 0)
        if self.paused:
            return
        if frames == self._seen_frames and not self._dirty:
            return
        self._seen_frames, self._dirty = frames, False
        t_end = r.last_t_s
        for ch, plot, curves in zip(r.channels, self.overview_plots, self.overview_curves):
            buf = r.buffers[ch.index]
            plot.setTitle(self._title(ch, buf.latest_tier()))
            for wl, curve in curves.items():
                x, y = _arrays(*buf.series(wl, self.window_s))
                curve.setData(x, y, connect="finite")
        for idx, plot, curves in self.detail_plots:
            buf = r.buffers[idx]
            for wl, curve in curves.items():
                x, y = _arrays(*buf.series(wl, self.window_s))
                curve.setData(x, y, connect="finite")
            if t_end is not None:
                plot.setXRange(max(0.0, t_end - self.window_s), max(t_end, 1.0), padding=0)
