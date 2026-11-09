"""Design tokens and the generated style sheet (no Qt needed)."""

import re
from pathlib import Path

import pytest

from noctinirs_host_ui.design import tokens as T
from noctinirs_host_ui.design.qss import build_qss

UI = Path(__file__).resolve().parents[1] / "src/noctinirs_host_ui"
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@pytest.mark.parametrize("theme", [T.LIGHT, T.DARK])
def test_every_colour_token_is_a_hex_value_and_both_themes_have_the_same_names(theme):
    assert theme.color_names() == T.LIGHT.color_names() == T.DARK.color_names()
    assert all(HEX.match(getattr(theme, n)) for n in theme.color_names())
    for needed in ("background", "surface", "surface_raised", "border", "text_primary", "text_secondary",
                   "accent", "accent_hover", "success", "warning", "danger", "plot_background", "plot_grid",
                   "hbo", "hbr"):
        assert needed in theme.color_names()


@pytest.mark.parametrize("theme", [T.LIGHT, T.DARK])
def test_text_and_status_colours_meet_wcag_contrast(theme):
    c = T.contrast
    assert c(theme.text_primary, theme.background) >= 7
    assert c(theme.text_primary, theme.surface) >= 7
    assert c(theme.text_secondary, theme.surface) >= 4.5
    assert c(theme.text_secondary, theme.background) >= 4.5
    assert c(theme.accent_text, theme.accent) >= 4.5
    assert c(theme.accent_text, theme.accent_hover) >= 4.5
    for fg, bg in ((theme.success, theme.success_bg), (theme.warning, theme.warning_bg),
                   (theme.danger, theme.danger_bg)):
        assert c(fg, bg) >= 4.5
    assert c(theme.text_primary, theme.plot_background) >= 7
    assert c(theme.plot_axis, theme.plot_background) >= 4.5


@pytest.mark.parametrize("theme", [T.LIGHT, T.DARK])
def test_hbo_and_hbr_are_distinct_and_readable_in_both_themes(theme):
    assert T.contrast(theme.hbo, theme.hbr) >= 1.3
    assert T.contrast(theme.hbo, theme.plot_background) >= 3
    assert T.contrast(theme.hbr, theme.plot_background) >= 3
    r = lambda h: int(h[1:3], 16)  # noqa: E731  warm hue has more red, cool hue more blue
    b = lambda h: int(h[5:7], 16)  # noqa: E731
    assert r(theme.hbo) > b(theme.hbo) and b(theme.hbr) > r(theme.hbr)   # same hue meaning in both themes


def test_spacing_shape_type_and_motion_follow_the_scale():
    assert (T.SPACE_XS, T.SPACE_S, T.SPACE_M, T.SPACE_L, T.SPACE_XL) == (4, 8, 16, 24, 32)
    assert all(6 <= r <= 10 for r in (T.RADIUS_S, T.RADIUS_M, T.RADIUS_L))
    assert (T.TYPE_XS, T.TYPE_S, T.TYPE_M, T.TYPE_L, T.TYPE_XL) == (12, 14, 16, 20, 24)
    assert all(150 <= ms <= 250 for ms in (T.MOTION_FAST_MS, T.MOTION_MEDIUM_MS, T.MOTION_SLOW_MS))
    assert "Segoe UI" in T.FONT_FAMILY


@pytest.mark.parametrize("theme, other", [(T.LIGHT, T.DARK), (T.DARK, T.LIGHT)])
def test_qss_is_generated_from_the_tokens_of_that_theme_only(theme, other):
    qss = build_qss(theme)
    assert "$" not in qss and "{" in qss                   # every placeholder was filled
    for name in ("background", "surface", "border", "text_primary", "accent", "danger", "warning_bg"):
        assert getattr(theme, name) in qss, name
    assert other.accent not in qss and other.background not in qss
    assert str(T.RADIUS_M) in qss and str(T.TYPE_S) in qss and str(T.SPACE_M) in qss
    assert build_qss(theme) == qss                          # deterministic


def test_qss_covers_the_component_states():
    qss = build_qss(T.LIGHT)
    for needle in ("QPushButton#primary", "QPushButton#danger", "QPushButton:hover", "QPushButton:pressed",
                   "QPushButton:disabled", "QPushButton:focus", "QLineEdit:focus", "QComboBox",
                   "QCheckBox::indicator:checked", "QToolTip", "QScrollBar:vertical", "QTabBar::tab:selected",
                   "QFrame#channelrow[selected=\"true\"]", "QLabel#previewbanner", "QLabel#chip[active=\"true\"]"):
        assert needle in qss, needle
    assert 'image: url("/x/check.png");' in build_qss(T.LIGHT, "/x/check.png")


def test_ui_modules_hold_no_hard_coded_colours():
    """Colours come from the tokens. The legacy demo modules (app, control_panel, live_view, theme,
    synthetic) predate the design system and are not part of this check."""
    legacy = {"app.py", "control_panel.py", "live_view.py", "theme.py", "synthetic.py", "credits.py"}
    offenders = []
    for path in UI.rglob("*.py"):
        if path.name in legacy or path.name == "tokens.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#[0-9A-Fa-f]{6}\b", line):
                offenders.append(f"{path.name}:{n}: {line.strip()[:60]}")
    assert not offenders, offenders
