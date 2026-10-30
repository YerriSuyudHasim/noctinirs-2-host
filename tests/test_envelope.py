import pytest

from helpers import build_envelope
from noctinirs_host.wire.envelope import parse_envelope
from noctinirs_host.wire.errors import WireError


def test_good_roundtrip():
    e = parse_envelope(build_envelope(b"abc", msg_sequence=7, ts=123456789012, session_id=9))
    assert (e.msg_type, e.payload, e.msg_sequence, e.timestamp_us, e.session_id) == (
        2, b"abc", 7, 123456789012, 9)
    assert not e.simulated and not e.ts_estimated


def test_flags_simulated_and_ts_estimated_accepted():
    e = parse_envelope(build_envelope(b"", flags=3))
    assert e.simulated and e.ts_estimated


@pytest.mark.parametrize("kwargs,code", [
    (dict(magic=0x12345678), "magic"),
    (dict(env_version=2), "envelope_version"),
    (dict(bad_crc=True), "crc"),
    (dict(flags=0x0004), "reserved_flags"),
    (dict(flags=0x8000), "reserved_flags"),
    (dict(msg_type=5), "msg_type"),
    (dict(proto=2), "protocol_version"),
    (dict(length=5), "length"),
])
def test_rejected(kwargs, code):
    with pytest.raises(WireError) as ei:
        parse_envelope(build_envelope(b"abc", **kwargs))
    assert ei.value.code == code


def test_size_limits():
    with pytest.raises(WireError) as ei:
        parse_envelope(b"\x00" * 31)
    assert ei.value.code == "size"
    with pytest.raises(WireError) as ei:
        parse_envelope(build_envelope(b"x" * 1441, length=1441))
    assert ei.value.code == "size"
    assert len(build_envelope(b"x" * 1440)) == 1472
    parse_envelope(build_envelope(b"x" * 1440))  # max datagram accepted
