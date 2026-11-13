"""Tahap 2 calibration estimators: robust sigma, fallback flags, drift bound, tie rule, ignored PD."""
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


def test_tie_prefers_lowest_code_when_sigma_is_uncertain():
    # Slot 10/11 pattern: a 2-visit level wins by point estimate, a 7-visit level is not different.
    levels = [level(16, 4e4, 4e4 / 280, 7, unc=0.2), level(32, 8e4, 8e4 / 400, 2),
              level(8, 2e4, 2e4 / 130, 2), level(4, 1e4, 1e4 / 130, 2)]
    choice = choose(levels)
    assert choice.code == 16 and "TIE_LOWEST_CODE" in choice.flags
    # The cheap 130-SNR levels are not tied with 400 (difference larger than the combined uncertainty).


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
