"""Theme tokens: contrast (WCAG AA) and style sheet sanity. Pure Python, no Qt needed."""

import pytest

from noctinirs_host_ui.theme import (
    DARK, LIGHT, THEMES, build_qss, contrast_pairs, contrast_ratio,
)


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_all_text_pairs_reach_wcag_aa(theme):
    for label, fg, bg in contrast_pairs(theme):
        assert contrast_ratio(fg, bg) >= 4.5, f"{theme.name}: {label} {fg} on {bg}"


def test_contrast_helper_known_values():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


@pytest.mark.parametrize("name", ["light", "dark"])
def test_qss_builds_and_uses_the_tokens(name):
    t = THEMES[name]
    qss = build_qss(t)
    for token in (t.bg, t.surface, t.accent, t.danger):
        assert token in qss
    assert 'QPushButton[role="danger"]' in qss and "QFrame#card" in qss
    assert "image: url(" not in qss  # no arrow images unless provided
    with_arrows = build_qss(t, "C:/x/up.png", "C:/x/down.png")
    assert "url(C:/x/up.png)" in with_arrows and "url(C:/x/down.png)" in with_arrows


def test_dark_and_light_differ_and_have_no_duplicate_names():
    assert LIGHT.bg != DARK.bg and LIGHT.name != DARK.name
