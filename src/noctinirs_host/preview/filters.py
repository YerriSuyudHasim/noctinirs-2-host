"""Causal Butterworth SOS for display-only optical preview."""

from cmath import exp, sqrt as complex_sqrt
from dataclasses import asdict, dataclass, replace
from math import ceil, isfinite, log, pi, prod, sqrt, tan


@dataclass(frozen=True)
class FilterConfig:
    bandpass: bool = False
    band_low: float = .01
    band_high: float = .1
    detrend: bool = False
    lowpass: bool = False
    low_cutoff: float = .5
    channels: tuple[str, ...] = ()

    def validate(self, fs):
        if (not all(isfinite(f) and f > 0 for f in (self.band_low, self.band_high, self.low_cutoff))
                or self.band_low >= self.band_high):
            raise ValueError("Filter cutoffs must be positive, with bandpass low below high.")
        cutoffs = [*([self.band_high] if self.bandpass else []),
                   *([.01] if self.detrend else []), *([self.low_cutoff] if self.lowpass else [])]
        if any(f >= fs / 2 for f in cutoffs):
            raise ValueError("Active filter cutoffs must be below half the optical sample rate.")

    @property
    def label(self):
        return " + ".join(label for active, label in ((self.bandpass, "BP"),
                                                        (self.detrend, "HP"),
                                                        (self.lowpass, "LP")) if active)

    def metadata(self):
        return dict(asdict(self), family="Butterworth", prototype_order=2,
                    scope="causal preview OD and Hb only")


def response(sections, hz, fs):
    z = exp(-2j * pi * hz / fs)
    return prod((b0 + b1*z + b2*z*z) / (1 + a1*z + a2*z*z)
                for b0, b1, b2, a1, a2 in sections)


def _section(pole, zeros):
    return (1., -(zeros[0] + zeros[1]), zeros[0] * zeros[1], -2 * pole.real, abs(pole)**2)


def design(config, fs):
    """Prewarp edges, transform a second-order analog prototype, then bilinear-map."""
    config.validate(fs)
    sections = []
    analog_pole = complex(-1, 1) / sqrt(2)

    def warp(hz):
        return tan(pi * hz / fs)

    def digital(pole):
        return (1 + pole) / (1 - pole)

    def add(poles, zeros, at):
        sos = [_section(digital(pole), pair) for pole, pair in zip(poles, zeros, strict=True)]
        gain = abs(response(sos, at, fs)) ** (-1 / len(sos))
        sections.extend((b0*gain, b1*gain, b2*gain, a1, a2) for b0, b1, b2, a1, a2 in sos)

    if config.bandpass:
        low, high = warp(config.band_low), warp(config.band_high)
        b = analog_pole * (high - low)
        root = complex_sqrt(b*b - 4 * low * high)
        from math import atan
        center = fs * atan(sqrt(low * high)) / pi
        add(((b + root) / 2, (b - root) / 2), ((-1., 1.), (-1., 1.)), center)
    if config.detrend:
        add((warp(.01) / analog_pole,), ((1., 1.),), fs/2)
    if config.lowpass:
        add((warp(config.low_cutoff) * analog_pole,), ((-1., -1.),), 0)
    return tuple(sections)


def timing(config, fs, sections):
    if not sections:
        return 0., 0., 0.
    radius = max(sqrt(section[4]) for section in sections)
    warmup = ceil(log(.01) / log(radius)) if 0 < radius < 1 else 0
    # Group delay is frequency-dependent. Report at a named, in-band frequency.
    hz = sqrt(config.band_low * config.band_high) if config.bandpass else (
        .05 if config.detrend else config.low_cutoff / 2)
    if config.lowpass:
        hz = min(hz, config.low_cutoff / 2)
    delta = hz / 1000
    ratio = response(sections, hz + delta, fs) / response(sections, hz - delta, fs)
    from cmath import phase
    return warmup / fs, -phase(ratio) / (4 * pi * delta), hz


class CausalFilter:
    def __init__(self, sections):
        self.sections = sections
        self.reset()

    def reset(self):
        self.memory = [(0., 0.) for _ in self.sections]

    def push(self, value):
        if not isfinite(value):
            self.reset()
            return float("nan")
        for i, (b0, b1, b2, a1, a2) in enumerate(self.sections):
            z1, z2 = self.memory[i]
            out = b0 * value + z1
            self.memory[i] = (b1 * value - a1 * out + z2, b2 * value - a2 * out)
            value = out
        return value


class PreviewFilters:
    def __init__(self, config, fs):
        self.config = config
        self.sections = design(config, fs)
        self.warmup_s, _, _ = timing(config, fs, self.sections)
        self.warmup_samples = ceil(self.warmup_s * fs)
        self.reset()

    def reset(self):
        self.filters = {}
        self.counts = {}

    def apply(self, points):
        if not self.sections or not self.config.channels:
            return points
        fields = ("od735", "od850", "d_hbo", "d_hbr")
        for name in self.config.channels:
            p = points.get(name)
            if p is None:
                continue
            if p.state != "VALID" and p.state != "SSR_WARMUP":
                self.filters.pop(name, None)
                self.counts.pop(name, None)
                continue
            if name not in self.filters:
                self.filters[name] = [CausalFilter(self.sections) for _ in fields]
                self.counts[name] = 0
            self.counts[name] += 1
            values = {field: f.push(getattr(p, field)) for field, f in zip(fields, self.filters[name])}
            values["d_hbt"] = values["d_hbo"] + values["d_hbr"]
            points[name] = replace(p, **values, filter_warming=self.counts[name] < self.warmup_samples)
        return points
