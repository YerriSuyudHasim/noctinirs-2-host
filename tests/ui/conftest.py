"""Qt test hygiene: closed windows must really be destroyed.

A window that is closed but kept alive is still restyled whenever the application
style sheet changes, so test time grows with every leaked window.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _flush_deleted_windows():
    yield
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        for w in QtWidgets.QApplication.topLevelWidgets():
            w.close()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
