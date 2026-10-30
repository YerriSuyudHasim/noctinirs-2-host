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
    (dict(reserved=1), "reserved"),
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
    # unknown bits and extra host rules: only this slot becomes host-INVALID
    ("quality>4", slot(2, quality=5), "unknown_quality"),
    ("reserved reason bit", slot(2, quality=0, reason=1 << 12, mask=0, witness=0xFF,
                                 dets=[blank_det()] * 4), "unknown_reason_bits"),
    ("reserved det_flags bit", slot(2, dets=[det(flags=0x9C)] + [det()] * 3), "bad_det_flags"),
    ("GATE_OFF in gated frame", slot(2, dets=[det(flags=0x5C)] + [det()] * 3), "bad_det_flags"),
    ("verified without witness", slot(2, witness=0xFF), "verified_slot_without_witness"),
    ("verified with SAT_ON", slot(2, dets=[det(flags=0x1C | 0x01)] + [det()] * 3),
     "verified_but_saturated"),
    ("verified with SAT_OFF", slot(2, dets=[det()] * 3 + [det(flags=0x1C | 0x02)]),
     "verified_but_saturated"),
    ("verified with stab 0xFE", slot(2, dets=[det(stab=0xFE)] + [det()] * 3),
     "verified_with_unusable_stab_q"),
    ("verified with stab 0xFF", slot(2, dets=[det(stab=0xFF)] + [det()] * 3),
     "verified_with_unusable_stab_q"),
    ("verified with no PASS flags", slot(2, dets=[det(flags=0x00)] + [det()] * 3),
     "verified_flags_not_pass"),
    ("verified with DIFF_NEGATIVE", slot(2, dets=[det(flags=0x1C | 0x20)] + [det()] * 3),
     "verified_flags_not_pass"),
])
def test_inconsistent_slot_is_invalid_not_science(name, s, why):
    f = o.parse_opt2(build_opt2(slot_overrides={2: s}))
    assert f.slots[2].host_invalid == why, name
    assert f.science_mask(2) == 0 and f.science_sample(2, 0) is None
    assert f.science_mask(3) == 0x0F  # other slots still processed
    assert any("slot 2" in w for w in f.warnings)


@pytest.mark.parametrize("name,s,code", [
    # the C codec rejects the whole packet in these cases (golden vectors agree)
    ("stop reason with quality 0", slot(2, quality=0, reason=1 << 6, mask=0, witness=0xFF,
                                        dets=[blank_det()] * 4), "reason_quality"),
    ("warmup reason with quality 3", slot(2, quality=3, reason=1 << 9, mask=0, witness=0xFF,
                                          dets=[blank_det()] * 4), "reason_quality"),
    ("quality 1 with reason", slot(2, quality=1, reason=1 << 10), "reason_quality"),
    ("quality 2 with reason bit 0", slot(2, quality=2, reason=1), "reason_quality"),
    ("stop and warmup together", slot(2, quality=3, reason=(1 << 6) | (1 << 9), mask=0,
                                      witness=0xFF, dets=[blank_det()] * 4), "reason_quality"),
    ("verified with n_on=1", slot(2, n_on=1), "verified_n_lt_2"),
    ("verified with n_off=0", slot(2, n_off=0), "verified_n_lt_2"),
    ("n_on used > available", slot(2, n_on=10, avail_on=9), "used_gt_available"),
    ("n_off used > available", slot(2, n_off=10, avail_off=0), "used_gt_available"),
    ("blank slot, used > available", slot(2, quality=0, reason=1 << 10, mask=0, witness=0xFF,
                                          dets=[blank_det()] * 4, n_on=5, avail_on=4),
     "used_gt_available"),
    ("bad witness", slot(2, witness=7), "slot_inconsistent"),
    ("bad mask", slot(2, mask=0x1F), "slot_inconsistent"),
    ("value present but unverified", slot(2, mask=0b0001, dets=[det()] * 4),
     "slot_inconsistent"),
    ("verified but blank", slot(2, mask=0b0001, dets=[blank_det()] * 4), "slot_inconsistent"),
    ("quality 0 with values", slot(2, quality=0, reason=1 << 10, mask=0, witness=0xFF,
                                   dets=[det()] * 4), "slot_inconsistent"),
    ("partial blank", slot(2, dets=[dict(det(), sum_off=INT32_MIN)] + [det()] * 3),
     "slot_inconsistent"),
    ("masked detector without stab 0xFF",
     slot(2, mask=0b1110, dets=[dict(blank_det(), stab=5)] + [det()] * 3), "slot_inconsistent"),
])
def test_codec_invalid_slot_rejects_whole_packet(name, s, code):
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2(slot_overrides={2: s}))
    assert ei.value.code == code, name


def test_reason_names_match_contract_section_4_3():
    names = [o.REASON_UNDER_K_ON, o.REASON_UNDER_K_OFF, o.REASON_LED_ON_MISSING,
             o.REASON_LED_OFF_MISSING, o.REASON_RUNTIME_TIMING, o.REASON_ADS_INTEGRITY,
             o.REASON_STOP_ABORT, o.REASON_STALE_EPOCH, o.REASON_INTERNAL,
             o.REASON_STARTUP_WARMUP, o.REASON_NO_WITNESS, o.REASON_WITNESS_UNSTABLE]
    assert names == [1 << i for i in range(12)]


@pytest.mark.parametrize("bit", range(12))
def test_every_known_reason_bit_with_valid_quality_is_rejected(bit):
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2(slot_overrides={2: slot(2, quality=2, reason=1 << bit)}))
    assert ei.value.code == "reason_quality"


def test_science_sample_never_claims_k_check_passed():
    f = o.parse_opt2(build_opt2())
    assert f.science_sample(0, 0).quality_vs_k == "not_checked"


def test_stop_and_warmup_slots_are_valid_holes():
    stop = slot(1, quality=3, reason=1 << 6, mask=0, witness=0xFF, dets=[blank_det()] * 4)
    warm = slot(2, quality=4, reason=1 << 9, mask=0, witness=0xFF, dets=[blank_det()] * 4)
    f = o.parse_opt2(build_opt2(slot_overrides={1: stop, 2: warm}))
    assert f.slots[1].host_invalid is None and f.slots[2].host_invalid is None
    assert f.science_mask(1) == 0 and f.science_mask(2) == 0 and not f.warnings


def test_engineering_frame_keeps_values_but_is_never_science():
    slots = [eng_slot(i) for i in range(20)]
    f = o.parse_opt2(build_opt2(slots, frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert f.engineering and not f.is_science and not f.warnings
    assert f.science_mask(0) == 0 and f.science_sample(0, 0) is None
    assert f.slots[0].detectors[0].sum_on == 10000  # not blanked on the host


@pytest.mark.parametrize("name,s", [
    ("verified_mask != 0", slot(0, mask=0x01, witness=0xFF, dets=[det(flags=0x40)] * 4)),
    ("witness != 0xFF", slot(0, mask=0, witness=1, dets=[det(flags=0x40)] * 4)),
    ("reason bit 10", slot(0, mask=0, witness=0xFF, reason=1 << 10, quality=0,
                           dets=[det(flags=0x40)] * 4)),
    ("gate bits set", slot(0, mask=0, witness=0xFF, dets=[det(flags=0x5C)] * 4)),
    ("GATE_OFF missing", slot(0, mask=0, witness=0xFF, dets=[det(flags=0x00)] * 4)),
])
def test_engineering_rule_violations_reject_packet(name, s):
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2([s] + [eng_slot(i) for i in range(1, 20)],
                                frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert ei.value.code == "engineering_rule", name


def test_engineering_used_gt_available_rejects_packet():
    s = dict(eng_slot(0), n_on=5, avail_on=4)
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2([s] + [eng_slot(i) for i in range(1, 20)],
                                frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert ei.value.code == "used_gt_available"


def test_used_equal_or_below_available_is_accepted():
    s = slot(2, n_on=10, avail_on=12, n_off=10, avail_off=10)
    assert o.parse_opt2(build_opt2(slot_overrides={2: s})).science_mask(2) == 0x0F


@pytest.mark.parametrize("flags,gid", [(o.FF_ENGINEERING_UNGATED, 1), (0, 0)])
def test_engineering_flag_and_gate_id_disagree_rejects_packet(flags, gid):
    with pytest.raises(WireError) as ei:
        o.parse_opt2(build_opt2(frame_flags=flags, gate_config_id=gid))
    assert ei.value.code == "engineering_mismatch"


def test_engineering_unknown_bits_only_warn_and_keep_values():
    s = slot(0, quality=5, mask=0, witness=0xFF, reason=1 << 12, dets=[det(flags=0x40)] * 4)
    f = o.parse_opt2(build_opt2([s] + [eng_slot(i) for i in range(1, 20)],
                                frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert not f.is_science and f.science_mask(0) == 0 and len(f.warnings) >= 2
    assert f.slots[0].detectors[0].sum_on == 10000


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


def test_clipped_sd_adds_warning():
    s = slot(0, dets=[det(sd_off=o.SD_CLIPPED)] + [det()] * 3)
    f = o.parse_opt2(build_opt2(slot_overrides={0: s}))
    assert any("clipped" in w and "slot 0" in w for w in f.warnings)


def test_multiple_broken_fields_report_first_check_in_fixed_order():
    # unknown quality AND missing witness AND bad mask at once: the same code every time
    s = slot(2, quality=5, mask=0x1F, witness=7)
    f = o.parse_opt2(build_opt2(slot_overrides={2: s}))
    assert f.slots[2].host_invalid == "unknown_quality"
    assert f.science_sample(2, 0) is None
