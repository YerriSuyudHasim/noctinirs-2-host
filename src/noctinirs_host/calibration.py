"""Rev 2.2 selection in IREF code space. No device, UI or recording dependencies."""

from dataclasses import dataclass
from math import isfinite, log, log1p, sqrt

FS = 8388607
MAX_CODE = 222
LEGACY_DEFAULT = 133


@dataclass(frozen=True)
class Assumptions:
    adc_margin: float = 0.05
    upward_drift: float = 0.2
    downward_drift: float = 0.2
    prediction_error: float = 0.05
    analysis_enbw_hz: float = 0.1
    band_snr: float = 86 * sqrt(50)
    floor_divisor: float = 3
    quant_margin: float = 0.02
    loss_fraction: float = 0.25
    min_occurrences: int = 24
    visit_target_occurrences: int = 31
    empirical_tie: float = 0.05
    short_interim_code: int = 67
    empirical_target_snr: float = 20.0
    empirical_floor_snr: float = 5.0
    live_detector_snr: float = 5.0
    drift_noise_multiple: float = 3.0
    stable_repeat_count: int = 3
    stable_repeat_spread: float = 0.10
    off_window: str = "reported"
    # Tahap 2 (robust estimator). Host-chosen, provisional; see HOST_R3_TAHAP2_REPORT.
    min_visits_per_code: int = 2        # fewer clean visits: level cannot be chosen (INSUFFICIENT_VISITS)
    min_robust_visits: int = 3          # from here the spread of per-visit sigma gives the uncertainty
    unknown_uncertainty: float = 0.25   # RETIRED in tahap 2.1 (selection no longer uses SNR ties); kept so older JSON loads
    tie_sigma: float = 2.0              # RETIRED in tahap 2.1, see above
    drift_cap_fraction: float = 0.15    # upper bound of the drift tolerance, as a fraction of the step
    # Tahap 2.1 (Owner decision 2026-11-13): choose the code whose ON-OFF step on the brightest live target
    # detector is closest (log distance) to a target level, among codes that pass the SNR floor and have
    # enough clean visits. Host-chosen and PROVISIONAL; basis in docs/preview-quality-metrics.md.
    target_level_long: float = 0.10     # step as a fraction of full scale, long slots
    target_level_short: float = 0.20    # step as a fraction of full scale, short slots (cap <133 keeps this reachable only near 132)
    min_choice_visits: int = 3          # clean visits a level needs to be a normal candidate (fewer: flagged fallback only)

    def __post_init__(self):
        if (not all(isfinite(x) and x > 0 for x in (self.empirical_target_snr,
                self.empirical_floor_snr, self.live_detector_snr, self.drift_noise_multiple,
                self.stable_repeat_spread)) or self.empirical_floor_snr > self.empirical_target_snr
                or type(self.stable_repeat_count) is not int or not 3 <= self.stable_repeat_count <= 5
                or type(self.visit_target_occurrences) is not int or self.visit_target_occurrences < 2
                or self.off_window not in ("reported", "29-38")
                or type(self.min_visits_per_code) is not int or self.min_visits_per_code < 1
                or type(self.min_robust_visits) is not int or self.min_robust_visits < 3
                or not all(isfinite(x) and x > 0 for x in (self.unknown_uncertainty, self.tie_sigma))
                or not (isfinite(self.drift_cap_fraction) and 0 < self.drift_cap_fraction < 1)
                or not all(isfinite(x) and 0 < x < 1 for x in (self.target_level_long, self.target_level_short))
                or type(self.min_choice_visits) is not int or self.min_choice_visits < self.min_visits_per_code):
            raise ValueError("Invalid empirical calibration parameters.")
        fractions = (self.adc_margin, self.downward_drift, self.loss_fraction, self.empirical_tie)
        nonnegative = (self.upward_drift, self.prediction_error, self.quant_margin)
        if (not all(0 <= x < 1 for x in fractions)
                or not all(isfinite(x) and x >= 0 for x in nonnegative)
                or not all(isfinite(x) and x > 0 for x in
                           (self.analysis_enbw_hz, self.band_snr, self.floor_divisor))
                or type(self.min_occurrences) is not int or self.min_occurrences < 2
                or type(self.short_interim_code) is not int or not 1 <= self.short_interim_code < LEGACY_DEFAULT):
            raise ValueError("Invalid calibration assumptions.")

    @property
    def headroom(self):
        return FS * (1 - self.adc_margin)

    @property
    def growth(self):
        return (1 + self.upward_drift) * (1 + self.prediction_error)

    def target(self, fs):
        return self.band_snr * sqrt(2 * self.analysis_enbw_hz / fs)


@dataclass(frozen=True)
class DetectorModel:
    k: float
    l0: float
    offset: float
    sigma0: float
    rho: float
    shot: float = 0

    def snr(self, x):
        return x / sqrt(self.sigma0 ** 2 + self.shot * x + (self.rho * x) ** 2)


@dataclass(frozen=True)
class Level:
    code: int
    mean: tuple[float, ...]
    sigma: tuple[float, ...]
    peak: tuple[float, ...]
    minimum: tuple[float, ...]
    visits: int
    count: int
    excluded: tuple[str, ...] = ()
    envelope: tuple[float, ...] = ()
    repeat_relative_spread: tuple[float, ...] = ()
    # Informational only; never excludes a level. warnings e.g. NOISE_FALLBACK_WITHIN_SIGMA.
    warnings: tuple[str, ...] = ()
    # Relative uncertainty of the SNR per detector (None-free; 0 when unknown), from per-visit sigma spread.
    uncertainty: tuple[float, ...] = ()
    sigma_visits: tuple[int, ...] = ()   # number of visits that contributed to sigma, per detector


@dataclass(frozen=True)
class Choice:
    code: int | None
    tier: int | None
    flags: tuple[str, ...]
    down: float | None = None
    up: float | None = None
    score: float | None = None
    limiting_detector: int | None = None


def x_min(snr, sigma0, rho, shot=0):
    if not all(isfinite(x) for x in (snr, sigma0, rho, shot)) or min(snr, sigma0) <= 0 or min(rho, shot) < 0:
        raise ValueError("INVALID_NOISE_MODEL")
    d = 1 - (snr * rho) ** 2
    if d <= 0:
        raise ValueError("SNR_UNREACHABLE")
    b = snr ** 2 * shot
    return (b + sqrt(b ** 2 + 4 * d * (snr * sigma0) ** 2)) / (2 * d)


def model_bounds(models, targets, fs, params):
    """E0/E2 bounds for already identified and validated detector models."""
    if any(not all(isfinite(x) for x in (m.k, m.l0, m.offset)) or m.k <= 0 for m in models):
        raise ValueError("K_UNDERDETERMINED")
    upper = min((params.headroom - m.l0 - max(0, m.offset)) / (params.growth * m.k) for m in models)
    if upper <= 0:
        raise ValueError("HEADROOM_EMPTY")
    target = params.target(fs)
    lower = max(x_min(target, models[i].sigma0, models[i].rho, models[i].shot) /
                ((1 - params.downward_drift) * models[i].k) for i in targets)
    return lower, upper


def model_choice(models, targets, g, c_min, c_max, fs, params):
    """E1 assumes an externally validated shape, sampled at every integer in its domain.

    Enumerating at most 222 integer codes gives the same discrete optimum as floor/ceil
    of the inverse, also for a nonlinear lookup. A lower code wins an exact tie.
    """
    if not 1 <= c_min <= c_max <= MAX_CODE:
        return Choice(None, None, ("OUT_OF_SWEEP",))
    domain = list(range(c_min, c_max + 1))
    values = [g(c) for c in domain]
    if (not all(isfinite(t) and t > 0 for t in values)
            or any(a >= b for a, b in zip(values, values[1:]))):
        return Choice(None, None, ("G_NONMONOTONIC",))
    try:
        lo, hi = model_bounds(models, targets, fs, params)
    except ValueError as exc:
        return Choice(None, None, (str(exc),))
    if hi < values[0]:
        return Choice(None, None, ("OVER_RANGE",))
    if lo > hi:
        return Choice(None, None, ("NO_SHARED_RANGE",))
    if lo > values[-1] and c_max < MAX_CODE:
        return Choice(None, None, ("OUT_OF_SWEEP",))
    flags = []
    if lo > values[-1]:
        index = len(domain) - 1
        flags.append("LOW_SIGNAL")
    else:
        valid = [i for i, t in enumerate(values) if lo <= t <= hi]
        if not valid:
            return Choice(None, None, ("QUANT_INFEASIBLE",))
        index = max(valid, key=lambda i: min(values[i] / lo, hi / values[i]))
    t = values[index]
    down, up = t / lo, hi / t
    if 1 <= min(down, up) < 1 + params.quant_margin:
        flags.append("LOW_MARGIN")
    if min(models[i].snr(models[i].k * t) for i in targets) < params.target(fs) / params.floor_divisor:
        flags.append("SNR_BELOW_FLOOR")
    if domain[index] > LEGACY_DEFAULT:
        flags.append("HIGH_CURRENT_PROFILE")
    return Choice(domain[index], 1, tuple(flags), down, up)


def visit_shortfall(level, params):
    """Informational flag: a level with too few clean visits is not scored in tier 2."""
    return ("INSUFFICIENT_VISITS",) if level.visits < params.min_visits_per_code else ()


def level_exclusions(level, params, offsets=None):
    flags = list(level.excluded)
    numbers = (*level.mean, *level.sigma, *level.minimum, *level.peak, *level.envelope)
    if (not all(isfinite(x) for x in numbers)
            or any(x >= params.headroom for x in level.peak)
            or any(x <= -params.headroom for x in level.minimum)):
        flags.append("CLIP_DETECTED")
    if level.count < params.min_occurrences:
        flags.append("INCOMPLETE_DATA")
    if level.code > LEGACY_DEFAULT and level.visits < 5 and not (
            level.visits >= params.stable_repeat_count and level.repeat_relative_spread
            and all(isfinite(x) and 0 <= x <= params.stable_repeat_spread
                    for x in level.repeat_relative_spread)):
        flags.append("TIER2_ENVELOPE_INSUFFICIENT")
    corrections = offsets if offsets is not None else (0,) * len(level.mean)
    if any(p + (params.growth - 1) * max(0, x - a) > params.headroom
           for p, x, a in zip(level.peak, level.envelope or level.mean, corrections, strict=True)):
        flags.append("HEADROOM_LIMITED")
    return tuple(dict.fromkeys(flags))


def empirical_choice(levels, targets, default_code, fs, params, offsets=None, *, model_flags=(), short=False):
    """E4 tiers 2 and 3. F3 allows the measured step as a flagged proxy for x."""
    cap = LEGACY_DEFAULT - 1 if short else MAX_CODE
    safe = [level for level in levels if 1 <= level.code <= cap and not level_exclusions(level, params, offsets)]
    flags = list(model_flags or ("MODEL_UNIDENTIFIED",))
    corrections = offsets if offsets is not None else (0,) * len(levels[0].mean) if levels else ()
    if offsets is None:
        flags.append("A_UNCORRECTED")
    target = params.empirical_target_snr
    # A detector must be alive somewhere in the current safe sweep. Keep this
    # target set fixed across codes; never cherry-pick a different PD per level.
    live = tuple(i for i in targets if any(level.sigma[i] > 0 and
                 (level.mean[i] - corrections[i]) / level.sigma[i] >= params.live_detector_snr
                 for level in safe))
    if set(live) != set(targets):
        flags.append("LOW_QUALITY_DETECTOR")
    targets = live
    scores = {}
    for level in safe:
        # Scoring needs a minimum of clean visits (outlier-robust estimator needs a sample).
        if level.visits < params.min_visits_per_code:
            continue
        if targets and all(level.sigma[i] > 0 for i in targets):
            values = [(level.mean[i] - corrections[i]) / level.sigma[i] for i in targets]
            worst = min(range(len(targets)), key=values.__getitem__)
            if max(values) > 5:  # Rev 2.2 c_on screen on the bright PD
                scores[level.code] = values[worst], targets[worst]

    # Tahap 2.1 selection. 1) a level must reach the SNR floor on its limiting detector (a level below the
    # floor is never chosen while any other candidate passes); 2) levels with at least min_choice_visits
    # clean visits are the normal candidates, thinner levels only when nothing else passes (flagged);
    # 3) among candidates take the step closest to the target level (log distance), levels within
    # empirical_tie of each other count as equally close and the higher SNR wins, then the lower code.
    # Nothing passes: fall through to the tier-3 default, never to "the dimmest level".
    passing = {c: v for c, v in scores.items() if v[0] >= params.empirical_floor_snr}
    if passing:
        by_code = {level.code: level for level in safe}
        full = {c: v for c, v in passing.items() if by_code[c].visits >= params.min_choice_visits}
        pool = full or passing
        goal = params.target_level_short if short else params.target_level_long
        step = {c: max((by_code[c].mean[i] - corrections[i] for i in targets), default=0.0) / FS for c in pool}
        distance = {c: abs(log(step[c] / goal)) if step[c] > 0 else float("inf") for c in pool}
        nearest = min(distance.values())
        equal = [c for c in pool if distance[c] <= nearest + log1p(params.empirical_tie)]
        code = max(equal, key=lambda c: (pool[c][0], -c))
        snr, detector = pool[code]
        if len(equal) > 1 and code != min(distance, key=distance.get):
            flags.append("TIE_HIGHER_SNR")
        if not full:
            flags.append("VISIT_FALLBACK")
        if by_code[code].visits < params.min_choice_visits:
            flags.append("FEW_VISITS")
        flags.extend(w for w in by_code[code].warnings if w not in flags)
        if step[code] < goal * (1 - params.empirical_tie):
            flags.append("BELOW_TARGET_LEVEL")   # the safe sweep never reached the target level
        if snr < target:
            flags.append("LOW_SIGNAL")
        if code > LEGACY_DEFAULT:
            flags.append("HIGH_CURRENT_PROFILE")
        return Choice(code, 2, tuple(flags), score=snr / target, limiting_detector=detector)

    # Even a default needs V evidence from the current sweep. Prefer measured
    # headroom to extrapolating an unidentified model outside measured levels.
    default_code = min(default_code, LEGACY_DEFAULT)
    if short:
        default_code = min(default_code, params.short_interim_code)
        flags.extend((f"SHORT_INTERIM_{default_code}", "ASSUMED_SHORT_DEFAULT"))
    flags.extend(("DEFAULT_TIER", "LOW_SIGNAL", "C_ON_UNVERIFIED"))
    codes = {level.code for level in safe if level.code <= default_code}
    if default_code in codes:
        return Choice(default_code, 3, tuple(flags))
    tested_default = next((level for level in levels if level.code == default_code), None)
    if tested_default is None:
        flags.append("DEFAULT_UNVERIFIED")
    else:
        exclusions = level_exclusions(tested_default, params, offsets)
        flags.extend(exclusions)
        if {"CLIP_DETECTED", "HEADROOM_LIMITED"}.intersection(exclusions):
            flags.append("DEFAULT_FAILS_V")
    if not codes:
        return Choice(None, None, (*flags, "NO_HEADROOM_EVIDENCE"))
    return Choice(max(codes), 3, tuple(flags))
