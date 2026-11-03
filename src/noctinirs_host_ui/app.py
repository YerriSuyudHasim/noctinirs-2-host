"""Main window: banner, S1 control panel (simulated device), demo plot, About dialog."""

from __future__ import annotations

import sys
import tempfile
from collections import deque
from pathlib import Path

# Import PySide6 before pyqtgraph so that pyqtgraph binds to PySide6.
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
import PySide6

from noctinirs_host import __version__ as CORE_VERSION
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.control.simulator import DeviceSimulator

from .control_panel import ControlPanel
from .credits import APP_NAME, BANNER_TEXT, SYNTHETIC_NOTICE, about_text
from .theme import THEMES, build_qss
from .synthetic import SAMPLE_RATE_HZ, WINDOW_SECONDS, SyntheticSource

_MAX_POINTS = int(SAMPLE_RATE_HZ * WINDOW_SECONDS)


def _arrow_images(theme) -> tuple[str | None, str | None]:
    """Draw the two small arrow icons for spin/combo boxes in the theme's muted colour.
    Returns forward-slash paths, or (None, None) if the files cannot be written."""
    try:
        folder = Path(tempfile.gettempdir()) / "noctinirs_host_ui"
        folder.mkdir(exist_ok=True)
        paths = []
        for kind in ("up", "down"):
            pm = QtGui.QPixmap(9, 6)
            pm.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(theme.muted))
            pts = [QtCore.QPointF(0.5, 5.5), QtCore.QPointF(8.5, 5.5), QtCore.QPointF(4.5, 0.5)]
            if kind == "down":
                pts = [QtCore.QPointF(0.5, 0.5), QtCore.QPointF(8.5, 0.5), QtCore.QPointF(4.5, 5.5)]
            painter.drawPolygon(QtGui.QPolygonF(pts))
            painter.end()
            path = folder / f"arrow_{kind}_{theme.name}.png"
            if not pm.save(str(path)):
                return None, None
            paths.append(path.as_posix())
        return paths[0], paths[1]
    except OSError:
        return None, None


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} (S1: simulated device)")
        self.resize(1240, 880)

        self._simulator = DeviceSimulator()
        self.controller = SessionController(LoopbackTransport(self._simulator))
        self.panel = ControlPanel(self.controller)
        self._source = SyntheticSource()
        self._t: deque[float] = deque(maxlen=_MAX_POINTS)
        self._y: deque[float] = deque(maxlen=_MAX_POINTS)

        # --- header: app name + the labels that must always be visible ---
        title = QtWidgets.QLabel(APP_NAME)
        title.setObjectName("title")
        banner = QtWidgets.QLabel(BANNER_TEXT)
        banner.setObjectName("banner")  # research prototype, non-clinical
        synthetic = QtWidgets.QLabel(SYNTHETIC_NOTICE)
        synthetic.setObjectName("synthetic_notice")
        header = QtWidgets.QFrame()
        header.setObjectName("header")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(20, 10, 20, 10)
        hl.setSpacing(14)
        hl.addWidget(title)
        hl.addWidget(banner)
        hl.addStretch(1)
        hl.addWidget(synthetic)
        self.dark_btn = QtWidgets.QPushButton("Dark mode: Off")
        self.dark_btn.setObjectName("dark_mode_button")
        self.dark_btn.setCheckable(True)
        self.dark_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.dark_btn.setToolTip("Switch between the light and the dark theme")
        self.dark_btn.toggled.connect(lambda on: self.set_theme("dark" if on else "light"))
        hl.addWidget(self.dark_btn)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Intensity (arbitrary units, synthetic)")
        self.plot.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.plot.showGrid(x=False, y=True, alpha=0.18)
        self.plot.getPlotItem().hideButtons()
        self.curve = self.plot.plot([], [], pen=pg.mkPen(width=2))

        central = QtWidgets.QWidget()
        central.setObjectName("root")
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(16)
        self.panel.setMinimumWidth(380)
        self.panel.setMaximumWidth(440)
        body.addWidget(self.panel)
        plot_card = QtWidgets.QFrame()
        plot_card.setObjectName("card")
        pl = QtWidgets.QVBoxLayout(plot_card)
        pl.setContentsMargins(16, 14, 16, 12)
        pl.setSpacing(8)
        self.plot_note = QtWidgets.QLabel("Demo signal only - the simulated device sends no measurement data yet.")
        self.plot_note.setObjectName("plot_note")
        pl.addWidget(self.plot_note)
        pl.addWidget(self.plot, stretch=1)
        body.addWidget(plot_card, stretch=1)
        outer.addLayout(body, stretch=1)
        self.setCentralWidget(central)

        view_menu = self.menuBar().addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        group = QtGui.QActionGroup(self)
        self._theme_actions = {}
        for key, label in (("light", "&Light"), ("dark", "&Dark")):
            act = theme_menu.addAction(label)
            act.setCheckable(True)
            act.setActionGroup(group)
            act.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            self._theme_actions[key] = act
        self.theme_name = "light"
        self.set_theme("light")

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self.show_about)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(1000 / SAMPLE_RATE_HZ))
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()
        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self.panel.tick)
        self._ui_timer.start()

    def set_theme(self, name: str) -> None:
        """Apply a colour theme to the whole application and to the plot."""
        theme = THEMES[name]
        self.theme_name = name
        self._theme_actions[name].setChecked(True)
        on = name == "dark"  # keep the header button in step with the menu
        self.dark_btn.blockSignals(True)
        self.dark_btn.setChecked(on)
        self.dark_btn.setText("Dark mode: On" if on else "Dark mode: Off")
        self.dark_btn.blockSignals(False)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            up, down = _arrow_images(theme)
            app.setStyleSheet(build_qss(theme, up, down))
        self.plot.setBackground(theme.plot_bg)
        axis_pen = pg.mkPen(theme.plot_axis)
        for side in ("left", "bottom"):
            ax = self.plot.getAxis(side)
            ax.setPen(axis_pen)
            ax.setTextPen(axis_pen)
        self.plot.getPlotItem().getViewBox().setBorder(None)
        self.curve.setPen(pg.mkPen(theme.accent, width=2))

    def _on_timer(self) -> None:
        if self.panel.is_streaming():  # demo curve moves only while the device streams
            self.tick()

    def tick(self) -> None:
        """Append one synthetic sample and redraw."""
        t, y = self._source.next_sample()
        self._t.append(t)
        self._y.append(y)
        self.curve.setData(list(self._t), list(self._y))

    @staticmethod
    def about_message() -> str:
        return about_text(CORE_VERSION, QtCore.qVersion(), PySide6.__version__)

    def show_about(self) -> None:
        QtWidgets.QMessageBox.about(self, f"About {APP_NAME}", self.about_message())


def run_self_test(window: MainWindow, out_path: str | None) -> int:
    """Draw a few samples, then report. Used to smoke-test a built bundle."""
    for _ in range(5):
        window.tick()
    ok = len(window._t) == 5 and bool(window.about_message())
    message = f"SELFTEST {'OK' if ok else 'FAIL'} core={CORE_VERSION} qt={QtCore.qVersion()}"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(message + "\n")
    else:
        print(message)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    self_test = "--self-test" in args
    out_path = None
    if self_test:
        i = args.index("--self-test")
        out_path = args[i + 1] if i + 1 < len(args) else None

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    window = MainWindow()
    if self_test:
        window.show()
        code = run_self_test(window, out_path)
        window.close()
        return code
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
