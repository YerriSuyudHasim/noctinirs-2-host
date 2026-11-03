"""Qt window tests. Skipped when PySide6/pyqtgraph are not installed (core-only CI)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from noctinirs_host_ui.app import MainWindow, main, run_self_test  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_window_shows_banner_and_synthetic_notice(qapp) -> None:
    w = MainWindow()
    banner = w.findChild(QtWidgets.QLabel, "banner")
    notice = w.findChild(QtWidgets.QLabel, "synthetic_notice")
    assert "research prototype" in banner.text().lower() and "non-clinical" in banner.text().lower()
    assert "SYNTHETIC" in notice.text()
    w.close()


def test_live_view_is_wired_to_the_simulated_stream(qapp) -> None:
    w = MainWindow()
    w._timer.stop()
    w._ui_timer.stop()
    assert w.controller.connect().ok and w.controller.start().ok
    assert w.live.receiver is w.receiver and w.controller.frame_sink == w.receiver.feed
    assert "SIMULATED" in w.plot_note.text()
    w.close()


def test_about_message_credits_components(qapp) -> None:
    text = MainWindow.about_message()
    assert "PySide6" in text and "pyqtgraph" in text and "NumPy" in text


def test_self_test_mode_writes_ok_and_exits_zero(qapp, tmp_path) -> None:
    out = tmp_path / "selftest.txt"
    assert main(["--self-test", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("SELFTEST OK")


def test_run_self_test_prints_when_no_path(qapp, capsys) -> None:
    w = MainWindow()
    assert run_self_test(w, None) == 0
    assert "SELFTEST OK" in capsys.readouterr().out
    w.close()
