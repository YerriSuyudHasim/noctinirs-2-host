"""Tahap 2.1: cardiac visibility classes, motion warm-up, burst-loss tolerance, gap bridging, no leak rule."""
import math
import random

import pytest

from noctinirs_host.preview.mbll import mbll_delta
from noctinirs_host.preview.params import DpfSetting, LEGACY_PREVIEW_EXTINCTION, resolve_dpf
from noctinirs_host.preview import quality as Q
from noctinirs_host.preview.signal_quality import (CARDIAC_CLEAR, CARDIAC_FAINT, CARDIAC_WEAK, ChannelMonitor,
                                                   MotionDetector, QualityConfig, cardiac_class, tier_for,
                                                   ChannelQuality, TIER_GOOD, TIER_NOISY)

FS = 10.0
DPF = resolve_dpf(DpfSetting())


def hb(a, b, sd=20.0):
    r = mbll_delta(a, b, sd, DPF, LEGACY_PREVIEW_EXTINCTION)
    return r.d_hbo_uM, r.d_hbr_uM


def pulse(t):
    return math.sin(2 * math.pi * 1.6 * t)


def test_cardiac_classes_follow_the_owner_thresholds_and_are_configurable():
    cfg = QualityConfig()
    assert (cfg.cardiac_db_min, cfg.cardiac_db_weak) == (6.0, 3.0)
    assert cardiac_class(None, cfg) is None and cardiac_class(float("nan"), cfg) is None
    assert cardiac_class(6.5, cfg) == CARDIAC_CLEAR
    assert cardiac_class(6.0, cfg) == CARDIAC_WEAK and cardiac_class(3.0, cfg) == CARDIAC_WEAK
    assert cardiac_class(2.9, cfg) == CARDIAC_FAINT
    custom = QualityConfig(cardiac_db_min=10.0, cardiac_db_weak=5.0)
    assert cardiac_class(8.0, custom) == CARDIAC_WEAK
    assert cfg.to_metadata()["status"].startswith("provisional")


def test_a_clear_pulse_counts_as_contact_and_a_6_to_7_db_pulse_no_longer_fails_the_old_10_db_gate():
    cfg = QualityConfig()
    q = ChannelQuality(sci=0.2, cardiac_db=6.5, contact_ok=6.5 > cfg.cardiac_db_min)
    assert q.contact_ok and tier_for(300.0, q, cfg) == TIER_GOOD
    weak = ChannelQuality(sci=0.2, cardiac_db=4.0, contact_ok=False)
    assert tier_for(300.0, weak, cfg) == TIER_NOISY


def test_leak_constants_are_gone():
    assert not any("LEAK" in name for name in Q.CONSTANTS) and not hasattr(Q, "LEAK_WARN_SIGMA")


def quiet_then_shift(shift_at, shift=0.15, seconds=60, seed=3, snr=300.0):
    rng = random.Random(seed)
    cfg = QualityConfig()
    mon = ChannelMonitor(FS, cfg, hb)
    out = []
    for i in range(int(seconds * FS)):
        t = i / FS
        jump = shift if shift_at <= t < shift_at + 3 else 0.0
        v = 0.01 * pulse(t) + rng.gauss(0, .002) + jump
        out.append((t, mon.push(t, v, v, snr, snr).motion_z))
    return out


def test_motion_warm_up_is_shorter_so_a_disturbance_25_s_in_is_seen_and_the_quiet_before_it_is_not():
    zs = quiet_then_shift(25.0)
    quiet = [z for t, z in zs if 8 <= t < 24 and z is not None]
    event = [z for t, z in zs if 25 <= t < 29 and z is not None]
    assert quiet, "there must be a verdict well before the old 30 s warm-up"
    assert max(quiet) < 6 and event and max(event) > 6
    assert all(z is None for t, z in zs if t < 5)      # nothing before the minimum trailing level exists


def test_the_scale_never_drops_below_the_frame_noise_floor():
    # A very bright, very quiet channel (SNR 800, OD noise ~0.0012) must not turn tiny wiggles into events.
    rng = random.Random(1)
    cfg = QualityConfig()
    mon = ChannelMonitor(FS, cfg, hb)
    zs = []
    for i in range(900):
        t = i / FS
        v = rng.gauss(0, 0.0004)
        zs.append(mon.push(t, v, v, 800.0, 800.0).motion_z)
    late = [z for z in zs[400:] if z is not None]
    assert late and max(late) < cfg.motion_z


def make_detector():
    return MotionDetector({"L": ["a", "b", "c"], "R": ["d", "e", "f"]}, QualityConfig())


FLAG = {"a": 9.0, "b": 9.0, "c": 1.0}
CALM = {"a": 1.0, "b": 1.0, "c": 1.0}


def test_frames_lost_to_a_burst_neither_confirm_nor_cancel_a_candidate_marker():
    det = make_detector()
    t = 0.0
    for _ in range(3):
        t += .1
        det.update(t, dict(FLAG))
    for _ in range(8):                    # 0.8 s without a verdict: the run is kept
        t += .1
        det.update(t, {})
    assert det.run["L"] == 3 and not det.active["L"]
    for _ in range(2):
        t += .1
        det.update(t, dict(FLAG))
    assert det.active["L"] and det.segments[0][0] == "L"
    # A clear verdict of "calm" still cancels immediately.
    t += .1
    det.update(t, dict(CALM))
    assert det.active["L"] is False


def test_a_long_blind_interval_ends_the_candidate():
    det = make_detector()
    t = 0.0
    for _ in range(3):
        t += .1
        det.update(t, dict(FLAG))
    t += 2.0                              # longer than motion_hold_s
    det.update(t, {})
    assert det.run["L"] == 0


def test_a_marker_after_a_gap_starts_at_the_last_evaluated_frame_so_the_lost_interval_is_marked():
    det = make_detector()
    t = 0.0
    for _ in range(10):
        t += .1
        det.update(t, dict(CALM))
    last_quiet = t
    t += 1.4                              # burst loss, shorter than the hold, longer than the bridge limit
    det.update(t, {})
    for _ in range(6):
        t += .1
        det.update(t, dict(FLAG))
    side, start, end = det.segments[0]
    assert side == "L" and start == pytest.approx(last_quiet) and end > start + 1.4
    assert "no correction" in det.summary()["note"]


def test_a_short_gap_does_not_stretch_the_marker():
    det = make_detector()
    t = 0.0
    for _ in range(10):
        t += .1
        det.update(t, dict(CALM))
    t += .5                               # below motion_gap_bridge_s
    for _ in range(6):
        t += .1
        det.update(t, dict(FLAG))
    side, start, end = det.segments[0]
    assert start == pytest.approx(t - 0.5, abs=0.1)
