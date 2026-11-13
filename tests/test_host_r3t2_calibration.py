"""Tahap 2 / 2.1 calibration estimators: robust sigma, fallback flags, drift bound, target-level selection, ignored PD."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from noctinirs_host.calibration import Assumptions, Level, empirical_choice, visit_shortfall
from noctinirs_host.calibration_sweep import block_noise, ignored_pd_clipped, robust_noise, pooled_noise

HEAD = Assumptions().headroom


class Row:
    def __init__(self, seq, step, within=1.0):
        self.first_seq, self._step, self._within = seq, step, within

    def on_mean(self, ain):
        return self._step

    def off_mean(self, ain):
        return 0.0

    def within_sigma(self, ain):
        return self._within


def rows(values, start=0, stride=800, within=1.0):
    return [Row(start + i * stride, v, within) for i, v in enumerate(values)]


def visit(values, **kw):
    return SimpleNamespace(rows=[rows(values, **kw)], fs=10)


def noisy(n, amp, seed=1):
    import random
    rng = random.Random(seed)
    return [1000 + rng.gauss(0, amp) for _ in range(n)]


def test_block_noise_is_spectral_and_never_bridges_gaps():
    sigma, source = block_noise(rows(noisy(40, 5)), 0, 10)
    assert source == "spectral" and 2 < sigma < 10
    # A gap splits into blocks shorter than 8 rows: fallback, reported as such.
    broken = rows(noisy(5, 5)) + rows(noisy(5, 5), start=20 * 800)
    sigma, source = block_noise(broken, 0, 10)
    assert source == "within" and sigma == pytest.approx(1.0)
    assert block_noise([], 0, 10) == (0.0, "none")


def test_robust_noise_ignores_one_outlier_visit():
    clean = [visit(noisy(40, 5, seed=s)) for s in (1, 2, 3, 4)]
    outlier = visit(noisy(40, 500, seed=9))
    sigma, unc, n, warnings = robust_noise(clean + [outlier], 0, 0)
    assert n == 5 and sigma < 12 and not warnings        # RMS pooling would be ~220
    assert unc > 0                                       # >=3 visits: spread gives an uncertainty
    assert pooled_noise(clean + [outlier], 0, 0) == sigma
    assert robust_noise(clean[:2], 0, 0)[1] == 0.0       # <3 visits: uncertainty unknown (0)


def test_noise_fallback_is_flagged_or_excluded_never_silent():
    good, short = visit(noisy(40, 5)), visit(noisy(5, 5))
    sigma, _, n, warnings = robust_noise([good, short], 0, 0)
    assert n == 1 and warnings == ("NOISE_FALLBACK_VISIT_EXCLUDED",)
    sigma, _, n, warnings = robust_noise([short], 0, 0)
    assert warnings == ("NOISE_FALLBACK_WITHIN_SIGMA",) and sigma == pytest.approx(1.0)


def level(code, mean, sigma, visits, unc=0.0, **kw):
    return Level(code, (mean,), (sigma,), (mean,), (0,), visits, 31, envelope=(mean,),
                 uncertainty=(unc,), **kw)


def choose(levels):
    return empirical_choice(levels, (0,), 133, 10, Assumptions())


FSV = 8388607


def pct(fraction):
    """A step of this fraction of full scale."""
    return fraction * FSV


def test_selection_takes_the_level_closest_to_the_target_not_the_lowest_code():
    # Tahap 2.1: target 10 % of FS (long). Steps 2 %, 10 %, 40 % of FS, all with a high SNR and 5 visits.
    levels = [level(8, pct(.02), pct(.02) / 300, 5), level(32, pct(.10), pct(.10) / 300, 5),
              level(67, pct(.40), pct(.40) / 300, 5)]
    assert choose(levels).code == 32
    assert "TIE_LOWEST_CODE" not in choose(levels).flags
    # Nothing reaches the target: the brightest safe level is the closest one and is flagged.
    dim = [level(8, pct(.001), pct(.001) / 300, 5), level(32, pct(.004), pct(.004) / 300, 5)]
    choice = choose(dim)
    assert choice.code == 32 and "BELOW_TARGET_LEVEL" in choice.flags


def test_target_level_is_configurable_and_differs_for_short_slots():
    levels = [level(16, pct(.05), pct(.05) / 300, 5), level(100, pct(.20), pct(.20) / 300, 5)]
    assert choose(levels).code == 16                                           # long target 10 %: |ln .5| < |ln 2|
    assert empirical_choice(levels, (0,), 133, 10, replace(Assumptions(), target_level_long=.2)).code == 100
    assert empirical_choice(levels, (0,), 133, 10, Assumptions(), short=True).code == 100   # short target 20 %
    with pytest.raises(ValueError):
        Assumptions(target_level_long=0.0)


def test_a_level_below_the_snr_floor_is_never_chosen_while_another_passes():
    # The 10 %-of-FS level has SNR 2 (below the floor of 5); the 30 % level passes and wins although farther away.
    levels = [level(32, pct(.10), pct(.10) / 2, 5), level(67, pct(.30), pct(.30) / 50, 5)]
    choice = choose(levels)
    assert choice.code == 67 and "SNR_BELOW_FLOOR" not in choice.flags


def test_no_passing_level_falls_back_to_tier3_never_to_the_dimmest_level():
    levels = [level(8, pct(.0005), pct(.0005) / 1.0, 5), level(67, pct(.002), pct(.002) / 2.0, 5),
              level(133, pct(.004), pct(.004) / 3.0, 5)]
    choice = choose(levels)
    assert (choice.code, choice.tier) == (133, 3) and "DEFAULT_TIER" in choice.flags


def test_minimum_visits_decide_before_distance_and_a_thin_fallback_is_flagged():
    near_thin = level(32, pct(.10), pct(.10) / 300, 2)
    far_full = level(67, pct(.30), pct(.30) / 300, 3)
    assert choose([near_thin, far_full]).code == 67
    choice = choose([near_thin, level(8, pct(.02), pct(.02) / 300, 2)])
    assert choice.code == 32 and {"FEW_VISITS", "VISIT_FALLBACK"} <= set(choice.flags)
    assert "VISIT_FALLBACK" not in choose([near_thin, far_full]).flags
    with pytest.raises(ValueError):
        Assumptions(min_choice_visits=1)                                         # below min_visits_per_code


def test_levels_equally_close_to_the_target_prefer_the_higher_snr():
    # 10.0 % and 10.3 % of FS are within the 5 % equivalence band: the higher SNR wins and the tie is flagged.
    levels = [level(32, pct(.100), pct(.100) / 40, 5), level(33, pct(.103), pct(.103) / 90, 5)]
    choice = choose(levels)
    assert choice.code == 33 and "TIE_HIGHER_SNR" in choice.flags
    far = [level(32, pct(.100), pct(.100) / 40, 5), level(33, pct(.130), pct(.130) / 90, 5)]
    assert choose(far).code == 32                                                # outside the band: distance decides


def test_clear_winner_is_kept_and_low_snr_levels_do_not_tie_in():
    levels = [level(4, 1e4, 1e4 / 8, 3, unc=0.05), level(16, 4e4, 4e4 / 300, 3, unc=0.05),
              level(32, 8e4, 8e4 / 900, 3, unc=0.05)]
    assert choose(levels).code == 32


def test_levels_with_too_few_visits_are_not_scored():
    levels = [level(16, 4e4, 4e4 / 50, 3, unc=0.05), level(32, 8e4, 8e4 / 5000, 1)]
    choice = choose(levels)
    assert choice.code == 16 and visit_shortfall(levels[1], Assumptions()) == ("INSUFFICIENT_VISITS",)


def test_few_visits_warning_and_level_warnings_reach_the_choice():
    levels = [level(16, 4e4, 4e4 / 300, 2, warnings=("NOISE_FALLBACK_WITHIN_SIGMA",))]
    flags = choose(levels).flags
    assert "FEW_VISITS" in flags and "NOISE_FALLBACK_WITHIN_SIGMA" in flags


def test_ignored_pd_clipping_is_reported_but_never_changes_the_choice():
    base = [level(16, 4e4, 4e4 / 300, 3, unc=0.05)]
    clipped = [Level(16, (4e4, 5e6), (4e4 / 300, 1.0), (4e4, HEAD + 1), (0, 0), 3, 31, envelope=(4e4, 5e6),
                     uncertainty=(0.05, 0.0))]
    assert ignored_pd_clipped(clipped, (1,), Assumptions()) == (1,)
    assert ignored_pd_clipped(clipped, (), Assumptions()) == ()
    assert ignored_pd_clipped(base * 1, (0,), Assumptions()) == ()
    # Sweep.finish scores with the ignored detector's peak/minimum/envelope zeroed (evidence stays in the artifact).
    evaluated = [replace(l, peak=(l.peak[0], 0), minimum=(l.minimum[0], 0), envelope=(l.envelope[0], 0))
                 for l in clipped]
    assert empirical_choice(evaluated, (0,), 133, 10, Assumptions()).code == choose(base).code
