"""Small shared widgets. Text that may hold long unbroken tokens is never clipped."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class AutoText(QtWidgets.QTextEdit):
    """Read-only (or editable) text that wraps anywhere and grows to show all of it."""

    def __init__(self, text: str = "", *, readonly: bool = True, min_lines: int = 1, parent=None):
        super().__init__(parent)
        self.setReadOnly(readonly)
        self.setAcceptRichText(False)
        self.setWordWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self._min_lines = min_lines
        self.textChanged.connect(self._fit)
        if text:
            self.setPlainText(text)
        self._fit()

    def set_text(self, text: str) -> None:
        if self.toPlainText() != text:
            self.setPlainText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        width = self.viewport().width()
        if width <= 20:
            width = max(self.width() - 2 * self.frameWidth(), 200)
        doc = self.document()
        doc.setTextWidth(width)
        lines_h = self.fontMetrics().lineSpacing() * self._min_lines
        height = int(max(doc.size().height(), lines_h)) + 2 * self.frameWidth() + 8
        if self.height() != height:
            self.setFixedHeight(height)

    def fully_visible(self) -> bool:
        """True when the box is tall enough for all wrapped text (nothing hidden)."""
        self._fit()
        return int(self.document().size().height()) <= self.height()
