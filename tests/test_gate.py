import pytest

from helpers import build_opt2, det, eng_slot, slot
from noctinirs_host.gate import GateParams, evaluate, recheck_frame
from noctinirs_host.wire import optical as o

P = GateParams(gate_config_id=1, z_witness_x10=50, z_channel_x10=50, stable_k_x10=30,
               floor_counts=4, se_floor_x100=30)


def ev(sum_on, sum_off, sd=0, stab=0, params=P, n=10, z=50):
    return evaluate(n, n, sum_on, sum_off, sd, sd, stab, params, z)


def test_step_z_exactly_at_threshold_passes():
    # sd=0 -> se_eff = se_floor 0.3 -> thr = 5.0*0.3 = 1.5 ; diff = 15/10 = 1.5
    p = GateParams(1, 50, 50, 30, 1, 30)
    assert ev(15, 0, params=p).step_z_pass
    assert not ev(14, 0, params=p).step_z_pass


def test_floor_and_negative():
    r = ev(30, 0)  # diff 3.0 < floor 4
    assert r.step_z_pass and not r.floor_pass
    assert ev(40, 0).floor_pass
    assert ev(0, 40).diff_negative and not ev(0, 40).step_z_pass


@pytest.mark.parametrize("stab,k10,ok", [
    (48, 30, True), (49, 30, False),      # 10*48 = 480 <= 480
    (36, 23, True), (37, 23, False),      # stable_k 2.3 -> stab_q <= 36
    (0xFE, 2000, False), (0xFF, 2000, False),
])
def test_stable_integer_rule(stab, k10, ok):
    p = GateParams(1, 50, 50, k10, 4, 30)
    assert ev(10000, 0, stab=stab, params=p).stable_pass is ok


def test_blank_or_empty_never_passes():
    for r in (evaluate(10, 10, o.INT32_MIN, 0, 0, 0, 0, P, 50),
              evaluate(10, 10, 0, 0, o.SD_BLANK, 0, 0, P, 50),
              evaluate(0, 10, 100, 0, 0, 0, 0, P, 50)):
        assert not (r.evaluated or r.step_z_pass or r.floor_pass or r.stable_pass)


def test_clean_frame_has_no_mismatch():
    assert recheck_frame(o.parse_opt2(build_opt2()), P) == []


def test_false_pass_is_caught():
    # firmware says all pass, but ON-OFF is only 1 count (diff=1.0 < floor, < threshold)
    weak = det(sum_on=1010, sum_off=1000, flags=0x1C)
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, dets=[weak] * 4)}))
    codes = {m.code for m in recheck_frame(f, P) if m.slot_index == 0}
    assert {"false_pass", "step_z_flag_differs", "floor_flag_differs"} <= codes


def test_missing_pass_flag_on_verified_slot_closes_science():
    d = det(flags=0x04 | 0x10)  # no STABLE flag, but slot claims verified
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, dets=[d] * 4)}))
    assert f.slots[0].host_invalid == "verified_flags_not_pass"
    assert f.science_sample(0, 0) is None
    assert recheck_frame(f, P) == []  # invalid slots are skipped, not double counted


def test_saturation_flag_on_verified_value_closes_science():
    d = det(flags=0x1C | o.DF_SAT_ON)
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, dets=[d] * 4)}))
    assert f.slots[0].host_invalid == "verified_but_saturated"
    assert f.science_sample(0, 0) is None


def test_witness_none_on_verified_slot_closes_science():
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, witness=0xFF)}))
    assert f.slots[0].host_invalid == "verified_slot_without_witness"
    assert f.science_sample(0, 0) is None


def test_stable_flag_set_but_host_says_unstable_is_caught():
    # stab_q=100 -> 10*100 > 16*stable_k, so STABLE flag is a false pass
    d = det(stab=100)
    f = o.parse_opt2(build_opt2(slot_overrides={0: slot(0, dets=[d] * 4)}))
    assert f.slots[0].host_invalid is None
    assert any(m.code == "false_pass" for m in recheck_frame(f, P))


def test_gate_config_id_mismatch():
    f = o.parse_opt2(build_opt2(gate_config_id=2))
    assert [m.code for m in recheck_frame(f, P)] == ["gate_config_id_mismatch"]


def test_engineering_not_applicable():
    f = o.parse_opt2(build_opt2([eng_slot(i) for i in range(20)],
                                frame_flags=o.FF_ENGINEERING_UNGATED, gate_config_id=0))
    assert recheck_frame(f, P) == []


def test_step_z_with_variance_and_unequal_counts():
    # s_on = s_off = 8 counts (sd_q4=128); n_on=4, n_off=16
    # se = sqrt(64/4 + 64/16) = sqrt(20) = 4.4721 ; threshold = 5.0 * se = 22.36
    def run(sum_on):
        return evaluate(4, 16, sum_on, 0, 128, 128, 0, P, 50)
    assert not run(88).step_z_pass  # diff 22.0 < 22.36
    assert run(92).step_z_pass      # diff 23.0 >= 22.36
    # swapping the counts changes se only through the correct term: n_on=16, n_off=4 gives the same se
    assert evaluate(16, 4, 16 * 23, 0, 128, 128, 0, P, 50).step_z_pass
    assert not evaluate(16, 4, 16 * 22, 0, 128, 128, 0, P, 50).step_z_pass
