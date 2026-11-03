"""Main window for the S0 spike: banner, one synthetic plot, About dialog."""

from __future__ import annotations

import sys
from collections import deque

# Import PySide6 before pyqtgraph so that pyqtgraph binds to PySide6.
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg
import PySide6

from noctinirs_host import __version__ as CORE_VERSION

from .credits import APP_NAME, BANNER_TEXT, SYNTHETIC_NOTICE, about_text
from .synthetic import SAMPLE_RATE_HZ, WINDOW_SECONDS, SyntheticSource

_MAX_POINTS = int(SAMPLE_RATE_HZ * WINDOW_SECONDS)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} (S0 spike)")
        self.resize(900, 560)

        self._source = SyntheticSource()
        self._t: deque[float] = deque(maxlen=_MAX_POINTS)
        self._y: deque[float] = deque(maxlen=_MAX_POINTS)

        banner = QtWidgets.QLabel(BANNER_TEXT)
        banner.setObjectName("banner")
        banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        banner.setStyleSheet(
            "background-color: #ffe08a; color: #3b2f00; font-weight: bold; padding: 6px;"
        )

        synthetic = QtWidgets.QLabel(SYNTHETIC_NOTICE)
        synthetic.setObjectName("synthetic_notice")
        synthetic.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        synthetic.setStyleSheet("color: #8a1c1c; font-weight: bold; padding: 4px;")

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Intensity (arbitrary units, synthetic)")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.plot.plot([], [], pen=pg.mkPen(width=2))

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(banner)
        layout.addWidget(synthetic)
        layout.addWidget(self.plot, stretch=1)
        self.setCentralWidget(central)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self.show_about)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(1000 / SAMPLE_RATE_HZ))
        self._timer.timeout.connect(self.tick)
        self._timer.start()

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
