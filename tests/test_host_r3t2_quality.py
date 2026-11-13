"""Tahap 2: quality indicators, hemisphere motion markers, SSR pairing/gate/time window, pulse protection."""
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest

from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.filters import FilterConfig
from noctinirs_host.preview.mbll import mbll_delta
from noctinirs_host.preview.params import (LEGACY_PREVIEW_EXTINCTION, PreviewConfig, SSR_MODES, DpfSetting,
                                           resolve_dpf)
from noctinirs_host.preview.pipeline import PreviewPipeline
from noctinirs_host.preview.shortsep import RollingRegressor
from noctinirs_host.preview.signal_quality import (ACHROMATIC, ChannelMonitor, ChannelQuality, MotionDetector,
                                                   QualityConfig, TIER_BAD, TIER_GOOD, TIER_NA, TIER_NOISY,
                                                   tier_for)

BOARD1 = Path(__file__).resolve().parents[1] / "config/optical_geometry_board1.json"
FS = 10.0
DPF = resolve_dpf(DpfSetting())


def hb(a, b, sd=20.0):
    r = mbll_delta(a, b, sd, DPF, LEGACY_PREVIEW_EXTINCTION)
    return r.d_hbo_uM, r.d_hbr_uM


def feed(monitor, n, od, snr=300.0, start=0.0):
    q = None
    for i in range(n):
        a, b = od(i / FS)
        q = monitor.push(start + i / FS, a, b, snr, snr)
    return q


def pulse(t):
    return math.sin(2 * math.pi * 1.6 * t)


def test_identical_pulse_in_both_wavelengths_is_flagged_achromatic():
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    q = feed(mon, 450, lambda t: (0.02 * pulse(t), 0.02 * pulse(t)))
    assert ACHROMATIC in q.flags and q.r_hbo_hbr > 0.8
    assert q.sci == pytest.approx(1.0, abs=1e-3) and q.contact_ok       # SCI alone cannot see this
    assert tier_for(500.0, q, QualityConfig()) == TIER_BAD


def test_physiological_pulse_is_not_flagged_and_can_be_good():
    # dHbR = -0.4 dHbO, forward model with the same extinction table as the monitor
    e = LEGACY_PREVIEW_EXTINCTION.eps
    def od(t):
        x = pulse(t)
        sd = 20.0 / 10 * 6.0
        return tuple(1e-4 * sd * (e[w]["HbO"] * x + e[w]["HbR"] * -0.4 * x) for w in ("735", "850"))
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    q = feed(mon, 450, od)
    assert ACHROMATIC not in q.flags and q.r_hbo_hbr < 0
    assert tier_for(300.0, q, QualityConfig()) == TIER_GOOD
    assert tier_for(50.0, q, QualityConfig()) == TIER_NOISY
    assert tier_for(5.0, q, QualityConfig()) == TIER_BAD


def test_noise_only_channel_has_no_contact_and_is_not_good():
    rng = random.Random(3)
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    q = feed(mon, 450, lambda t: (rng.gauss(0, .01), rng.gauss(0, .01)), snr=300.0)
    assert q.contact_ok is False
    assert tier_for(300.0, q, QualityConfig()) == TIER_NOISY


def test_cardiac_metrics_are_not_assessable_at_low_fs_and_before_warmup():
    low = ChannelMonitor(5.0, QualityConfig(), hb)
    assert low.push(0.0, 0.0, 0.0, 300, 300).flags == ("CARDIAC_NOT_ASSESSABLE",)
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    q = feed(mon, 20, lambda t: (pulse(t) * .01, pulse(t) * .01))
    assert q.sci is None and q.contact_ok is None            # not enough data: no verdict
    assert tier_for(None, q, QualityConfig()) == TIER_NA


def test_gap_is_not_interpolated_window_just_has_fewer_samples():
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    feed(mon, 300, lambda t: (pulse(t) * .01, pulse(t) * .01))
    n_before = len(mon._sci.q)
    mon.push(500.0, 0.0, 0.0, 300, 300)                      # 200 s hole
    assert len(mon._sci.q) <= n_before + 1


def test_motion_z_reacts_to_a_level_shift_and_stays_low_in_quiet_signal():
    rng = random.Random(5)
    mon = ChannelMonitor(FS, QualityConfig(), hb)
    zs = []
    for i in range(1200):
        t = i / FS
        jump = 0.15 if 90 <= t < 93 else 0.0
        z = mon.push(t, 0.01 * pulse(t) + rng.gauss(0, .002) + jump, 0.01 * pulse(t) + rng.gauss(0, .002) + jump,
                     300, 300).motion_z
        zs.append((t, z))
    quiet = [z for t, z in zs if 40 <= t < 85 and z is not None]
    event = [z for t, z in zs if 90 <= t < 95 and z is not None]
    assert quiet and max(quiet) < 6 and max(event) > 6


def test_motion_detector_needs_enough_eligible_channels_and_persistence():
    cfg = QualityConfig()
    det = MotionDetector({"L": ["a", "b", "c"], "R": ["d", "e", "f"]}, cfg)
    t = 0.0
    def step(z):
        nonlocal t
        t += .1
        return det.update(t, z)
    for _ in range(5):                                 # only one eligible channel: never a marker
        assert step({"a": 50.0}) == {"L": False, "R": False}
    for _ in range(cfg.motion_persist - 1):            # two flagged channels but one frame short: not yet
        assert step({"a": 9.0, "b": 9.0, "c": 1.0})["L"] is False
    assert step({"a": 9.0, "b": 9.0, "c": 1.0}) == {"L": True, "R": False}     # last needed frame: marker, left only
    assert step({"a": 1.0, "b": 1.0, "c": 1.0})["L"] is False
    summary = det.summary()
    assert summary["segments"][0][0] == "L" and summary["frames"]["R"] == 0
    assert "no correction" in summary["note"]


def test_ssr_modes_no_longer_offer_the_8mm_pd():
    assert SSR_MODES == ("off", "5mm")
    with pytest.raises(ValueError):
        PreviewConfig(ssr_mode="8mm").validate()
    with pytest.raises(ValueError):
        PreviewConfig(ssr_mode="both").validate()


def board1_pipeline(**cfg):
    return PreviewPipeline(load_geometry(BOARD1), PreviewConfig(ssr_mode="5mm", **cfg), 10)


def test_short_is_paired_by_pd_hemisphere_and_only_5mm():
    pl = board1_pipeline()
    by = {c.name: c for c in pl.geometry.channels}
    assert pl._short_for(by["CH1"]) == ["SC1L"] and pl._short_for(by["CH6"]) == ["SC1L"]
    assert pl._short_for(by["CH9"]) == ["SC1R"] and pl._short_for(by["CH16"]) == ["SC1R"]
    # A channel whose PD is on the right but whose source is on the left follows the PD, not the source.
    crossed = replace(by["CH1"], pd="PD3")
    assert pl._short_for(crossed) == ["SC1R"]
    assert board1_pipeline.__name__ and PreviewPipeline(load_geometry(BOARD1), PreviewConfig(), 10)._short_for(by["CH1"]) == []


def stage_for(pl, **states):
    base = {c.name: ("VALID", "", 1e5, 1e5, 0.0, 0.0, (), 100.0, 100.0) for c in pl.geometry.channels}
    base.update(states)
    return base


def test_short_gate_blocks_weak_achromatic_moving_or_invalid_shorts():
    pl = board1_pipeline()
    ch = next(c for c in pl.geometry.channels if c.name == "CH1")
    ok_q = ChannelQuality()
    pl._quality = {"SC1L": (ok_q, 500.0, 500.0, 500.0)}
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl)) == []
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl, SC1L=("INVALID", "735: SATURATED", 0, 0, 0, 0, (), 1, 1))) == [("SC1L", "SATURATED")]
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl, SC1L=("VALID", "", 1, 1, 0, 0, ("LOW_SIGNAL",), 1, 1))) == [("SC1L", "LOW_SIGNAL")]
    # Tahap 2.1: the cross-side leak criterion is gone. A leftover LEAK_WARN tag can no longer block a short.
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl, SC1L=("VALID", "", 1, 1, 0, 0, ("LEAK_WARN",), 1, 1))) == []
    pl._quality = {"SC1L": (ok_q, 40.0, 40.0, 40.0)}
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl)) == [("SC1L", "SNR_FRAME_LOW")]
    pl._quality = {"SC1L": (ChannelQuality(flags=(ACHROMATIC,)), 500.0, 500.0, 500.0)}
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl)) == [("SC1L", ACHROMATIC)]
    pl._quality = {"SC1L": (ok_q, 500.0, 500.0, 500.0)}
    pl._motion.active["L"] = True
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl)) == [("SC1L", "MOTION")]
    pl._motion.active["L"] = False
    pl.configure(replace(pl.config, ssr_gate_motion=False, ssr_gate_achromatic=False, ssr_min_snr=10.0))
    pl._motion.active["L"] = True
    pl._quality = {"SC1L": (ChannelQuality(flags=(ACHROMATIC,)), 40.0, 40.0, 40.0)}
    assert pl._short_gate(ch, ["SC1L"], stage_for(pl)) == []     # every gate is configurable


def test_rolling_regressor_window_is_time_based_so_a_gap_shortens_history():
    rng = random.Random(1)
    reg = RollingRegressor(1, window=300, min_samples=10, method="ols", ridge_lambda=0, window_s=30.0)
    t = 0.0
    for _ in range(100):
        x = rng.gauss(0, 1)
        reg.push(2 * x + .01 * rng.gauss(0, 1), (x,), t)
        t += .1
    assert reg.n == 100
    t += 200.0                                         # 200 s hole, no interpolation
    x = rng.gauss(0, 1)
    reg.push(2 * x, (x,), t)
    assert reg.n == 1                                  # old rows are older than 30 s: evicted
    # without timestamps the legacy row-count behaviour is unchanged
    legacy = RollingRegressor(1, window=5, min_samples=3, method="ols", ridge_lambda=0)
    for i in range(10):
        legacy.push(float(i), (float(i),))
    assert legacy.n == 5


def test_pulse_protection_toggle_keeps_or_releases_the_cutoffs():
    cfg = FilterConfig(lowpass=True, low_cutoff=.3, bandpass=True, band_low=.02, band_high=.4)
    on = cfg.cardiac_safe(10)
    assert on.low_cutoff >= 3.0 and on.band_high >= 3.0              # default: protected
    off = replace(cfg, pulse_protection=False)
    assert off.cardiac_safe(10) == off and off.low_cutoff == .3      # released: LP/BP used as set
    assert FilterConfig().pulse_protection is True
    assert "pulse_protection" in cfg.metadata()
    pl = PreviewPipeline(load_geometry(BOARD1), PreviewConfig(filters=replace(cfg, pulse_protection=False)), 10)
    assert pl.config.filters.low_cutoff == .3


def test_provenance_documents_quality_names_and_ssr_pairing():
    pl = board1_pipeline()
    prov = pl.provenance()
    assert prov["signal_quality"]["names"]["SNR_frame"] and prov["signal_quality"]["names"]["SNR_cal"]
    assert prov["signal_quality"]["config"]["status"].startswith("provisional")
    assert "ignored_pd_clipped" in prov and "short_separation" in prov
    meta = pl.config.to_metadata()["short_separation"]
    assert "5 mm PD only" in meta["pairing"] and meta["gate"]["min_snr_frame"] == 100.0
