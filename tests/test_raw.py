import pytest

from helpers import build_raw, raw_sample
from noctinirs_host.wire import optical as o
from noctinirs_host.wire.errors import WireError


def test_parse_and_sign_extension():
    p = build_raw([raw_sample((-1, 8388607, -8388608, 5), 4, 3),
                   raw_sample((0, 0, 0, 0), 0, 0xFF)], first=0xFFFFFFFF)
    r = o.parse_frame(p)
    assert isinstance(r, o.RawPacket) and not r.is_science
    assert r.samples[0].ain == (-1, 8388607, -8388608, 5)
    assert (r.samples[0].phase_class, r.samples[0].slot_index) == (4, 3)
    assert [s.sample_sequence for s in r.samples] == [0xFFFFFFFF, 0]  # wraps mod 2^32


def test_gap_before_flag():
    assert o.parse_raw(build_raw(raw_flags=3)).gap_before
    assert not o.parse_raw(build_raw(raw_flags=1)).gap_before


def test_max_size():
    p = build_raw([raw_sample()] * 100)
    assert len(p) == 1416
    assert o.parse_raw(p).sample_count == 100


@pytest.mark.parametrize("kw,code", [
    (dict(raw_flags=0), "raw_flags"),
    (dict(raw_flags=5), "raw_flags"),
    (dict(reserved=1), "reserved_prefix"),
    (dict(samples=[raw_sample(phase=9, slot_idx=1)]), "raw_phase_class"),
    (dict(samples=[raw_sample(phase=0, slot_idx=3)]), "raw_slot_index"),
    (dict(samples=[raw_sample(phase=4, slot_idx=0xFF)]), "raw_slot_index"),
    (dict(samples=[raw_sample(phase=4, slot_idx=20)]), "raw_slot_index"),
    (dict(count=2), "raw_size"),
    (dict(samples=[raw_sample()] * 101), "raw_sample_count"),
    (dict(adc_source=3), "unknown_adc_source"),
])
def test_raw_rejects(kw, code):
    with pytest.raises(WireError) as ei:
        o.parse_frame(build_raw(**kw))
    assert ei.value.code == code


def test_all_phase_classes_accepted():
    p = build_raw([raw_sample(phase=ph, slot_idx=0xFF if ph == 0 else 2) for ph in range(9)])
    assert [s.phase_class for s in o.parse_raw(p).samples] == list(range(9))


def test_empty_and_unknown():
    with pytest.raises(WireError):
        o.parse_frame(b"")
    with pytest.raises(WireError) as ei:
        o.parse_frame(b"\x01\x09" + b"\x00" * 30)
    assert ei.value.code == "unsupported_layout"
