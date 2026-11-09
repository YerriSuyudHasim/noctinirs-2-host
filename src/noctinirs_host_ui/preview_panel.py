"""PREVIEW widgets: settings form, 16-channel sweep grid and channel detail.

Display only. Every number comes from `noctinirs_host.preview` (the core); this module has no
MBLL, OD, DPF or extinction constants. Invalid channels are NaN and are drawn as gaps.
"""

from __future__ import annotations

import bisect
import dataclasses
import math
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from noctinirs_host.preview import PREVIEW_LABEL
from noctinirs_host.preview.params import (DpfSetting, ExtinctionTable, PreviewConfig,
                                           SSR_METHODS, SSR_MODES, resolve_dpf)

MODES = (("Hb", "Δ Hb (µM)"), ("OD", "OD (ln)"), ("I", "Intensity (ON−OFF counts)"))
WINDOWS_S = (10, 20, 30, 60)
# Display ranges only. None = robust auto (central 96 % of the window plus margin; points outside
# are clamped to the edge and marked in red); "full" = the whole data range, no clamping.
SCALES = {"Hb": (None, "full", 1.0, 2.0, 5.0, 20.0), "OD": (None, "full", 0.01, 0.05, 0.2, 1.0),
          "I": (None, "full")}
ROBUST_PERCENTILES = (2.0, 98.0)
ROBUST_MARGIN = 0.35
UNITS = {"Hb": "µM", "OD": "", "I": "counts"}
GAP_FACTOR = 2.5
ERASE_FRACTION = 0.03
RETAIN_S = 130.0
CHANNEL_ROWS = ("long",)


def fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:.{digits}g}"


def _compact(combo: QtWidgets.QComboBox) -> None:
    """A combo box should not force its panel as wide as its longest entry."""
    combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(8)


class PreviewSettingsForm(QtWidgets.QWidget):
    """Edits the fields of PreviewConfig that belong to its `sections` ("mbll", "ssr")."""

    changed = QtCore.Signal()

    def __init__(self, sections=("mbll", "ssr"), base: PreviewConfig | None = None, parent=None):
        super().__init__(parent)
        self.sections = tuple(sections)
        self._base = base or PreviewConfig()
        self._loading = False
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)   # narrow side panel
        d = QtWidgets.QDoubleSpinBox
        if "mbll" in self.sections:
            self.eps = {}
            for wl in ("735", "850"):
                for hb in ("HbO", "HbR"):
                    box = d(decimals=1, minimum=1.0, maximum=1e6, singleStep=10.0)
                    self.eps[(wl, hb)] = box
                    form.addRow(f"ε {hb} @ {wl} nm (M⁻¹cm⁻¹)", box)
            self.eps_note = QtWidgets.QLabel()
            self.eps_note.setWordWrap(True)
            form.addRow(self.eps_note)
            self.dpf_mode = QtWidgets.QComboBox()
            self.dpf_mode.addItem("Manual", "manual")
            self.dpf_mode.addItem("From age (Scholkmann & Wolf 2013)", "age")
            self.dpf_735 = d(decimals=3, minimum=0.1, maximum=50.0, singleStep=0.1)
            self.dpf_850 = d(decimals=3, minimum=0.1, maximum=50.0, singleStep=0.1)
            self.age = d(decimals=1, minimum=0.0, maximum=120.0, singleStep=1.0)
            form.addRow("DPF mode", self.dpf_mode)
            form.addRow("DPF 735 nm", self.dpf_735)
            form.addRow("DPF 850 nm", self.dpf_850)
            form.addRow("Age (years)", self.age)
            self.dpf_info = QtWidgets.QLabel()
            self.dpf_info.setWordWrap(True)
            form.addRow(self.dpf_info)
            self.baseline = d(decimals=1, minimum=1.0, maximum=600.0, singleStep=1.0)
            form.addRow("Baseline duration (s, median)", self.baseline)
            self.dpf_mode.currentIndexChanged.connect(self._dpf_changed)
        if "ssr" in self.sections:
            self.ssr_mode = QtWidgets.QComboBox()
            for mode in SSR_MODES:
                self.ssr_mode.addItem({"off": "Off", "5mm": "5 mm", "8mm": "8 mm",
                                       "both": "Both (5 mm + 8 mm)"}[mode], mode)
            self.ssr_method = QtWidgets.QComboBox()
            for method in SSR_METHODS:
                self.ssr_method.addItem(method.upper() if method != "auto" else "Auto (OLS; Ridge for both)",
                                        method)
            self.ridge = d(decimals=6, minimum=0.0, maximum=1000.0, singleStep=0.001)
            self.window = d(decimals=1, minimum=1.0, maximum=600.0, singleStep=5.0)
            form.addRow("Short separation", self.ssr_mode)
            form.addRow("Method", self.ssr_method)
            form.addRow("Ridge λ", self.ridge)
            form.addRow("Rolling window (s)", self.window)
            self.ssr_note = QtWidgets.QLabel(
                "Regression runs on OD before MBLL. It assumes the short channel holds only "
                "superficial signal. Until enough samples exist the channel shows no value.")
            self.ssr_note.setWordWrap(True)
            form.addRow(self.ssr_note)
        self.set_config(self._base)
        for combo in self.findChildren(QtWidgets.QComboBox):
            _compact(combo)
        for widget in (self.findChildren(QtWidgets.QDoubleSpinBox)):
            widget.valueChanged.connect(self._edited)
        for widget in (self.findChildren(QtWidgets.QComboBox)):
            widget.currentIndexChanged.connect(self._edited)

    def _edited(self, *_):
        if not self._loading:
            if "mbll" in self.sections:
                self._dpf_changed()
            self.changed.emit()

    def _dpf_changed(self, *_):
        age = self.dpf_mode.currentData() == "age"
        self.dpf_735.setEnabled(not age)
        self.dpf_850.setEnabled(not age)
        self.age.setEnabled(age)
        try:
            r = resolve_dpf(self._dpf_setting())
            text = f"DPF {r.values['735']:.3f} / {r.values['850']:.3f} · {r.source} · {r.status}"
            if r.warnings:
                text += "\n⚠ " + "\n⚠ ".join(r.warnings)
        except ValueError as exc:
            text = str(exc)
        self.dpf_info.setText(text)

    def _dpf_setting(self) -> DpfSetting:
        return DpfSetting(mode=self.dpf_mode.currentData(),
                          manual={"735": self.dpf_735.value(), "850": self.dpf_850.value()},
                          age_years=self.age.value() if self.dpf_mode.currentData() == "age" else None,
                          source=self._base.dpf.source if self.dpf_mode.currentData() == "manual"
                          else "Scholkmann & Wolf 2013", status=self._base.dpf.status)

    def set_config(self, cfg: PreviewConfig) -> None:
        self._loading = True
        self._base = cfg
        if "mbll" in self.sections:
            for (wl, hb), box in self.eps.items():
                box.setValue(cfg.extinction.eps[wl][hb])
            self.eps_note.setText(f"Extinction source: {cfg.extinction.source} · status "
                                  f"{cfg.extinction.status}")
            self.dpf_mode.setCurrentIndex(max(0, self.dpf_mode.findData(cfg.dpf.mode)))
            self.dpf_735.setValue(cfg.dpf.manual["735"])
            self.dpf_850.setValue(cfg.dpf.manual["850"])
            self.age.setValue(cfg.dpf.age_years if cfg.dpf.age_years is not None else 28.0)
            self.baseline.setValue(cfg.baseline_s)
        if "ssr" in self.sections:
            self.ssr_mode.setCurrentIndex(self.ssr_mode.findData(cfg.ssr_mode))
            self.ssr_method.setCurrentIndex(self.ssr_method.findData(cfg.ssr_method))
            self.ridge.setValue(cfg.ridge_lambda)
            self.window.setValue(cfg.ssr_window_s)
        self._loading = False
        if "mbll" in self.sections:
            self._dpf_changed()

    def apply_to(self, cfg: PreviewConfig) -> PreviewConfig:
        """Return `cfg` with this form's fields replaced. Raises ValueError if invalid."""
        changes = {}
        if "mbll" in self.sections:
            eps = {wl: {hb: self.eps[(wl, hb)].value() for hb in ("HbO", "HbR")} for wl in ("735", "850")}
            edited = eps != {wl: dict(r) for wl, r in cfg.extinction.eps.items()}
            changes["extinction"] = ExtinctionTable(
                eps, cfg.extinction.source + (" — edited by operator" if edited and
                                              "edited by operator" not in cfg.extinction.source else ""),
                cfg.extinction.status)
            changes["dpf"] = self._dpf_setting()
            changes["baseline_s"] = self.baseline.value()
        if "ssr" in self.sections:
            changes.update(ssr_mode=self.ssr_mode.currentData(), ssr_method=self.ssr_method.currentData(),
                           ridge_lambda=self.ridge.value(), ssr_window_s=self.window.value())
        new = dataclasses.replace(cfg, **changes)
        new.validate()
        return new


VISIBLE_ROWS = 3          # exactly this many channel rows fill the visible area at the top
ROW_MIN_H = 96            # px: a sensible minimum when the window is small
DIVIDER_H = 28            # px: the "Left hemisphere / Right hemisphere" bands
FOOTER_H = 46             # px: the shared time axis
DISPLAY_HZ = 25           # one display refresh timer, decoupled from acquisition
SHARED_SCALE_PERIOD_S = 0.5
Y_SHRINK_RATIO = 1.8      # keep the y range until it is this much wider than the data needs
TRACE_WIDTH = 2.2        # px of the current trace; the previous sweep pass is thinner
KEYS = {"Hb": ("hbo", "hbr"), "OD": ("od1", "od2"), "I": ("i1", "i2")}


def channel_order(geometry) -> list:
    """[(hemisphere name, [ChannelInfo, ...]), ...]: left first, then right. Within a hemisphere the long
    channels in channel number, then the short channels by name. Derived from the geometry (the side
    of each channel's source), not hard-coded."""
    side = {s.id: s.side for s in geometry.sources}
    groups = []
    for code, name in (("L", "Left hemisphere"), ("R", "Right hemisphere")):
        longs = sorted((c for c in geometry.channels if c.kind == "long" and side[c.source] == code),
                       key=lambda c: int("".join(ch for ch in c.name if ch.isdigit())))
        shorts = sorted((c for c in geometry.channels if c.kind == "short" and side[c.source] == code),
                        key=lambda c: c.name)
        groups.append((name, longs + shorts))
    return groups


def peak_decimate(x: np.ndarray, y: np.ndarray, max_points: int):
    """Min/max per bin so spikes survive; NaN gaps stay NaN. Returns (x, y)."""
    n = len(x)
    if n <= max_points or max_points < 4:
        return x, y
    bins = max_points // 2
    idx = np.linspace(0, n, bins + 1).astype(int)[:-1]
    idx = np.unique(idx)
    lo = np.fmin.reduceat(y, idx)
    hi = np.fmax.reduceat(y, idx)
    xs = x[idx]
    xo = np.empty(2 * len(idx))
    yo = np.empty(2 * len(idx))
    xo[0::2], xo[1::2] = xs, xs
    # order min/max by which comes first inside the bin so the line does not zig-zag
    first_is_lo = np.ones(len(idx), dtype=bool)
    yo[0::2] = np.where(first_is_lo, lo, hi)
    yo[1::2] = np.where(first_is_lo, hi, lo)
    return xo, yo


class _PassPlot(pg.PlotWidget):
    """A plot that never takes the mouse wheel: the wheel scrolls the stack of rows."""

    def wheelEvent(self, event):  # noqa: N802 (Qt name)
        event.ignore()


class ChannelRow(QtWidgets.QFrame):
    """One channel: a label column on the left and one plot with both traces."""

    clicked = QtCore.Signal(str)

    def __init__(self, ch, hemisphere: str, parent=None) -> None:
        super().__init__(parent)
        self.ch, self.name = ch, ch.name
        self.status_key = None
        self.ylim = None                    # the y range currently applied (None: auto range)
        self.tip_provider = None            # called only when a tooltip is shown (nothing is formatted per frame)
        self.setObjectName("channelrow")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", False)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 6, 4)
        lay.setSpacing(8)
        info = QtWidgets.QWidget()
        info.setFixedWidth(132)
        col = QtWidgets.QVBoxLayout(info)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.name_button = QtWidgets.QPushButton(ch.name)
        self.name_button.setObjectName("rowname")
        self.name_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.name_button.setToolTip(f"{ch.name}: open the channel detail")
        self.name_button.clicked.connect(lambda: self.clicked.emit(self.name))
        self.meta = QtWidgets.QLabel(f"{hemisphere.split()[0]} · {ch.kind} · {ch.sd_mm:g} mm")
        self.meta.setObjectName("rowmeta")
        self.legend = QtWidgets.QLabel()
        self.legend.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.status = QtWidgets.QLabel("…")
        self.status.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.status.setObjectName("rowmeta")
        self.status.setWordWrap(True)
        for w in (self.name_button, self.meta, self.legend, self.status):
            col.addWidget(w)
        col.addStretch()
        lay.addWidget(info)
        self.plot = _PassPlot()
        self.plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.plot.setMinimumHeight(40)
        lay.addWidget(self.plot, 1)
        self.plot.scene().sigMouseClicked.connect(lambda _e: self.clicked.emit(self.name))

    def event(self, event) -> bool:
        if event.type() == QtCore.QEvent.Type.ToolTip and self.tip_provider is not None:
            QtWidgets.QToolTip.showText(event.globalPos(), self.tip_provider(), self)
            return True
        return super().event(event)

    def set_selected(self, on: bool) -> None:
        if self.property("selected") != on:
            self.setProperty("selected", on)
            self.style().unpolish(self)
            self.style().polish(self)


class PreviewPanel(QtWidgets.QWidget):
    """The graph area: one row per channel, stacked and scrollable, exactly three rows visible.

    Each row has both traces in one plot (ΔHbO and ΔHbR, or the OD / intensity pair) and shares one time
    axis with all the others (linked X; the sticky footer carries the labels). Quality is shown on
    each row: a gap is missing or invalid data; a drawn line carries markers for warnings (LOW_SIGNAL,
    LEAK_WARN) and for a channel shown without short-separation correction. The display controls are
    in `controls`; the window puts them in the settings panel. Only rows in view are redrawn."""

    selected = QtCore.Signal(str)
    detail_changed = QtCore.Signal()
    sidebar_toggle_requested = QtCore.Signal()

    def __init__(self, geometry, theme=None, parent=None):
        super().__init__(parent)
        from .design.tokens import LIGHT
        from .widgets import SmoothScrollArea
        self.geometry = geometry
        self._theme = theme
        self._tokens = theme.tokens if theme is not None else LIGHT
        self.fs = 10.0
        self.mode = "Hb"
        self.window_s = 30
        self.scale = None
        self.lock_scale = False
        self.current = "CH1"
        self._pts: dict = {c.name: deque() for c in geometry.channels}   # ChannelPoint history (detail, status)
        self._tl: list = []                                                  # shared time of every cached row
        self._cols = {c.name: {k: [] for k in ("hbo", "hbr", "od1", "od2", "i1", "i2", "warn", "unc")}
                      for c in geometry.channels}
        self._dirty_rows: set = set()
        self._epoch = None
        self._shared = None
        self._shared_at = 0.0
        self.refresh_count = 0
        self.last_refresh_ms = 0.0
        self.channels = {c.name: c for c in geometry.channels}
        self.groups = channel_order(geometry)
        self.order = [c.name for _, chs in self.groups for c in chs]
        self.longs = [c.name for c in geometry.channels if c.kind == "long"]
        self.shorts = [c.name for c in geometry.channels if c.kind == "short"]
        self.setObjectName("plotpanel")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        outer.addLayout(top)
        self.collapse_button = QtWidgets.QToolButton()
        self.collapse_button.setText("◀  Panel")
        self.collapse_button.setToolTip("Sembunyikan atau tampilkan panel pengaturan")
        self.collapse_button.clicked.connect(self.sidebar_toggle_requested.emit)
        top.addWidget(self.collapse_button)
        self.banner = QtWidgets.QLabel(f"{PREVIEW_LABEL} · Δ = perubahan dari baseline")
        self.banner.setObjectName("previewbanner")
        top.addWidget(self.banner, 1)
        self.jump_combo = QtWidgets.QComboBox()
        self.jump_combo.addItem("Ke channel…", None)
        for name in self.order:
            self.jump_combo.addItem(name, name)
        _compact(self.jump_combo)
        self.jump_combo.setToolTip("Lompat ke channel")
        self.jump_combo.activated.connect(self._jump)
        top.addWidget(self.jump_combo)

        # controls (placed in the settings panel by the window)
        self.geometry_note = QtWidgets.QLabel()
        self.geometry_note.setWordWrap(True)
        self.geometry_note.setObjectName("muted")
        self.controls = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.controls)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode_combo = QtWidgets.QComboBox()
        for key, label in MODES:
            self.mode_combo.addItem(label, key)
        self.window_combo = QtWidgets.QComboBox()
        for s in WINDOWS_S:
            self.window_combo.addItem(f"{s} s sweep", s)
        self.window_combo.setCurrentIndex(WINDOWS_S.index(30))
        self.scale_combo = QtWidgets.QComboBox()
        for combo in (self.mode_combo, self.window_combo, self.scale_combo):
            _compact(combo)
        self.lock_check = QtWidgets.QCheckBox("Kunci skala bersama (semua baris)")
        form.addRow("Display", self.mode_combo)
        form.addRow("Sweep", self.window_combo)
        form.addRow("Scale", self.scale_combo)
        form.addRow(self.lock_check)
        form.addRow(self.geometry_note)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.window_combo.currentIndexChanged.connect(self._window_changed)
        self.scale_combo.currentIndexChanged.connect(self._scale_changed)
        self.lock_check.toggled.connect(self._lock_changed)

        # the stack of rows
        self.scroll = SmoothScrollArea(theme)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.rows_host = QtWidgets.QWidget()
        self.rows_host.setObjectName("rowshost")
        host = QtWidgets.QVBoxLayout(self.rows_host)
        host.setContentsMargins(0, 0, 0, 0)
        host.setSpacing(0)
        self.rows: dict = {}
        self.dividers: list = []
        self.plots, self.curves, self.clamps, self.cursors, self.warns, self.uncorr = {}, {}, {}, {}, {}, {}
        for hemisphere, chs in self.groups:
            divider = QtWidgets.QLabel(hemisphere.upper())
            divider.setObjectName("hemisphere")
            divider.setFixedHeight(DIVIDER_H)
            divider.setContentsMargins(12, 0, 0, 0)
            host.addWidget(divider)
            self.dividers.append(divider)
            for ch in chs:
                row = ChannelRow(ch, hemisphere)
                row.tip_provider = lambda n=ch.name: "\n".join(f"{k}: {v}" for k, v in self.detail_rows(n))
                row.clicked.connect(self.select)
                host.addWidget(row)
                self.rows[ch.name] = row
                self._style_plot(row)
        host.addStretch(0)
        self.scroll.setWidget(self.rows_host)
        outer.addWidget(self.scroll, 1)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)

        # one shared time axis, linked to every row
        self.master = self.rows[self.order[0]].plot
        for name in self.order:
            plot = self.rows[name].plot
            plot.getPlotItem().hideAxis("bottom")
            if plot is not self.master:
                plot.getPlotItem().vb.setXLink(self.master.getPlotItem().vb)
        self.footer = _PassPlot()
        self.footer.setFixedHeight(FOOTER_H)
        self.footer.setMenuEnabled(False)
        self.footer.hideButtons()
        fitem = self.footer.getPlotItem()
        left = fitem.getAxis("left")           # an invisible left axis: the same width as the rows' axes,
        left.setWidth(48)                      # so the shared time axis lines up with every plot
        left.setStyle(showValues=False, tickLength=0)
        left.setPen(pg.mkPen(None))
        fitem.getViewBox().setMouseEnabled(False, False)
        fitem.getAxis("bottom").setLabel("s (sweep time, shared by all rows)")
        fitem.getViewBox().setXLink(self.master.getPlotItem().vb)
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(148, 0, 6, 0)     # lines the axis up with the plots (label column + margins)
        foot.addWidget(self.footer)
        outer.addLayout(foot)

        self.legend = QtWidgets.QLabel()
        self.legend.setObjectName("muted")
        self.legend.setWordWrap(True)
        outer.addWidget(self.legend)

        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)   # the default timer is coarse (about 16 ms) on Windows
        self.timer.setInterval(int(1000 / DISPLAY_HZ))
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        if theme is not None:
            theme.changed.connect(self.apply_theme)
        self._mode_changed()
        self.apply_theme(self._tokens)
        self._apply_window()
        self.select("CH1")

    # ---- construction and theming ------------------------------------------------------------
    def _style_plot(self, row: ChannelRow) -> None:
        name, w = row.name, row.plot
        w.setMenuEnabled(False)
        w.setMouseEnabled(False, False)
        w.hideButtons()
        item = w.getPlotItem()
        # Only the time grid: pyqtgraph's y grid doubled the paint cost (measured). A cheap zero line
        # marks the baseline of the delta values instead.
        item.showGrid(x=True, y=False, alpha=0.25)
        item.getAxis("left").setWidth(48)
        font = w.font()
        font.setPointSize(8)
        item.getAxis("left").setStyle(tickFont=font)
        item.getAxis("bottom").setStyle(tickFont=font)
        curves = []
        for style in (QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenStyle.SolidLine,
                      QtCore.Qt.PenStyle.DotLine, QtCore.Qt.PenStyle.DotLine):
            c = pg.PlotDataItem(connect="finite")
            c.setClipToView(True)
            c._nn2_style = style
            item.addItem(c)
            curves.append(c)
        clamp = pg.ScatterPlotItem(size=6)
        warn = pg.ScatterPlotItem(size=5, symbol="t1")
        uncorr = pg.ScatterPlotItem(size=5, symbol="d")
        cursor = pg.InfiniteLine(angle=90)
        zero = pg.InfiniteLine(angle=0, pos=0)
        zero.setZValue(-5)
        for extra in (zero, clamp, warn, uncorr, cursor):
            item.addItem(extra)
        row.zero = zero
        self.plots[name], self.curves[name], self.clamps[name], self.cursors[name] = w, curves, clamp, cursor
        self.warns[name], self.uncorr[name] = warn, uncorr

    def apply_theme(self, tokens) -> None:
        """Colours of the plots and the row legends follow the theme. No curve data is touched."""
        self._tokens = tokens
        t = tokens
        for name, row in self.rows.items():
            w = row.plot
            w.setBackground(t.plot_background)
            item = w.getPlotItem()
            for axis in ("left", "bottom"):
                ax = item.getAxis(axis)
                ax.setPen(pg.mkPen(t.plot_axis))
                ax.setTextPen(pg.mkPen(t.plot_axis))
            item.showGrid(x=True, y=False, alpha=0.3)
            row.zero.setPen(pg.mkPen(t.plot_axis, width=1, style=QtCore.Qt.PenStyle.DashLine))
            self.cursors[name].setPen(pg.mkPen(t.plot_axis, width=1))
            self.clamps[name].setPen(pg.mkPen(t.marker_clamp))
            self.clamps[name].setBrush(pg.mkBrush(t.marker_clamp))
            self.warns[name].setPen(pg.mkPen(t.marker_warning, width=0.6))
            self.warns[name].setBrush(pg.mkBrush(QtGui.QColor(t.marker_warning)))
            self.uncorr[name].setPen(pg.mkPen(t.marker_uncorrected, width=0.6))
            self.uncorr[name].setBrush(pg.mkBrush(QtGui.QColor(t.marker_uncorrected)))
        self.footer.setBackground(t.plot_background)
        fax = self.footer.getPlotItem().getAxis("bottom")
        fax.setPen(pg.mkPen(t.plot_axis))
        fax.setTextPen(pg.mkPen(t.plot_axis))
        self._paint_traces()
        self._dirty_rows = set(self.order)
        for name in self.order:
            self._update_status(name)

    def _trace_colors(self):
        t = self._tokens
        return (t.hbo, t.hbr) if self.mode == "Hb" else (t.od_a, t.od_b)

    def _trace_names(self):
        return {"Hb": ("ΔHbO", "ΔHbR"), "OD": ("OD 735", "OD 850"), "I": ("I 735", "I 850")}[self.mode]

    def _paint_traces(self) -> None:
        c1, c2 = self._trace_colors()
        n1, n2 = self._trace_names()
        for name, row in self.rows.items():
            for k, curve in enumerate(self.curves[name]):
                color = (c1, c2)[k % 2]
                width = TRACE_WIDTH if k < 2 else 1.2
                curve.setPen(pg.mkPen(color, width=width, style=curve._nn2_style))
            row.legend.setText(f"<span style='color:{c1}'><b>━ {n1}</b></span>&nbsp; "
                               f"<span style='color:{c2}'><b>━ {n2}</b></span>")
        t = self._tokens
        self.legend.setText(
            f"<b>Celah</b> = data hilang atau invalid · <span style='color:{t.marker_warning}'><b>▲</b></span> "
            f"peringatan (LOW_SIGNAL / LEAK_WARN), garis tetap tampil · "
            f"<span style='color:{t.marker_uncorrected}'><b>◆</b></span> tanpa koreksi SCR · "
            f"<span style='color:{t.marker_clamp}'><b>●</b></span> di luar skala (ditandai) · label 735/850 nominal")

    # ---- controls ------------------------------------------------------------------------------
    def set_fs(self, fs: float | None) -> None:
        if fs:
            self.fs = float(fs)

    def _mode_changed(self, *_):
        self.mode = self.mode_combo.currentData()
        self.scale_combo.blockSignals(True)
        self.scale_combo.clear()
        for s in SCALES[self.mode]:
            label = ("Auto (robust)" if s is None else "Full range" if s == "full"
                     else f"±{s:g} {UNITS[self.mode]}".strip())
            self.scale_combo.addItem(label, s)
        self.scale_combo.blockSignals(False)
        self.scale = None
        self._shared = None
        if hasattr(self, "rows"):
            self._paint_traces()
        self._dirty_rows = set(self.order)

    def _window_changed(self, *_):
        self.window_s = self.window_combo.currentData()
        self._apply_window()
        self._dirty_rows = set(self.order)

    def _scale_changed(self, *_):
        self.scale = self.scale_combo.currentData()
        self._shared = None
        self._dirty_rows = set(self.order)

    def _lock_changed(self, on: bool) -> None:
        self.lock_scale = bool(on)
        self._shared = None
        self._dirty_rows = set(self.order)

    def _apply_window(self) -> None:
        self.master.getPlotItem().setXRange(0, float(self.window_s), padding=0)

    def set_geometry_note(self, text: str) -> None:
        self.geometry_note.setText(text)
        self.banner.setToolTip(text)

    # ---- data -------------------------------------------------------------------------------------
    def clear(self) -> None:
        for d in self._pts.values():
            d.clear()
        self._tl.clear()
        for cols in self._cols.values():
            for lst in cols.values():
                lst.clear()
        self._dirty_rows = set(self.order)
        for name, row in self.rows.items():
            row.ylim = None
            row.status.setText("…")
            for curve in self.curves[name]:
                curve.setData([], [])
            for item in (self.warns[name], self.uncorr[name], self.clamps[name]):
                item.setData([])

    def reset_for_epoch(self, epoch) -> None:
        if epoch != self._epoch:
            self._epoch = epoch
            self.clear()

    @staticmethod
    def _record(p) -> dict:
        shown = p.state == "VALID"
        return {"hbo": p.d_hbo, "hbr": p.d_hbr, "od1": p.od735, "od2": p.od850, "i1": p.i735, "i2": p.i850,
                "warn": shown and bool(p.warnings), "unc": shown and p.ssr_state == "UNCORRECTED"}

    def push(self, frames) -> None:
        frames = list(frames)
        if not frames:
            return
        period = 1.0 / self.fs
        for fr in frames:
            if self._tl and fr.t_s - self._tl[-1] > GAP_FACTOR * period:   # a hole in time: a NaN break
                self._tl.append(self._tl[-1] + period)
                for cols in self._cols.values():
                    for k, lst in cols.items():
                        lst.append(False if k in ("warn", "unc") else math.nan)
            self._tl.append(fr.t_s)
            for name, p in fr.points.items():
                rec = self._record(p)
                cols = self._cols[name]
                for k, v in rec.items():
                    cols[k].append(v)
                d = self._pts[name]
                d.append(p)
                if len(d) > 4000:
                    d.popleft()
        t_last = self._tl[-1]
        if self._tl[0] < t_last - RETAIN_S - 20:
            k = bisect.bisect_left(self._tl, t_last - RETAIN_S)
            del self._tl[:k]
            for cols in self._cols.values():
                for lst in cols.values():
                    del lst[:k]
        self._dirty_rows = set(self.order)
        for name in self.order:
            self._update_status(name)

    def _update_status(self, name: str) -> None:
        d = self._pts[name]
        row, t = self.rows[name], self._tokens
        if not d:
            return
        p = d[-1]
        if p.state == "INVALID":
            text, color = "✕ INVALID", t.danger
        elif p.state == "BASELINE":
            text, color = "… baseline", t.text_secondary
        elif p.warnings:
            text, color = "▲ " + ", ".join(p.warnings), t.warning
        elif p.ssr_state == "UNCORRECTED":
            text, color = "◆ tanpa koreksi SCR", t.marker_uncorrected
        else:
            text, color = "● OK", t.success
        if row.status_key != (text, color):          # a style-sheet change is costly: only when it changed
            row.status_key = (text, color)
            row.status.setText(f"<span style='color:{color}'>{text}</span>")   # rich text: no style-sheet re-polish

    def _mode_arrays(self, name: str, start: int = 0):
        k1, k2 = KEYS[self.mode]
        cols = self._cols[name]
        return (np.asarray(self._tl[start:], dtype=float), np.asarray(cols[k1][start:], dtype=float),
                np.asarray(cols[k2][start:], dtype=float), np.asarray(cols["warn"][start:], dtype=bool),
                np.asarray(cols["unc"][start:], dtype=bool))

    def series(self, name: str):
        """(t, y1, y2) of everything retained, with NaN breaks at time holes."""
        return self._mode_arrays(name)[:3]

    def flags(self, name: str):
        """(warning mask, uncorrected mask) aligned with `series`."""
        return self._mode_arrays(name)[3:]

    def finite_points(self, name: str) -> int:
        _, a, b = self.series(name)
        return int(np.isfinite(a).sum() + np.isfinite(b).sum())

    def clamped_points(self, name: str) -> int:
        return len(self.clamps[name].data) if self.clamps[name].data is not None else 0

    def marker_counts(self, name: str) -> tuple:
        """(warning markers, uncorrected markers) currently drawn on the row's plot."""
        return len(self.warns[name].data), len(self.uncorr[name].data)

    def finite_channels(self) -> int:
        return sum(1 for n in self.longs if self.finite_points(n) > 0)

    # ---- visibility and layout -------------------------------------------------------------------
    def eventFilter(self, obj, event):  # noqa: N802 (Qt name)
        if obj is self.scroll.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802 (Qt name)
        super().showEvent(event)
        self.timer.start()
        QtCore.QTimer.singleShot(0, self._relayout)

    def hideEvent(self, event):  # noqa: N802 (Qt name)
        super().hideEvent(event)
        self.timer.stop()                # a hidden panel does not even wake up

    def row_height(self) -> int:
        vh = self.scroll.viewport().height()
        return max(ROW_MIN_H, (vh - DIVIDER_H) // VISIBLE_ROWS)

    def _relayout(self) -> None:
        h = self.row_height()
        for row in self.rows.values():
            if row.height() != h or row.minimumHeight() != h:
                row.setFixedHeight(h)
        self._dirty_rows = set(self.order)

    def visible_names(self, margin: int = 0) -> list:
        top = self.scroll.verticalScrollBar().value() - margin
        bottom = top + self.scroll.viewport().height() + 2 * margin
        return [n for n in self.order if self.rows[n].y() + self.rows[n].height() > top
                and self.rows[n].y() < bottom]

    def fully_visible_rows(self) -> list:
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()
        return [n for n in self.order if self.rows[n].y() >= top - 1
                and self.rows[n].y() + self.rows[n].height() <= bottom + 1]

    def _scrolled(self, *_):
        self._dirty_rows |= set(self.visible_names())

    def _jump(self, index: int) -> None:
        name = self.jump_combo.itemData(index)
        if name:
            self.scroll.scroll_to(self.rows[name].y())
            self.select(name)
        self.jump_combo.setCurrentIndex(0)

    def scroll_to_channel(self, name: str) -> None:
        self.scroll.scroll_to(self.rows[name].y())

    # ---- drawing --------------------------------------------------------------------------------
    @staticmethod
    def _robust_limits(ys, shown):
        """Central part of the visible data plus a margin; None when there is nothing to scale."""
        parts = [y[shown & np.isfinite(y)] for y in ys]
        vals = np.concatenate(parts) if parts else np.empty(0)
        if vals.size < 4:
            return None
        lo, hi = np.percentile(vals, ROBUST_PERCENTILES)
        span = hi - lo
        if span <= 1e-12 * max(1.0, abs(hi), abs(lo)):
            span = max(abs(hi), 1e-9) * 0.05 + 1e-12
        return lo - ROBUST_MARGIN * span, hi + ROBUST_MARGIN * span

    def _window_start(self) -> int:
        if not self._tl:
            return 0
        return bisect.bisect_left(self._tl, self._tl[-1] - 2.0 * self.window_s - 1.0)

    def _row_limits(self, ys, shown):
        if isinstance(self.scale, (int, float)):
            return -float(self.scale), float(self.scale)
        if self.scale is None:
            return self._robust_limits(ys, shown)
        return None

    def _shared_limits(self, start: int):
        """One y range for every row (the lock): the union of the rows' own robust limits."""
        if self.scale == "full":
            return None
        now = time.monotonic()
        if self._shared is not None and now - self._shared_at < SHARED_SCALE_PERIOD_S:
            return self._shared
        w = float(self.window_s)
        t_last = self._tl[-1]
        lo, hi = math.inf, -math.inf
        for name in self.order:
            t, y1, y2, _, _ = self._mode_arrays(name, start)
            lim = self._row_limits([y1, y2], np.ones(len(t), dtype=bool))
            if lim is not None:
                lo, hi = min(lo, lim[0]), max(hi, lim[1])
        self._shared = (lo, hi) if lo < hi else None
        self._shared_at = now
        self._dirty_rows = set(self.order)
        return self._shared

    def refresh(self) -> None:
        """Redraw the rows that are in view and changed. Cheap when nothing is dirty."""
        if not self._tl or not self._dirty_rows or not self.isVisible():
            return                       # nothing new, or nobody is looking: no drawing at all
        t0 = time.perf_counter()
        visible = self.visible_names(margin=self.row_height() // 2)
        todo = [n for n in visible if n in self._dirty_rows]
        if not todo:
            return
        start = self._window_start()
        w = float(self.window_s)
        t_last = self._tl[-1]
        cur_pass = math.floor(t_last / w)
        cursor_x = t_last - cur_pass * w
        erase = w * ERASE_FRACTION
        shared = self._shared_limits(start) if self.lock_scale else None
        for name in todo:
            self._dirty_rows.discard(name)
            row = self.rows[name]
            t, y1, y2, warn, unc = self._mode_arrays(name, start)
            ys = [y1, y2]
            passes = np.floor(t / w)
            x = t - passes * w
            new = passes == cur_pass
            old = (passes == cur_pass - 1) & (x > cursor_x + erase)
            shown = new | old
            limits = shared if (self.lock_scale and shared is not None) else self._row_limits(ys, shown)
            vb = row.plot.getPlotItem().getViewBox()
            if limits is not None:
                # Hysteresis: moving the y range regenerates the axis (the costly part of a draw), so the range
                # only moves when the data leaves it or it became much too large.
                cur = row.ylim
                if (cur is None or limits[0] < cur[0] - 1e-12 or limits[1] > cur[1] + 1e-12
                        or (cur[1] - cur[0]) > Y_SHRINK_RATIO * (limits[1] - limits[0])):
                    row.ylim = cur = limits
                    vb.setYRange(cur[0], cur[1], padding=0)
                limits = cur
            else:
                if row.ylim is not None:
                    row.ylim = None
                vb.enableAutoRange("y", True)
            clamp_xy = []
            if limits is not None:
                lo, hi = limits
                for k, y in enumerate(ys):
                    over = np.isfinite(y) & ((y < lo) | (y > hi))
                    clamp_xy.append((t[over], np.clip(y[over], lo, hi)))
                    ys[k] = np.clip(y, lo, hi)
            curves = self.curves[name]
            max_pts = max(64, 2 * row.plot.width())
            for k in range(2):
                for curve, mask in ((curves[k], new), (curves[k + 2], old)):
                    if mask.any():
                        xs, yv = peak_decimate(x[mask], ys[k][mask], max_pts)
                        curve.setData(xs, yv)
                    else:
                        curve.setData([], [])
            lead = np.where(np.isfinite(ys[0]), ys[0], ys[1])
            for item, mask in ((self.warns[name], warn), (self.uncorr[name], unc)):
                sel = mask & shown & np.isfinite(lead)
                item.setData(x[sel], lead[sel])
            pts = []
            for ts_, ys_ in clamp_xy:
                pts.extend({"pos": (tt - math.floor(tt / w) * w, yy)} for tt, yy in zip(ts_, ys_)
                           if math.floor(tt / w) in (cur_pass, cur_pass - 1))
            self.clamps[name].setData(pts)
            self.cursors[name].setValue(cursor_x)
        self.refresh_count += 1
        self.last_refresh_ms = (time.perf_counter() - t0) * 1000.0
        self.detail_changed.emit()

    # ---- selection and detail ---------------------------------------------------------------------
    def select(self, name: str) -> None:
        if name in self.channels:
            if name != self.current and self.current in self.rows:
                self.rows[self.current].set_selected(False)
            self.current = name
            self.rows[name].set_selected(True)
            self.selected.emit(name)
            self.detail_changed.emit()

    def detail_rows(self, name: str | None = None) -> list:
        """[(label, value)] for the channel's newest frame. Names follow CHn_I735 etc."""
        name = name or self.current
        ch = self.channels[name]
        rows = [("Channel", f"{name} · {ch.kind} · {ch.source}–{ch.pd} (AIN{ch.ain}) · {ch.sd_mm:g} mm"
                 + (" · DRAFT distance" if self.geometry.is_draft else ""))]
        if not self._pts[name]:
            return rows + [("State", "no data yet")]
        p = self._pts[name][-1]
        rows += [("State", p.state + (f" ({p.reason})" if p.reason else "")),
                 ("Warnings", ", ".join(p.warnings) if p.warnings else "none"),
                 (f"{name}_I735", fmt(p.i735)), (f"{name}_I850", fmt(p.i850)),
                 (f"{name}_OD735", fmt(p.od735)), (f"{name}_OD850", fmt(p.od850)),
                 ("ΔHbO (µM)", fmt(p.d_hbo)), ("ΔHbR (µM)", fmt(p.d_hbr)), ("ΔHbT (µM)", fmt(p.d_hbt)),
                 ("Short separation",
                  "applied, β " + ", ".join(fmt(b, 3) for b in p.ssr_beta) if p.ssr_applied else
                  ("tanpa koreksi SCR: " + p.ssr_reason if p.ssr_state == "UNCORRECTED" else "off")),
                 ("Noise σ (counts) 735 / 850", f"{fmt(p.sigma735, 3)} / {fmt(p.sigma850, 3)}"),
                 ("I/σ (measured) 735 / 850", f"{fmt(p.snr735, 3)} / {fmt(p.snr850, 3)}")]
        return rows
