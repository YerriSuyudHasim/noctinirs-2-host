from helpers import build_opt2, build_raw, raw_sample, slot
from noctinirs_host.wire import optical as o
from noctinirs_host.wire.counters import REJECT_REASONS, RejectCounters


def test_each_named_counter_counts_its_own_rejection():
    c = RejectCounters()
    cases = {
        "reserved": build_opt2(reserved=1),
        "engineering_mismatch": build_opt2(frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=1),
        "verified_n_lt_2": build_opt2(slot_overrides={0: slot(0, n_on=1)}),
        "reason_quality": build_opt2(slot_overrides={0: slot(0, quality=2, reason=1)}),
        "used_gt_available": build_opt2(slot_overrides={0: slot(0, n_on=10, avail_on=9)}),
        "stab_n_lt_4": build_opt2(slot_overrides={0: slot(0, n_on=3)}),
        "raw_flags": build_raw(raw_flags=0),
        "sample_count": build_raw(samples=[raw_sample()] * 101),
    }
    for name, payload in cases.items():
        assert c.parse(payload) is None, name
    assert c.named() == {r: 1 for r in REJECT_REASONS}
    assert c.frames_ok == 0


def test_good_frame_and_invalid_slot_are_counted():
    c = RejectCounters()
    assert c.parse(build_opt2()) is not None
    assert c.parse(build_opt2(slot_overrides={1: slot(1, quality=5)})) is not None
    assert c.frames_ok == 2 and c.slot_invalid["unknown_quality"] == 1
    assert sum(c.rejected.values()) == 0


def test_other_rejections_are_not_dropped():
    c = RejectCounters()
    assert c.parse(build_opt2()[:-1]) is None
    assert c.rejected["opt2_size"] == 1


def test_opt2_sample_count_zero_is_counted_as_sample_count():
    c = RejectCounters()
    p = bytearray(build_opt2())
    p[12:14] = (0).to_bytes(2, "little")
    assert c.parse(bytes(p)) is None
    assert c.named()["sample_count"] == 1
