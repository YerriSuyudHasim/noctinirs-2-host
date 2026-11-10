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


class WrapLabel(QtWidgets.QLabel):
    """A word-wrapped label that sizes its own height to the width the layout gives it, so its complete
    text is always visible (a plain wrapped label inside a horizontal layout is clipped instead)."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)

        self._fitting = False

    def _fit(self) -> None:
        if self._fitting:                    # setFixedHeight re-enters the layout: never recurse
            return
        self._fitting = True
        try:
            h = self.heightForWidth(max(self.width(), 1))
            if h > 0 and self.minimumHeight() != h:
                self.setFixedHeight(h)
        finally:
            self._fitting = False

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._fit()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        super().setText(text)
        self._fit()


class ElidedLabel(QtWidgets.QLabel):
    """One line of text, elided with an ellipsis when it does not fit; the full text is the tooltip."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._full = text
        super().setText(text)
        self._update_tip()
        self.update()

    def _update_tip(self) -> None:
        tip = self._full if self.is_elided() else ""
        if self.toolTip() != tip:
            self.setToolTip(tip)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(event)
        self._update_tip()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().showEvent(event)
        self._update_tip()

    def full_text(self) -> str:
        return self._full

    def is_elided(self) -> bool:
        avail = self.contentsRect().width() - 2 * self.margin()
        return self.fontMetrics().horizontalAdvance(self._full) > max(avail, 0)

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(40, super().minimumSizeHint().height())

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self._full) + 2 * self.margin() + 4,
                            super().sizeHint().height())

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        # the background and frame come from the style sheet, then the elided text
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        rect = self.contentsRect().adjusted(self.margin(), 0, -self.margin(), 0)
        shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.TextElideMode.ElideRight, rect.width())
        painter.setPen(self.palette().color(QtGui.QPalette.ColorRole.WindowText))
        painter.drawText(rect, int(self.alignment()) | int(QtCore.Qt.AlignmentFlag.AlignVCenter), shown)


class StatusChip(QtWidgets.QLabel):
    """A rounded status pill. Its colour follows a tone and changes with a short animation."""

    TONES = ("neutral", "ok", "warn", "danger", "accent")

    def __init__(self, text: str = "", theme=None, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statuschip")
        self._theme = theme
        self._tone = "neutral"
        self._bg = QtGui.QColor()
        self._target_bg = QtGui.QColor()
        self._border = QtGui.QColor()
        self._anim: QtCore.QVariantAnimation | None = None
        self.setContentsMargins(12, 5, 12, 5)
        self.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self._apply(animate=False)
        if theme is not None:
            theme.changed.connect(lambda _t: self._apply(animate=False))
            theme.motion_changed.connect(self._motion_changed)

    @property
    def tone(self) -> str:
        return self._tone

    @property
    def animating(self) -> bool:
        return self._anim is not None and self._anim.state() == QtCore.QAbstractAnimation.State.Running

    def set_tone(self, tone: str) -> None:
        if tone not in self.TONES:
            raise ValueError(tone)
        if tone != self._tone:
            self._tone = tone
            self._apply(animate=True)

    def _colors(self):
        if self._theme is not None:
            t = self._theme.tokens
        else:
            from .design.tokens import LIGHT as t  # noqa: N811
        return {"neutral": (t.surface, t.text_primary, t.border),
                "ok": (t.success_bg, t.success, t.success),
                "warn": (t.warning_bg, t.warning, t.warning),
                "danger": (t.danger_bg, t.danger, t.danger),
                "accent": (t.accent, t.accent_text, t.accent)}[self._tone]

    def _apply(self, *, animate: bool) -> None:
        bg, fg, border = (QtGui.QColor(c) for c in self._colors())
        self.setStyleSheet(f"QLabel#statuschip {{ color: {fg.name()}; background: transparent; border: 0; }}")
        self._border = border
        self._target_bg = bg
        ms = self._theme.ms(150) if self._theme is not None else 0
        if self._anim is not None:
            self._anim.stop()
        if not animate or ms == 0 or not self.isVisible() or not self._bg.isValid():
            self._bg = bg
            self.update()
            return
        if self._anim is None:          # one animation object for the life of the chip (no per-change garbage)
            self._anim = QtCore.QVariantAnimation(self)
            self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._anim.valueChanged.connect(self._step)
        self._anim.setStartValue(self._bg)
        self._anim.setEndValue(bg)
        self._anim.setDuration(ms)
        self._anim.start()

    def _motion_changed(self, reduced: bool) -> None:
        """Reduce Motion takes effect at once: an in-flight colour change jumps to its end colour."""
        if reduced and self._anim is not None:
            self._anim.stop()
            self._bg = QtGui.QColor(self._target_bg)
            self.update()

    def _step(self, value) -> None:
        self._bg = QtGui.QColor(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QtGui.QPen(self._border))
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()
        super().paintEvent(event)


class Section(QtWidgets.QWidget):
    """A titled block that folds away. The height change is animated unless motion is reduced."""

    def __init__(self, title: str, content: QtWidgets.QWidget, *, expanded: bool = True, theme=None,
                 parent=None) -> None:
        super().__init__(parent)
        self.content = content
        self._theme = theme
        self._anim: QtCore.QPropertyAnimation | None = None
        self._end_visible = expanded
        self.button = QtWidgets.QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.button.setStyleSheet("QToolButton { text-align: left; font-weight: 600; }")
        self.button.toggled.connect(self._toggled)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(4)
        lay.addWidget(self.button)
        lay.addWidget(content)
        self._set_arrow(expanded)
        content.setVisible(expanded)
        if theme is not None:
            theme.motion_changed.connect(self._motion_changed)

    @property
    def animating(self) -> bool:
        return self._anim is not None and self._anim.state() == QtCore.QAbstractAnimation.State.Running

    def _set_arrow(self, on: bool) -> None:
        self.button.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

    def _toggled(self, on: bool) -> None:
        self._set_arrow(on)
        if self._anim is not None:
            self._anim.stop()
        self._end_visible = on
        ms = self._theme.ms(200) if self._theme is not None else 0
        if ms == 0 or not self.isVisible():
            self._settle()
            return
        start = self.content.height() if self.content.isVisible() else 0
        if on:
            self.content.setMaximumHeight(0)
            self.content.setVisible(True)
            end = max(self.content.sizeHint().height(), 1)
        else:
            end = 0
        if self._anim is None:          # one animation object per section, reused by every fold
            self._anim = QtCore.QPropertyAnimation(self.content, b"maximumHeight", self)
            self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._anim.finished.connect(self._settle)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setDuration(ms)
        self._anim.start()

    def _settle(self) -> None:
        self.content.setMaximumHeight(16777215)
        self.content.setVisible(self._end_visible)

    def _motion_changed(self, reduced: bool) -> None:
        if reduced and self.animating:
            self._anim.stop()
            self._settle()

    @property
    def expanded(self) -> bool:
        return self.button.isChecked()


class SmoothScrollArea(QtWidgets.QScrollArea):
    """A vertical scroll area whose mouse wheel eases to its target (only the scroll position moves)."""

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._target = 0
        self.stops = None                    # optional callable -> scroll positions the wheel snaps between
        self._anim: QtCore.QPropertyAnimation | None = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(48)
        if theme is not None:
            theme.motion_changed.connect(self._motion_changed)

    def scroll_to(self, value: int) -> None:
        bar = self.verticalScrollBar()
        value = max(bar.minimum(), min(bar.maximum(), int(value)))
        self._target = value
        ms = self._theme.ms(180) if self._theme is not None else 0
        if self._anim is not None:
            self._anim.stop()
        if ms == 0 or not self.isVisible():
            bar.setValue(value)
            return
        if self._anim is None:          # one animation object, retargeted for every scroll
            self._anim = QtCore.QPropertyAnimation(bar, b"value", self)
            self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(value)
        self._anim.setDuration(ms)
        self._anim.start()

    def _motion_changed(self, reduced: bool) -> None:
        if reduced and self._anim is not None and self._anim.state() == QtCore.QAbstractAnimation.State.Running:
            self._anim.stop()
            self.verticalScrollBar().setValue(self._target)

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if delta == 0 or bar.maximum() == 0:
            event.ignore()
            return
        running = self._anim is not None and self._anim.state() == QtCore.QAbstractAnimation.State.Running
        base = self._target if running else bar.value()
        if self.stops is not None:           # row-aligned: one wheel notch moves to the next stop
            target = base
            for _ in range(max(1, round(abs(delta) / 120))):
                stops = sorted(self.stops())
                if delta < 0:
                    later = [v for v in stops if v > target + 1]
                    target = later[0] if later else target
                else:
                    earlier = [v for v in stops if v < target - 1]
                    target = earlier[-1] if earlier else target
            self.scroll_to(target)
        else:
            self.scroll_to(base - int(delta / 120 * 3 * bar.singleStep()))
        event.accept()


class FadeStack(QtWidgets.QStackedWidget):
    """A stacked widget whose pages fade in (200 ms). A page may name a smaller `fade_target`: the
    Live page does, so the graphs themselves never animate. Reduce motion switches instantly."""

    def __init__(self, theme=None, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._anim: QtCore.QPropertyAnimation | None = None
        self._target: QtWidgets.QWidget | None = None
        self.transitions_started = 0
        if theme is not None:
            theme.motion_changed.connect(self._motion_changed)

    @property
    def animating(self) -> bool:
        return self._anim is not None and self._anim.state() == QtCore.QAbstractAnimation.State.Running

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (Qt name)
        same = index == self.currentIndex()
        super().setCurrentIndex(index)
        ms = self._theme.ms(200) if self._theme is not None else 0
        if same or ms == 0 or not self.isVisible():
            return
        page = self.widget(index)
        target = getattr(page, "fade_target", page)
        self._finish()
        effect = QtWidgets.QGraphicsOpacityEffect(target)
        effect.setOpacity(0.0)
        target.setGraphicsEffect(effect)
        if self._anim is None:          # one animation object for the stack; only its target effect changes
            self._anim = QtCore.QPropertyAnimation(self)
            self._anim.setPropertyName(b"opacity")
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._anim.finished.connect(self._finish)
        self._anim.setTargetObject(effect)
        self._anim.setDuration(ms)
        self._target = target
        self._anim.start()
        self.transitions_started += 1

    def _motion_changed(self, reduced: bool) -> None:
        if reduced:
            self._finish()

    def _finish(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.setTargetObject(None)
        if self._target is not None:
            try:
                self._target.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self._target = None
