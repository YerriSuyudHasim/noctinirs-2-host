"""Visual theme: colour tokens (light/dark), spacing scale and the application style sheet.

Pure Python (no Qt import) so the tokens can be unit-tested, including WCAG contrast.
Look and feel only: no behaviour lives here. System fonts only (no bundled fonts).
"""

from __future__ import annotations

from dataclasses import dataclass

FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial")
MONO_FAMILIES = ("Cascadia Mono", "Consolas", "Menlo", "Courier New")
RADIUS = 10  # cards
RADIUS_CONTROL = 8  # buttons and inputs
SPACE = 12  # base spacing unit (px)


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    surface: str
    border: str
    text: str
    muted: str
    accent: str
    on_accent: str
    danger: str
    on_danger: str
    ok: str
    warn_bg: str
    on_warn: str
    plot_bg: str
    plot_grid: str
    plot_axis: str
    chip_bg: str


LIGHT = Theme(
    name="light", bg="#F4F5F7", surface="#FFFFFF", border="#E1E4E9", text="#1A1E26",
    muted="#59616F", accent="#1D4ED8", on_accent="#FFFFFF", danger="#C62828",
    on_danger="#FFFFFF", ok="#17703F", warn_bg="#FFF3CF", on_warn="#5F4300",
    plot_bg="#FFFFFF", plot_grid="#E6E9EE", plot_axis="#59616F", chip_bg="#EEF0F3",
)
DARK = Theme(
    name="dark", bg="#101318", surface="#181C23", border="#2A313C", text="#E7EAF0",
    muted="#A2ABB9", accent="#7FA8FF", on_accent="#0A1020", danger="#D23A3F",
    on_danger="#FFFFFF", ok="#5CD08A", warn_bg="#3A2F10", on_warn="#F6D77A",
    plot_bg="#181C23", plot_grid="#2A313C", plot_axis="#A2ABB9", chip_bg="#232934",
)
THEMES = {"light": LIGHT, "dark": DARK}


def _lin(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colours."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def contrast_pairs(t: Theme) -> list[tuple[str, str, str]]:
    """(label, foreground, background) pairs that must reach WCAG AA (4.5:1)."""
    return [
        ("text on bg", t.text, t.bg), ("text on surface", t.text, t.surface),
        ("muted on bg", t.muted, t.bg), ("muted on surface", t.muted, t.surface),
        ("accent text on surface", t.accent, t.surface),
        ("primary button", t.on_accent, t.accent), ("danger button", t.on_danger, t.danger),
        ("ok text on surface", t.ok, t.surface), ("warn text", t.on_warn, t.warn_bg),
        ("muted on pressed/selected", t.muted, t.chip_bg), ("text on pressed/selected", t.text, t.chip_bg),
        ("plot axis on plot", t.plot_axis, t.plot_bg),
    ]


def build_qss(t: Theme, arrow_up: str | None = None, arrow_down: str | None = None) -> str:
    """Application style sheet. `arrow_up`/`arrow_down` are optional image paths (forward
    slashes) for the spin-box and combo-box arrows; without them Qt draws its own."""
    arrows = ""
    if arrow_up and arrow_down:
        arrows = (
            f"QSpinBox::up-arrow {{ image: url({arrow_up}); width: 9px; height: 6px; }}\n"
            f"QSpinBox::down-arrow {{ image: url({arrow_down}); width: 9px; height: 6px; }}\n"
            f"QComboBox::down-arrow {{ image: url({arrow_down}); width: 9px; height: 6px; }}")
    font = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
    mono = ", ".join(f'"{f}"' for f in MONO_FAMILIES)
    return f"""
* {{ font-family: {font}; font-size: 13px; color: {t.text}; }}
QMainWindow, QWidget#root, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.bg}; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}

QFrame#card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS}px; }}
QFrame#header {{ background: {t.surface}; border: none; border-bottom: 1px solid {t.border}; }}
QLabel#title {{ font-size: 15px; font-weight: 600; }}
QLabel#sectionTitle {{ color: {t.muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#muted {{ color: {t.muted}; }}
QLabel#plot_note {{ color: {t.muted}; background: transparent; }}

QLabel#banner {{ color: {t.muted}; background: transparent; }}
QLabel#synthetic_notice {{ background: {t.warn_bg}; color: {t.on_warn}; border-radius: 11px;
                          padding: 3px 12px; font-weight: 600; font-size: 12px; }}
QLabel#state_label {{ font-size: 20px; font-weight: 600; background: transparent; }}
QLabel#state_label[tone="ok"] {{ color: {t.ok}; }}
QLabel#state_label[tone="info"] {{ color: {t.accent}; }}
QLabel#state_label[tone="danger"] {{ color: {t.danger if t.name == "light" else "#FF8A8E"}; }}
QLabel#state_label[tone="muted"] {{ color: {t.muted}; }}

QPushButton {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS_CONTROL}px;
              padding: 8px 14px; font-weight: 500; }}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.chip_bg}; }}
QPushButton:checked {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.bg}; border-color: {t.border}; }}
QPushButton[role="primary"] {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}
QPushButton[role="primary"]:disabled {{ background: {t.chip_bg}; color: {t.muted}; border-color: {t.border}; }}
QPushButton[role="danger"] {{ background: {t.danger}; color: {t.on_danger}; border-color: {t.danger};
                             font-weight: 700; letter-spacing: 1px; }}
QPushButton[role="danger"]:disabled {{ background: {t.chip_bg}; color: {t.muted}; border-color: {t.border}; }}

QSpinBox, QComboBox {{ background: {t.surface}; border: 1px solid {t.border};
                       border-radius: {RADIUS_CONTROL}px; padding: 5px 8px; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 20px; border: none;
                                            background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
{arrows}
QSpinBox:disabled, QComboBox:disabled {{ color: {t.muted}; background: {t.bg}; }}
QSpinBox:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QComboBox QAbstractItemView {{ background: {t.surface}; selection-background-color: {t.chip_bg};
                              selection-color: {t.text}; border: 1px solid {t.border}; }}

QToolButton#sectionToggle {{ background: transparent; border: none; color: {t.muted}; font-size: 11px;
                            font-weight: 600; letter-spacing: 1px; padding: 4px 0; text-align: left; }}
QToolButton#sectionToggle:hover {{ color: {t.text}; }}

QTableWidget {{ background: {t.surface}; border: none; gridline-color: transparent;
               alternate-background-color: {t.bg}; }}
QTableWidget::item {{ padding: 2px 6px; }}
QHeaderView::section {{ background: {t.surface}; color: {t.muted}; border: none;
                       border-bottom: 1px solid {t.border}; padding: 6px; font-weight: 600; }}
QPlainTextEdit#event_log {{ background: {t.surface}; border: 1px solid {t.border};
                           border-radius: {RADIUS_CONTROL}px; padding: 6px; font-size: 12px;
                           font-family: {mono}; color: {t.muted}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

QMenuBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.chip_bg}; border-radius: 6px; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.chip_bg}; }}
QMessageBox {{ background: {t.surface}; }}
"""
