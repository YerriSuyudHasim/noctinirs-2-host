"""Tahap 2 UI: geometry warning (M6), pulse-protection toggle (M2), separated metric names."""
import math

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets

from test_engineering import window, qapp, wait_for   # noqa: F401  (fixtures)
from noctinirs_host.preview.filters import FilterConfig
from noctinirs_host.preview.pipeline import ChannelPoint


def test_default_geometry_shows_a_clear_warning_and_explicit_geometry_does_not(window):
    w = window(geometry_default=True)
    assert "TANPA --geometry" in w.welcome_geometry.text() or "TANPA --geometry" in w.welcome_geometry.toolTip() \
        or "TANPA --geometry" in getattr(w.welcome_geometry, "full_text", lambda: "")()
    w2 = window()
    assert "TANPA --geometry" not in w2.welcome_geometry.text()


def test_pulse_protection_checkbox_is_on_by_default_and_reaches_the_filter_config(window):
    w = window()
    assert w.pulse_protection.isChecked()
    w.pulse_protection.setChecked(False)
    w.lp.setChecked(True)
    w.lp_cutoff.setValue(0.3)
    w.filter_channels["CH1"].setChecked(True)
    w._apply_filters()
    wait_for(lambda: not w._pending)
    cfg = w.state["preview_config"].filters if w.state.get("preview_config") else w.preview_cfg.filters
    assert cfg.pulse_protection is False and math.isclose(cfg.low_cutoff, 0.3)


def test_detail_rows_name_snr_frame_cv_and_quality_separately(window):
    w = window()
    ch = next(iter(w.panel.channels))
    pt = ChannelPoint(ch, "long", "VALID", i735=1000.0, i850=1000.0, sigma735=10.0, sigma850=20.0,
                      sci=0.9, cardiac_db=15.0, r_hbo_hbr=0.1, tier="good")
    w.panel._pts[ch].append(pt)
    labels = dict(w.panel.detail_rows(ch))
    keys = " | ".join(labels)
    assert "SNR_frame" in keys and "CV_%" in keys and "PROVISIONAL" in keys and "I/σ (measured)" not in keys
    cv = next(v for k, v in labels.items() if k.startswith("CV_%"))
    assert cv.startswith("1") and "/ 2" in cv                                # 100/100 and 100/50
    assert any(v == "baik" for v in labels.values())
