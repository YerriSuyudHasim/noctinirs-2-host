"""Theme and motion preferences: live switching, persistence (QSettings), reduce motion."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6 import QtCore

from . import tokens as T
from .qss import build_qss

KEY_THEME = "ui/theme"
KEY_REDUCE_MOTION = "ui/reduce_motion"


def default_settings() -> QtCore.QSettings:
    return QtCore.QSettings("NoctiNIRS", "NoctiNIRS2Host")


class ThemeManager(QtCore.QObject):
    """One per window. Anything themed connects to `changed`; anything animated asks `ms()`."""

    changed = QtCore.Signal(object)       # the new Tokens
    motion_changed = QtCore.Signal(bool)  # reduce motion on/off

    def __init__(self, settings: QtCore.QSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings if settings is not None else default_settings()
        name = str(self.settings.value(KEY_THEME, T.DEFAULT_THEME))
        self._tokens = T.THEMES.get(name, T.THEMES[T.DEFAULT_THEME])
        self._reduce = self._as_bool(self.settings.value(KEY_REDUCE_MOTION, False))

    @staticmethod
    def _as_bool(value) -> bool:
        return value is True or str(value).lower() in ("true", "1", "yes")

    @property
    def tokens(self) -> T.Tokens:
        return self._tokens

    @property
    def name(self) -> str:
        return self._tokens.name

    @property
    def reduce_motion(self) -> bool:
        return self._reduce

    def _check_icon(self) -> str | None:
        """A check mark in the colour that sits on the accent, drawn once per theme into the temp folder."""
        from PySide6 import QtGui
        try:
            folder = Path(tempfile.gettempdir()) / "noctinirs_ui"
            folder.mkdir(exist_ok=True)
            path = folder / f"check_{self._tokens.name}.png"
            if not path.exists():
                pix = QtGui.QPixmap(16, 16)
                pix.fill(QtCore.Qt.GlobalColor.transparent)
                painter = QtGui.QPainter(pix)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                pen = QtGui.QPen(QtGui.QColor(self._tokens.accent_text), 2.2)
                pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolyline([QtCore.QPointF(3.5, 8.5), QtCore.QPointF(6.7, 11.5), QtCore.QPointF(12.5, 4.5)])
                painter.end()
                pix.save(str(path), "PNG")
            return path.as_posix()
        except OSError:
            return None

    def qss(self) -> str:
        return build_qss(self._tokens, self._check_icon())

    def ms(self, duration: int) -> int:
        """A motion duration, or 0 when the user asked for reduced motion."""
        return 0 if self._reduce else duration

    def set_theme(self, name: str) -> None:
        if name not in T.THEMES:
            raise ValueError(f"unknown theme {name!r}")
        if name == self._tokens.name:
            return
        self._tokens = T.THEMES[name]
        self.settings.setValue(KEY_THEME, name)
        self.settings.sync()
        self.changed.emit(self._tokens)

    def toggle(self) -> None:
        self.set_theme("dark" if self._tokens.name == "light" else "light")

    def set_reduce_motion(self, on: bool) -> None:
        on = bool(on)
        if on == self._reduce:
            return
        self._reduce = on
        self.settings.setValue(KEY_REDUCE_MOTION, on)
        self.settings.sync()
        self.motion_changed.emit(on)


def build_palette(t: T.Tokens):
    """A QPalette from the tokens, for the widgets that no style sheet rule reaches (menus, dialogs)."""
    from PySide6 import QtGui
    pal = QtGui.QPalette()
    roles = QtGui.QPalette.ColorRole
    for role, color in ((roles.Window, t.background), (roles.WindowText, t.text_primary),
                        (roles.Base, t.surface), (roles.AlternateBase, t.surface_raised),
                        (roles.Text, t.text_primary), (roles.Button, t.surface),
                        (roles.ButtonText, t.text_primary), (roles.ToolTipBase, t.surface_raised),
                        (roles.ToolTipText, t.text_primary), (roles.Highlight, t.accent),
                        (roles.HighlightedText, t.accent_text), (roles.PlaceholderText, t.text_secondary),
                        (roles.Link, t.accent)):
        pal.setColor(role, QtGui.QColor(color))
    disabled = QtGui.QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(disabled, role, QtGui.QColor(t.text_secondary))
    return pal
