"""Design tokens: the single source of truth for colour, spacing, type and motion.

Pure Python (no Qt import) so it can be unit-tested, including WCAG contrast. Everything visual in
the desktop (the style sheet, the pyqtgraph plots, the status chips) is derived from these values.
HbO and HbR use an Okabe-Ito pair (vermillion / blue): distinct for the common colour-vision
deficiencies, and the same hue meaning in both themes.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# --- spacing: an 8 px grid --------------------------------------------------------------------
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 16, 24, 32
# --- shape -----------------------------------------------------------------------------------------
RADIUS_S, RADIUS_M, RADIUS_L = 6, 8, 10
# --- type: one family, one scale (px) ------------------------------------------------------------
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'
TYPE_XS, TYPE_S, TYPE_M, TYPE_L, TYPE_XL = 12, 14, 16, 20, 24
# --- motion: 150-250 ms, ease-out -------------------------------------------------------------------
MOTION_FAST_MS, MOTION_MEDIUM_MS, MOTION_SLOW_MS = 150, 200, 250
EASING = "OutCubic"   # name of the QEasingCurve; the Qt layer resolves it


@dataclass(frozen=True)
class Tokens:
    name: str
    background: str
    surface: str
    surface_raised: str
    border: str
    text_primary: str
    text_secondary: str
    accent: str
    accent_hover: str
    accent_text: str           # text on the accent colour
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    plot_background: str
    plot_grid: str
    plot_axis: str
    hbo: str
    hbr: str
    od_a: str                  # the two traces of the OD / intensity modes (nominal 735 / 850)
    od_b: str
    marker_warning: str
    marker_uncorrected: str
    marker_clamp: str

    def color_names(self) -> tuple:
        return tuple(f.name for f in fields(self) if f.name != "name")


LIGHT = Tokens(
    name="light", background="#F4F8FC", surface="#FFFFFF", surface_raised="#EAF2FB", border="#CFE0EF",
    text_primary="#102F4B", text_secondary="#46607A", accent="#1565C0", accent_hover="#0F4FA0",
    accent_text="#FFFFFF", success="#17703F", success_bg="#E3F4EA", warning="#8A5A00",
    warning_bg="#FFF3CF", danger="#B3261E", danger_bg="#FBE9E7", plot_background="#FFFFFF",
    plot_grid="#DCE6F0", plot_axis="#46607A", hbo="#D55E00", hbr="#0072B2", od_a="#B83A0B",
    od_b="#1D4ED8", marker_warning="#E69F00", marker_uncorrected="#7B4FB3", marker_clamp="#C62828",
)
DARK = Tokens(
    name="dark", background="#0E1624", surface="#162235", surface_raised="#1D2C44", border="#2B3C58",
    text_primary="#E6EDF7", text_secondary="#9FB2C9", accent="#4C9AFF", accent_hover="#77B3FF",
    accent_text="#06121F", success="#4CC38A", success_bg="#12332A", warning="#F2B84B",
    warning_bg="#3A2F10", danger="#FF7B72", danger_bg="#3D1A1A", plot_background="#121D2E",
    plot_grid="#263750", plot_axis="#9FB2C9", hbo="#FFB070", hbr="#4A9EFF", od_a="#FF8F6B",
    od_b="#7FA8FF", marker_warning="#F2B84B", marker_uncorrected="#B79CFF", marker_clamp="#FF7B72",
)
THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "light"


def _linear(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two #RRGGBB colours."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
