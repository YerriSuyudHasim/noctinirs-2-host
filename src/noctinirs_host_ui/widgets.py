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


class Section(QtWidgets.QWidget):
    """A titled block of the left panel that can be folded away."""

    def __init__(self, title: str, content: QtWidgets.QWidget, *, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.content = content
        self.button = QtWidgets.QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.button.setStyleSheet("QToolButton { text-align: left; font-weight: bold; border: 0; "
                                  "border-bottom: 1px solid #cfe0ef; padding: 4px 2px; }")
        self.button.toggled.connect(self._toggled)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        lay.addWidget(self.button)
        lay.addWidget(content)
        self._toggled(expanded)

    def _toggled(self, on: bool) -> None:
        self.button.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)
        self.content.setVisible(on)

    @property
    def expanded(self) -> bool:
        return self.button.isChecked()
