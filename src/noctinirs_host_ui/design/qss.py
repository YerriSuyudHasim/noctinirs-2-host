"""Style sheet generated from the tokens (a template plus values; nothing is hand-written per theme)."""

from __future__ import annotations

from string import Template

from . import tokens as T

TEMPLATE = Template("""
* { font-family: $font; }
QWidget { color: $text_primary; font-size: ${type_s}px; }
QWidget#root { background: $background; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
QWidget#page, QWidget#card { background: $surface; border: 1px solid $border; border-radius: ${radius_l}px; }
QWidget#plotpanel { background: $surface; border: 1px solid $border; border-radius: ${radius_l}px; }
QSplitter::handle { background: transparent; width: ${space_s}px; }
QToolTip { background: $surface_raised; color: $text_primary; border: 1px solid $border; padding: 4px 8px; }

QLabel { background: transparent; }
QLabel#title { font-size: ${type_xl}px; font-weight: 600; }
QLabel#subtitle, QLabel#muted { color: $text_secondary; }
QLabel#sectiontitle { font-size: ${type_m}px; font-weight: 600; }
QLabel#banner { font-size: ${type_s}px; font-weight: 600; padding: ${space_xs}px ${space_s}px; }
QLabel#previewbanner { background: $warning_bg; color: $warning; border: 1px solid $warning;
    border-radius: ${radius_s}px; padding: ${space_xs}px ${space_s}px; font-weight: 600; }
QLabel#panelbanner { background: $warning_bg; color: $warning; border: 1px solid $warning;
    border-radius: ${radius_s}px; padding: 2px ${space_s}px; font-weight: 600; font-size: ${type_xs}px; }
QLabel#step, QLabel#feedback, QLabel#detail { background: $surface_raised; border: 1px solid $border;
    border-radius: ${radius_m}px; padding: ${space_s}px; }
QLabel#error { color: $danger; background: $danger_bg; border: 1px solid $danger;
    border-radius: ${radius_m}px; padding: ${space_s}px; }
QPushButton#rowname { background: transparent; border: 0; padding: 0; text-align: left; font-weight: 600;
    font-size: ${type_m}px; min-height: 0; }
QPushButton#rowname:hover { color: $accent; background: transparent; }
QPushButton#rowname:focus { border: 0; padding: 0; text-decoration: underline; }
QFrame#channelrow { background: $surface; border: 0; border-bottom: 1px solid $border; border-left: 3px solid transparent; }
QFrame#channelrow[selected="true"] { background: $surface_raised; border-left: 3px solid $accent; }
QLabel#legend { color: $text_secondary; font-size: ${type_xs}px; }
QLabel#rowmeta { color: $text_secondary; font-size: ${type_xs}px; }
QLabel#hemisphere { color: $text_secondary; font-size: ${type_xs}px; font-weight: 600; letter-spacing: 1px; }

QPushButton { background: $surface; color: $text_primary; border: 1px solid $border;
    border-radius: ${radius_m}px; padding: 6px ${space_m}px; min-height: ${space_m}px; }
QPushButton:hover { border-color: $accent; background: $surface_raised; }
QPushButton:pressed { background: $border; }
QPushButton:focus { border: 2px solid $accent; padding: 5px 15px; }
QPushButton:disabled { color: $text_secondary; background: $surface_raised; border-color: $border; }
QPushButton#primary { background: $accent; color: $accent_text; border: 1px solid $accent; font-weight: 600; }
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:pressed { background: $accent_hover; }
QPushButton#primary:disabled { background: $surface_raised; color: $text_secondary; border-color: $border; }
QPushButton#danger { background: $danger_bg; color: $danger; border: 1px solid $danger; font-weight: 600; }
QPushButton#danger:hover { background: $danger; color: $surface; }
QPushButton#danger:disabled { background: $surface_raised; color: $text_secondary; border-color: $border; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; }
QPushButton#ghost:hover { background: $surface_raised; border-color: $border; }

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $surface; color: $text_primary; border: 1px solid $border; border-radius: ${radius_m}px;
    padding: 5px ${space_s}px; selection-background-color: $accent; selection-color: $accent_text; }
QLineEdit:hover, QTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: $accent; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus { border: 2px solid $accent; padding: 4px 7px; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
    background: $surface_raised; color: $text_secondary; }
QComboBox::drop-down { border: 0; width: ${space_l}px; }
QComboBox QAbstractItemView { background: $surface; color: $text_primary; border: 1px solid $border;
    selection-background-color: $accent; selection-color: $accent_text; outline: 0; }
QCheckBox { spacing: ${space_s}px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $border; border-radius: 4px;
    background: $surface; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; $check_image }
QCheckBox::indicator:hover { border-color: $accent; }

QToolButton { background: transparent; color: $text_primary; border: 1px solid transparent;
    border-radius: ${radius_m}px; padding: 4px ${space_s}px; }
QToolButton:hover { background: $surface_raised; border-color: $border; }
QToolButton:pressed { background: $border; }
QToolButton:focus { border: 2px solid $accent; }
QToolButton::menu-indicator { image: none; }
QMenu { background: $surface; color: $text_primary; border: 1px solid $border; padding: ${space_xs}px; }
QMenu::item { padding: 6px ${space_l}px 6px ${space_m}px; border-radius: ${radius_s}px; }
QMenu::item:selected { background: $accent; color: $accent_text; }
QMenuBar { background: $background; color: $text_primary; }
QMenuBar::item:selected { background: $surface_raised; }

QTabWidget::pane { border: 1px solid $border; border-radius: ${radius_m}px; background: $surface; }
QTabBar::tab { background: transparent; color: $text_secondary; padding: 6px ${space_m}px;
    border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $text_primary; border-bottom: 2px solid $accent; }
QTabBar::tab:hover { color: $text_primary; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: $text_secondary; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 4px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: $text_secondary; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: 0;
    width: 0; height: 0; }

QLabel#chip { padding: 2px ${space_s}px; border-radius: ${radius_l}px; background: $surface_raised; color: $text_secondary; }
QLabel#chip[active="true"] { background: $accent; color: $accent_text; font-weight: 600; }
QLabel#chip[done="true"] { color: $text_primary; }
""")


def build_qss(t: T.Tokens, check_icon: str | None = None) -> str:
    """The application style sheet for one theme. Every value comes from the tokens.
    `check_icon` is the path of a check-mark image for checked boxes (drawn by the Qt layer)."""
    values = {name: getattr(t, name) for name in t.color_names()}
    values["check_image"] = f'image: url("{check_icon}");' if check_icon else ""
    values.update(
        font=T.FONT_FAMILY, space_xs=T.SPACE_XS, space_s=T.SPACE_S, space_m=T.SPACE_M, space_l=T.SPACE_L,
        space_xl=T.SPACE_XL, radius_s=T.RADIUS_S, radius_m=T.RADIUS_M, radius_l=T.RADIUS_L,
        type_xs=T.TYPE_XS, type_s=T.TYPE_S, type_m=T.TYPE_M, type_l=T.TYPE_L, type_xl=T.TYPE_XL)
    return TEMPLATE.substitute(values)
