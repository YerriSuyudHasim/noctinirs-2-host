import pytest

from helpers import INT32_MIN, blank_det, blank_slot, build_opt2, det, eng_slot, slot
from noctinirs_host.wire import optical as o
from noctinirs_host.wire.errors import WireError


def test_size_is_1384():
    assert len(build_opt2()) == o.OPT2_PAYLOAD_SIZE == 1384


def test_good_frame_all_science():
    f = o.parse_frame(build_opt2())
    assert isinstance(f, o.Opt2Frame) and f.is_science and not f.warnings
    assert f.science_mask(0) == 0x0F
    s = f.science_sample(0, 2)
    assert (s.sum_on, s.n_on, s.sd_on, s.clipped_on) == (10000, 10, 3.0, False)


def test_blank_slot_is_a_hole():
    f = o.parse_opt2(build_opt2(slot_overrides={4: blank_slot(4)}))
    assert f.science_mask(4) == 0 and f.slots[4].host_invalid is None
    assert f.science_sample(4, 0) is None and f.science_mask(5) == 0x0F


def test_partial_mask():
    s = slot(3, mask=0b0101)
    f = o.parse_opt2(build_opt2(slot_overrides={3: s}))
    assert f.science_mask(3) == 0b0101 and f.science_sample(3, 1) is None


@pytest.mark.parametrize("payload_len", [1383, 1385, 1416])
def test_wrong_size_rejected(payload_len):
    p = build_opt2()
    p = p[:payload_len] if payload_len < len(p) else p + b"\x00" * (payload_len - len(p))
    with pytest.raises(WireError) as ei:
        o.parse_frame(p)
    assert ei.value.code == "opt2_size"


def test_layout1_rejected_with_message():
    with pytest.raises(WireError) as ei:
        o.parse_frame(build_opt2(layout=1))
    assert ei.value.code == "unsupported_layout"
    assert "layout tidak didukung" in ei.value.message and "layout 1" in ei.value.message


@pytest.mark.parametrize("kw,code", [
    (dict(layout=9), "unsupported_layout"),
    (dict(adc_source=2), "unknown_adc_source"),
    (dict(slot_count=19), "bad_counts"),
    (dict(det_count=3), "bad_counts"),
    (dict(reserved=1), "reserved_prefix"),
])
def test_frame_level_rejects(kw, code):
    with pytest.raises(WireError) as ei:
        o.parse_frame(build_opt2(**kw))
    assert ei.value.code == code


def test_slot_index_mismatch_rejected():
    bad = slot(7)
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2(slot_overrides={3: bad}))
    assert ei.value.code == "slot_index"


@pytest.mark.parametrize("name,s,why", [
    ("quality>4", slot(2, quality=5), "unknown_quality"),
    ("reserved reason bit", slot(2, quality=0, reason=1 << 12, mask=0, witness=0xFF,
                                 dets=[blank_det()] * 4), "unknown_reason_bits"),
    ("stop reason with quality 0", slot(2, quality=0, reason=1 << 6, mask=0, witness=0xFF,
                                        dets=[blank_det()] * 4), "reason_quality_mismatch"),
    ("warmup reason with quality 3", slot(2, quality=3, reason=1 << 9, mask=0, witness=0xFF,
                                          dets=[blank_det()] * 4), "reason_quality_mismatch"),
    ("quality 1 with reason", slot(2, quality=1, reason=1 << 10), "reason_quality_mismatch"),
    ("reserved det_flags bit", slot(2, dets=[det(flags=0x9C)] + [det()] * 3), "bad_det_flags"),
    ("GATE_OFF in gated frame", slot(2, dets=[det(flags=0x5C)] + [det()] * 3), "bad_det_flags"),
    ("bad witness", slot(2, witness=7), "bad_witness"),
    ("bad mask", slot(2, mask=0x1F), "bad_verified_mask"),
    ("value present but unverified", slot(2, mask=0b0001, dets=[det()] * 4),
     "unverified_value_present"),
    ("verified but blank", slot(2, mask=0b0001, dets=[blank_det()] * 4), "verified_but_blank"),
    ("quality 0 with values", slot(2, quality=0, reason=1 << 10, mask=0, witness=0xFF,
                                   dets=[det()] * 4), "values_present_but_not_verified"),
    ("partial blank", slot(2, dets=[dict(det(), sum_off=INT32_MIN)] + [det()] * 3),
     "partial_blank"),
    ("verified with n=1", slot(2, n_on=1), "verified_with_too_few_samples"),
])
def test_inconsistent_slot_is_invalid_not_science(name, s, why):
    f = o.parse_opt2(build_opt2(slot_overrides={2: s}))
    assert f.slots[2].host_invalid == why, name
    assert f.science_mask(2) == 0 and f.science_sample(2, 0) is None
    assert f.science_mask(3) == 0x0F  # other slots still processed
    assert any("slot 2" in w for w in f.warnings)


def test_stop_and_warmup_slots_are_valid_holes():
    stop = slot(1, quality=3, reason=1 << 6, mask=0, witness=0xFF, dets=[blank_det()] * 4)
    warm = slot(2, quality=4, reason=1 << 9, mask=0, witness=0xFF, dets=[blank_det()] * 4)
    f = o.parse_opt2(build_opt2(slot_overrides={1: stop, 2: warm}))
    assert f.slots[1].host_invalid is None and f.slots[2].host_invalid is None
    assert f.science_mask(1) == 0 and f.science_mask(2) == 0 and not f.warnings


def test_blank_detector_without_ff_stab_warns_but_stays_valid():
    d = dict(blank_det(), stab=5)
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, mask=0b1110, dets=[d] + [det()] * 3)}))
    assert f.science_mask(0) == 0b1110 and any("stab_q=0xFF" in w for w in f.warnings)


def test_engineering_frame_keeps_values_but_is_never_science():
    slots = [eng_slot(i) for i in range(20)]
    f = o.parse_opt2(build_opt2(slots, frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert f.engineering and not f.is_science and not f.warnings
    assert f.science_mask(0) == 0 and f.science_sample(0, 0) is None
    assert f.slots[0].detectors[0].sum_on == 10000  # not blanked on the host


def test_engineering_rule_violations_only_warn():
    bad = slot(0, mask=0x01, witness=1, dets=[det(flags=0x1C)] * 4)
    f = o.parse_opt2(build_opt2([bad] + [eng_slot(i) for i in range(1, 20)],
                                frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert not f.is_science and f.science_mask(0) == 0 and len(f.warnings) >= 3


def test_engineering_flag_and_gate_id_disagree_is_non_science():
    f = o.parse_opt2(build_opt2(frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=1))
    assert not f.is_science and any("disagree" in w for w in f.warnings)
    f2 = o.parse_opt2(build_opt2(frame_flags=0, gate_config_id=0))
    assert not f2.is_science


@pytest.mark.parametrize("flag", [o.FF_STARTING_GATE, o.FF_WARMUP])
def test_starting_gate_and_warmup_never_science(flag):
    f = o.parse_opt2(build_opt2(frame_flags=flag))
    assert not f.is_science and f.science_mask(0) == 0


def test_unknown_frame_flags_invalidate_every_slot():
    f = o.parse_opt2(build_opt2(frame_flags=0x0100))
    assert all(f.science_mask(i) == 0 for i in range(20))
    assert all(s.host_invalid == "unknown_frame_flags" for s in f.slots)


def test_clipped_sd_is_reported_not_exact():
    s = slot(0, dets=[det(sd_on=o.SD_CLIPPED)] + [det()] * 3)
    f = o.parse_opt2(build_opt2(slot_overrides={0: s}))
    sm = f.science_sample(0, 0)
    assert sm.clipped_on and sm.sd_on == 4095.875 and not sm.clipped_off
