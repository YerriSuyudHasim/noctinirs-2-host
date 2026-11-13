from math import pi, sin, sqrt

import pytest

from noctinirs_host.preview.filters import CausalFilter, FilterConfig, PreviewFilters, design, response, timing
from noctinirs_host.preview.pipeline import ChannelPoint


FS = 10


def test_butterworth_edges_and_stopbands():
    cfg = FilterConfig(bandpass=True)
    sections = design(cfg, FS)
    for f in (.01, .1):
        assert abs(response(sections, f, FS)) == pytest.approx(1 / sqrt(2), rel=1e-8)
    assert abs(response(sections, .001, FS)) < .02
    assert abs(response(sections, 1, FS)) < .02
    assert abs(response(sections, sqrt(.01 * .1), FS)) == pytest.approx(1, rel=1e-5)
    warmup, delay, hz = timing(cfg, FS, sections)
    assert warmup > 0 and delay > 0 and .01 < hz < .1


@pytest.mark.parametrize(("cfg", "cutoff"), [(FilterConfig(detrend=True), .01),
                                             (FilterConfig(lowpass=True), .5)])
def test_single_stage_cutoff(cfg, cutoff):
    assert abs(response(design(cfg, FS), cutoff, FS)) == pytest.approx(1 / sqrt(2))


def test_causal_streaming_sinusoid_and_gap_reset():
    cfg = FilterConfig(lowpass=True)
    sections = design(cfg, FS)
    stream = CausalFilter(sections)
    x = [sin(2*pi*.5*n/FS) for n in range(1000)]
    y = [stream.push(v) for v in x]
    assert sum(v*v for v in y[-200:]) / sum(v*v for v in x[-200:]) == pytest.approx(.5)
    assert y[0] == 0
    assert stream.push(float("nan")) != stream.push(float("nan"))
    assert stream.push(1.) == CausalFilter(sections).push(1.)


def test_filter_bank_leaves_intensity_and_other_channels_alone():
    bank = PreviewFilters(FilterConfig(lowpass=True, channels=("CH1",)), FS)
    point = ChannelPoint("CH1", "long", "VALID", i735=22, od735=1., d_hbo=1., d_hbr=1.)
    other = ChannelPoint("CH2", "long", "VALID", od735=1.)
    points = bank.apply(dict(CH1=point, CH2=other))
    assert points["CH1"].i735 == 22 and points["CH1"].od735 != 1.
    assert points["CH1"].filter_warming and points["CH2"] is other
    gap = ChannelPoint("CH1", "long", "INVALID")
    assert bank.apply(dict(CH1=gap))["CH1"] is gap
    reset = bank.apply(dict(CH1=point))["CH1"]
    assert reset.od735 > points["CH1"].od735 and bank.counts["CH1"] == 2
    for _ in range(51):
        bank.apply(dict(CH1=gap))
    restarted = bank.apply(dict(CH1=point))["CH1"]
    assert restarted.od735 == points["CH1"].od735 and bank.counts["CH1"] == 1
