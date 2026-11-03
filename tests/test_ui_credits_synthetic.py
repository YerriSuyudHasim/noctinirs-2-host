"""Pure-Python parts of the UI package (no Qt needed)."""

from __future__ import annotations

from noctinirs_host_ui.credits import BANNER_TEXT, CREDITS, about_text
from noctinirs_host_ui.synthetic import SyntheticSource


def test_banner_states_prototype_and_non_clinical() -> None:
    assert "research prototype" in BANNER_TEXT.lower()
    assert "non-clinical" in BANNER_TEXT


def test_credits_cover_bundled_components_with_licenses() -> None:
    names = {c.name.split()[0] for c in CREDITS}
    assert {"Qt", "pyqtgraph", "NumPy"} <= names
    assert all(c.license_name for c in CREDITS)
    assert not any("GPL-" in c.license_name and "LGPL" not in c.license_name for c in CREDITS)


def test_about_text_has_versions_notice_and_credits() -> None:
    text = about_text("9.9.9", "6.x", "6.y")
    for needle in ("9.9.9", "Not a medical device", "PySide6", "pyqtgraph", "LGPL-3.0", "MIT",
                   "THIRD_PARTY_NOTICES.txt", "may replace"):
        assert needle in text


def test_synthetic_source_is_deterministic_and_bounded() -> None:
    a, b = SyntheticSource(seed=7), SyntheticSource(seed=7)
    seq_a = [a.next_sample() for _ in range(50)]
    assert seq_a == [b.next_sample() for _ in range(50)]
    assert seq_a[1][0] > seq_a[0][0]
    assert all(abs(v) < 1.1 for _, v in seq_a)
