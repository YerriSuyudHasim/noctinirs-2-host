"""PREVIEW widgets: settings form, 16-channel sweep grid and channel detail.

Display only. Every number comes from `noctinirs_host.preview` (the core); this module has no
MBLL, OD, DPF or extinction constants. Invalid channels are NaN and are drawn as gaps.
"""

from __future__ import annotations

import dataclasses
import math
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from noctinirs_host.preview import PREVIEW_LABEL
from noctinirs_host.preview.params import (DpfSetting, ExtinctionTable, PreviewConfig,
                                           SSR_METHODS, SSR_MODES, resolve_dpf)

HB_COLORS = ("#c62828", "#1d4ed8")      # dHbO, dHbR
WL_COLORS = ("#b83a0b", "#1d4ed8")      # 735, 850 (nominal labels)
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


class PreviewPanel(QtWidgets.QWidget):
    """Graph area: 16 sweep plots (CH1-CH8 left, CH9-CH16 right) and the short-channel chips.

    The display controls live in `controls` (the window puts them in the left panel). Plots
    stretch with the window. Quality is shown on each plot: a gap is missing or invalid data; a
    line that stays drawn carries markers for warnings (LOW_SIGNAL, LEAK_WARN) and for a channel
    shown without short-separation correction."""

    selected = QtCore.Signal(str)
    detail_changed = QtCore.Signal()

    def __init__(self, geometry, parent=None):
        super().__init__(parent)
        self.geometry = geometry
        self.fs = 10.0
        self.mode = "Hb"
        self.window_s = 30
        self.scale = None
        self.current = "CH1"
        self._t: deque = deque()
        self._pts: dict = {c.name: deque() for c in geometry.channels}
        self._dirty = False
        self._epoch = None
        self.channels = {c.name: c for c in geometry.channels}
        self.longs = [c.name for c in geometry.channels if c.kind == "long"]
        self.shorts = [c.name for c in geometry.channels if c.kind == "short"]
        self.setObjectName("page")           # white panel, whatever the system theme
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)
        self.banner = QtWidgets.QLabel(f"{PREVIEW_LABEL} · Δ = perubahan dari baseline")
        self.banner.setObjectName("previewbanner")
        outer.addWidget(self.banner)
        self.geometry_note = QtWidgets.QLabel()          # long text: shown in the left panel
        self.geometry_note.setWordWrap(True)
        self.geometry_note.setStyleSheet("font-size: 11px;")

        # controls (placed in the left panel by the window)
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
        form.addRow("Display", self.mode_combo)
        form.addRow("Sweep", self.window_combo)
        form.addRow("Scale", self.scale_combo)
        form.addRow(self.geometry_note)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.window_combo.currentIndexChanged.connect(self._window_changed)
        self.scale_combo.currentIndexChanged.connect(self._scale_changed)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(2)
        outer.addLayout(grid, 1)
        grid.addWidget(QtWidgets.QLabel("<b>Left hemisphere · CH1–CH8</b>"), 0, 0)
        grid.addWidget(QtWidgets.QLabel("<b>Right hemisphere · CH9–CH16</b>"), 0, 1)
        self.plots, self.curves, self.clamps, self.cursors, self.warns, self.uncorr = {}, {}, {}, {}, {}, {}
        half = len(self.longs) // 2
        for i, name in enumerate(self.longs):
            col, row = (0, i + 1) if i < half else (1, i - half + 1)
            plot = self._make_plot(name)
            if row != half:  # one time axis under the last row; all rows share the same sweep
                plot.getPlotItem().hideAxis("bottom")
            else:
                plot.getPlotItem().setLabel("bottom", "s (sweep time)")
            grid.addWidget(plot, row, col)
            grid.setRowStretch(row, 14 if row == half else 10)   # the last row also holds the time axis
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        chips = QtWidgets.QHBoxLayout()
        outer.addLayout(chips)
        chips.addWidget(QtWidgets.QLabel("Short channels"))
        self.chips = {}
        for name in self.shorts:
            btn = QtWidgets.QPushButton(name)
            btn.clicked.connect(lambda _=False, n=name: self.select(n))
            self.chips[name] = btn
            chips.addWidget(btn)
        chips.addStretch()
        self.legend = QtWidgets.QLabel()
        self.legend.setWordWrap(True)
        outer.addWidget(self.legend)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self._mode_changed()

    # ---- construction ----------------------------------------------------
    def _make_plot(self, name: str) -> pg.PlotWidget:
        ch = self.channels[name]
        w = pg.PlotWidget(background="#ffffff")
        w.setMinimumHeight(24)
        w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        w.setMenuEnabled(False)
        w.setMouseEnabled(False, False)
        w.hideButtons()
        item = w.getPlotItem()
        item.showGrid(x=True, y=True, alpha=0.25)
        item.setContentsMargins(0, 0, 4, 0)
        item.setTitle(self._title(ch, ""), size="9pt", color="#102f4b")
        item.getAxis("left").setWidth(46)
        font = w.font()
        font.setPointSize(8)
        for ax in ("left", "bottom"):
            item.getAxis(ax).setStyle(tickFont=font)
            item.getAxis(ax).setTextPen("#102f4b")
        width = 2.0
        curves = [pg.PlotDataItem(connect="finite", pen=pg.mkPen(WL_COLORS[0], width=width)),
                  pg.PlotDataItem(connect="finite", pen=pg.mkPen(WL_COLORS[1], width=width)),
                  pg.PlotDataItem(connect="finite", pen=pg.mkPen(WL_COLORS[0], width=1.2, style=QtCore.Qt.PenStyle.DotLine)),
                  pg.PlotDataItem(connect="finite", pen=pg.mkPen(WL_COLORS[1], width=1.2, style=QtCore.Qt.PenStyle.DotLine))]
        for c in curves:
            item.addItem(c)
        clamp = pg.ScatterPlotItem(size=6, pen=pg.mkPen("#d00000"), brush=pg.mkBrush("#d00000"))
        warn = pg.ScatterPlotItem(size=5, symbol="t1", pen=pg.mkPen("#a35b00", width=0.6),
                                  brush=pg.mkBrush(242, 161, 0, 190))
        uncorr = pg.ScatterPlotItem(size=5, symbol="d", pen=pg.mkPen("#5b2d91", width=0.6),
                                    brush=pg.mkBrush(155, 111, 208, 190))
        for extra in (clamp, warn, uncorr):
            item.addItem(extra)
        cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen("#7a8aa0", width=1))
        item.addItem(cursor)
        w.scene().sigMouseClicked.connect(lambda _e, n=name: self.select(n))
        self.plots[name], self.curves[name], self.clamps[name], self.cursors[name] = w, curves, clamp, cursor
        self.warns[name], self.uncorr[name] = warn, uncorr
        return w

    def _title(self, ch, status: str) -> str:
        base = f"{ch.name} · {ch.sd_mm:g} mm"
        return base + (f"  {status}" if status else "")

    # ---- controls --------------------------------------------------------
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
        names = {"Hb": ("ΔHbO", "ΔHbR"), "OD": ("OD 735", "OD 850"), "I": ("I 735", "I 850")}[self.mode]
        c1, c2 = (HB_COLORS if self.mode == "Hb" else WL_COLORS)
        self.legend.setText(
            f"<span style='color:{c1}'><b>━ {names[0]}</b></span> &nbsp; <span style='color:{c2}'>"
            f"<b>━ {names[1]}</b></span> &nbsp;·&nbsp; <b>celah</b> = data hilang atau invalid "
            f"&nbsp;·&nbsp; <span style='color:#a35b00'><b>▲</b></span> peringatan (LOW_SIGNAL / "
            f"LEAK_WARN), garis tetap tampil &nbsp;·&nbsp; <span style='color:#5b2d91'><b>◆</b></span> "
            f"tanpa koreksi SCR &nbsp;·&nbsp; label 735/850 nominal")
        self._dirty = True

    def _window_changed(self, *_):
        self.window_s = self.window_combo.currentData()
        self._dirty = True

    def _scale_changed(self, *_):
        self.scale = self.scale_combo.currentData()
        self._dirty = True

    def set_geometry_note(self, text: str) -> None:
        self.geometry_note.setText(text)
        self.banner.setToolTip(text)

    # ---- data ------------------------------------------------------------
    def clear(self) -> None:
        self._t.clear()
        for d in self._pts.values():
            d.clear()
        self._dirty = True

    def reset_for_epoch(self, epoch) -> None:
        if epoch != self._epoch:
            self._epoch = epoch
            self.clear()

    def push(self, frames) -> None:
        for fr in frames:
            self._t.append(fr.t_s)
            for name, d in self._pts.items():
                d.append(fr.points[name])
        while self._t and self._t[-1] - self._t[0] > RETAIN_S:
            self._t.popleft()
            for d in self._pts.values():
                d.popleft()
        if frames:
            self._dirty = True

    def _values(self, p):
        if self.mode == "Hb":
            return p.d_hbo, p.d_hbr
        if self.mode == "OD":
            return p.od735, p.od850
        return p.i735, p.i850

    def _build(self, name: str):
        """Time, both series (NaN breaks at time holes) and the per-point warning flags."""
        t = list(self._t)
        pts = list(self._pts[name])
        ts, y1, y2, warn, unc = [], [], [], [], []
        period = 1.0 / self.fs
        for i, (ti, p) in enumerate(zip(t, pts)):
            if i and ti - t[i - 1] > GAP_FACTOR * period:
                ts.append(t[i - 1] + period)
                y1.append(math.nan)
                y2.append(math.nan)
                warn.append(False)
                unc.append(False)
            a, b = self._values(p)
            ts.append(ti)
            y1.append(a)
            y2.append(b)
            shown = p.state == "VALID"
            warn.append(shown and bool(p.warnings))
            unc.append(shown and p.ssr_state == "UNCORRECTED")
        return (np.asarray(ts), np.asarray(y1), np.asarray(y2),
                np.asarray(warn, dtype=bool), np.asarray(unc, dtype=bool))

    def series(self, name: str):
        """(t, y1, y2) with NaN breaks inserted at time gaps."""
        return self._build(name)[:3]

    def flags(self, name: str):
        """(warning mask, uncorrected mask) aligned with `series`."""
        return self._build(name)[3:]

    def finite_points(self, name: str) -> int:
        _, a, b = self.series(name)
        return int(np.isfinite(a).sum() + np.isfinite(b).sum())

    def clamped_points(self, name: str) -> int:
        return len(self.clamps[name].data) if self.clamps[name].data is not None else 0

    def marker_counts(self, name: str) -> tuple:
        """(warning markers, uncorrected markers) currently drawn on the plot."""
        return len(self.warns[name].data), len(self.uncorr[name].data)

    def refresh(self) -> None:
        if not self._dirty or not self._t:
            return
        self._dirty = False
        w = float(self.window_s)
        t_last = self._t[-1]
        cur_pass = math.floor(t_last / w)
        cursor_x = t_last - cur_pass * w
        erase = w * ERASE_FRACTION
        for name in self.longs:
            t, y1, y2, warn, unc = self._build(name)
            ys = [y1, y2]
            clamp_xy = []
            passes = np.floor(t / w)
            x = t - passes * w
            new = passes == cur_pass
            old = (passes == cur_pass - 1) & (x > cursor_x + erase)
            limits = None
            if isinstance(self.scale, (int, float)):
                limits = (-float(self.scale), float(self.scale))
            elif self.scale is None:
                limits = self._robust_limits(ys, new | old)
            if limits is not None:
                lo, hi = limits
                for k, y in enumerate(ys):
                    over = np.isfinite(y) & ((y < lo) | (y > hi))
                    clamp_xy.append((t[over], np.clip(y[over], lo, hi)))
                    ys[k] = np.clip(y, lo, hi)
            c = self.curves[name]
            for k in range(2):
                self._set(c[k], x, ys[k], new)
                self._set(c[k + 2], x, ys[k], old)
            shown = new | old
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
            item = self.plots[name].getPlotItem()
            item.setXRange(0, w, padding=0)
            if limits is not None:
                item.setYRange(limits[0], limits[1], padding=0)
            else:
                item.enableAutoRange("y", True)
            last = self._pts[name][-1]
            if last.state == "INVALID":
                mark, color = "✕", "#992231"
            elif last.state == "VALID" and (last.warnings or last.ssr_state == "UNCORRECTED"):
                mark, color = ("▲" if last.warnings else "◆"), "#a35b00"
            elif last.state == "BASELINE":
                mark, color = "…", "#526a80"
            else:
                mark, color = "", "#102f4b"
            item.setTitle(self._title(self.channels[name], mark), size="9pt", color=color)
            self.plots[name].setToolTip("\n".join(f"{k}: {v}" for k, v in self.detail_rows(name)))
        for name, btn in self.chips.items():
            last = self._pts[name][-1]
            btn.setToolTip("\n".join(f"{k}: {v}" for k, v in self.detail_rows(name)))
            bad = last.state == "INVALID"
            btn.setStyleSheet("color:#992231;" if bad else ("color:#a35b00;" if last.warnings else ""))
        self.detail_changed.emit()

    @staticmethod
    def _robust_limits(ys, shown):
        """Central part of the visible data plus a margin; None when there is nothing to scale."""
        vals = np.concatenate([y[shown & np.isfinite(y)] for y in ys])
        if vals.size < 4:
            return None
        lo, hi = np.percentile(vals, ROBUST_PERCENTILES)
        span = hi - lo
        if span <= 1e-12 * max(1.0, abs(hi), abs(lo)):
            span = max(abs(hi), 1e-9) * 0.05 + 1e-12
        return lo - ROBUST_MARGIN * span, hi + ROBUST_MARGIN * span

    @staticmethod
    def _set(curve, x, y, mask):
        if mask.any():
            curve.setData(x[mask], y[mask])
        else:
            curve.setData([], [])

    # ---- detail ----------------------------------------------------------
    def select(self, name: str) -> None:
        if name in self.channels:
            self.current = name
            self.selected.emit(name)
            self._dirty = True
            self.refresh()
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

    def finite_channels(self) -> int:
        return sum(1 for n in self.longs if self.finite_points(n) > 0)
