"""Rev 2.2 selection in IREF code space. No device, UI or recording dependencies."""

from dataclasses import dataclass
from math import isfinite, sqrt

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
    loss_fraction: float = 0.02
    min_occurrences: int = 30
    empirical_tie: float = 0.05

    def __post_init__(self):
        fractions = (self.adc_margin, self.downward_drift, self.loss_fraction, self.empirical_tie)
        nonnegative = (self.upward_drift, self.prediction_error, self.quant_margin)
        if (not all(0 <= x < 1 for x in fractions)
                or not all(isfinite(x) and x >= 0 for x in nonnegative)
                or not all(isfinite(x) and x > 0 for x in
                           (self.analysis_enbw_hz, self.band_snr, self.floor_divisor))
                or type(self.min_occurrences) is not int or self.min_occurrences < 2):
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


def level_exclusions(level, params, offsets=None):
    flags = list(level.excluded)
    numbers = (*level.mean, *level.sigma, *level.minimum, *level.peak)
    if (not all(isfinite(x) for x in numbers)
            or any(x >= params.headroom for x in level.peak)
            or any(x <= -params.headroom for x in level.minimum)):
        flags.append("CLIP_DETECTED")
    if level.count < params.min_occurrences:
        flags.append("INCOMPLETE_DATA")
    if level.code > LEGACY_DEFAULT and level.visits < 5:
        flags.append("TIER2_ENVELOPE_INSUFFICIENT")
    corrections = offsets if offsets is not None else (0,) * len(level.mean)
    if any(p + (params.growth - 1) * max(0, x - a) > params.headroom
           for p, x, a in zip(level.peak, level.mean, corrections, strict=True)):
        flags.append("HEADROOM_LIMITED")
    return tuple(dict.fromkeys(flags))


def empirical_choice(levels, targets, default_code, fs, params, offsets=None, *, model_flags=()):
    """E4 tiers 2 and 3. F3 allows the measured step as a flagged proxy for x."""
    safe = [level for level in levels if 1 <= level.code <= MAX_CODE and not level_exclusions(level, params, offsets)]
    flags = list(model_flags or ("MODEL_UNIDENTIFIED",))
    corrections = offsets if offsets is not None else (0,) * len(levels[0].mean) if levels else ()
    if offsets is None:
        flags.append("A_UNCORRECTED")
    target = params.target(fs)
    scores = {}
    for level in safe:
        if all(level.sigma[i] > 0 for i in targets):
            values = [(level.mean[i] - corrections[i]) / level.sigma[i] for i in targets]
            worst = min(range(len(targets)), key=values.__getitem__)
            if max(values) > 5:  # Rev 2.2 c_on screen on the bright PD
                scores[level.code] = values[worst], targets[worst]

    if scores:
        best = max(snr for snr, _ in scores.values())
        cutoff = best - abs(best) * params.empirical_tie
        code = min(c for c, (snr, _) in scores.items() if snr >= cutoff)
        snr, detector = scores[code]
        if snr < target:
            flags.append("LOW_SIGNAL")
        if snr < target / params.floor_divisor:
            flags.append("SNR_BELOW_FLOOR")
        if code > LEGACY_DEFAULT:
            flags.append("HIGH_CURRENT_PROFILE")
        return Choice(code, 2, tuple(flags), score=snr / target, limiting_detector=detector)

    # Even a default needs V evidence from the current sweep. Prefer measured
    # headroom to extrapolating an unidentified model outside measured levels.
    if not safe:
        return Choice(None, None, (*flags, "NO_HEADROOM_EVIDENCE"))
    flags.extend(("DEFAULT_TIER", "LOW_SIGNAL", "C_ON_UNVERIFIED"))
    codes = {level.code for level in safe}
    if default_code in codes:
        return Choice(default_code, 3, tuple(flags))
    tested_default = next((level for level in levels if level.code == default_code), None)
    flags.append("DEFAULT_FAILS_V" if tested_default else "DEFAULT_UNVERIFIED")
    code = max(codes)
    if code > LEGACY_DEFAULT:
        flags.append("HIGH_CURRENT_PROFILE")
    return Choice(code, 3, (*flags, "HEADROOM_LIMITED"))
