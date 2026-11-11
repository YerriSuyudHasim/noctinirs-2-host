from dataclasses import replace
from hashlib import sha256
import json
from math import sqrt

import pytest

from noctinirs_host.calibration import Assumptions, DetectorModel, Level, empirical_choice, model_bounds, model_choice, x_min
from noctinirs_host.calibration_sweep import SweepPlan, Visit
from noctinirs_host.control import protocol as p
from noctinirs_host.control.default_table import SHORT_SLOTS
from noctinirs_host.control.session import SessionController
from noctinirs_host.preview.filters import FilterConfig
from noctinirs_host.preview.slots import PH_OFF_STABLE, PH_ON_STABLE
from noctinirs_host.wire.optical import PH_OUTSIDE, RAW_SLOT_NONE, RawPacket, RawSample
from noctinirs_host.engineering import EngineeringRun, StartExhausted
from noctinirs_host.geometry import load_geometry
from test_engineering_run import GEOMETRY, poll
from test_udp_session import Clock


def test_rev22_section_8():
    params = Assumptions()
    assert x_min(86, 30, .005) == pytest.approx(2857.684, abs=.001)
    for k, sigma, expected_snr, after_drift in [
        (70, 30, 100.70, 84.51), (1730 / 30, sqrt(144**2 - 8.65**2), 19.94, 15.98),
    ]:
        detector = DetectorModel(.225 * k, 200000, 0, sigma, .005)
        choice = model_choice([detector], [0], float, 1, 222, 10, params)
        assert choice.code == 222 and "LOW_SIGNAL" in choice.flags
        assert detector.snr(detector.k * choice.code) == pytest.approx(expected_snr, abs=.01)
        assert detector.snr(detector.k * choice.code * .8) == pytest.approx(after_drift, abs=.01)
        if sigma == 30:
            assert choice.down == pytest.approx(.97883, abs=.00001)
            assert detector.snr(detector.k * 133) == pytest.approx(65.923, abs=.001)
        else:
            assert "SNR_BELOW_FLOOR" in choice.flags
    detectors = [DetectorModel(.225 * k, 200000, 0, 100, .005) for k in (29700, 273000)]
    assert x_min(86, 100, .005) == pytest.approx(9525.615, abs=.001)
    assert detectors[1].l0 + detectors[1].k * 133 == 8369525
    for up, down, delta, code, lower, upper in [
        (1, 0, 0, 10, 1.4255, 63.241), (1, 0, .05, 9, 1.4255, 60.230),
        (.2, .2, .05, 13, 1.7818, 100.383), (1, .5, .05, 13, 2.8509, 60.230),
    ]:
        p = replace(params, upward_drift=up, downward_drift=down, prediction_error=delta)
        assert model_bounds(detectors, [0], 10, p) == pytest.approx((lower, upper), abs=.001)
        assert model_choice(detectors, [0], float, 1, 222, 10, p).code == code


def test_tier2_requires_five_visits_and_tier3_requires_headroom():
    level = Level(177, (1000,), (1,), (2000,), (0,), 4, 40)
    clipped = Level(133, (1000,), (0,), (8e6,), (0,), 5, 40)
    weak = Level(67, (1,), (1,), (2000,), (0,), 2, 40)
    params = Assumptions()
    choice = empirical_choice([level, clipped, weak], [0], 133, 10, params)
    assert (choice.code, choice.tier) == (67, 3)
    assert "DEFAULT_FAILS_V" in choice.flags
    choice = empirical_choice([replace(level, visits=5), clipped], [0], 133, 10, params)
    assert (choice.code, choice.tier) == (177, 2)
    assert "A_UNCORRECTED" in choice.flags


def run_fake(**options):
    clock = Clock()
    run = EngineeringRun(load_geometry(GEOMETRY), fake_options={"clock": clock, **options},
                         controller_factory=lambda transport: SessionController(transport, clock),
                         sweep_plan=SweepPlan((8, 133, 177), (4, 16, 67), 1))
    run.connect(True, "", "")
    run.configure(10)
    return run, clock


def complete_sweep(run, clock):
    run.calibrate()
    for _ in range(5000):
        clock.now += .05
        run.tick()
        if not run.sweep:
            break
    assert run.calibration, run.error


@pytest.mark.parametrize("stage2_model", [False, True])
def test_fake_calibration_is_consumed_and_archived(tmp_path, stage2_model):
    run, clock = run_fake(stage2_model=stage2_model)
    try:
        with pytest.raises(ValueError, match="fresh calibration"):
            run.start()
        complete_sweep(run, clock)
        sweep = run.calibration
        visits = sweep.document["visits"]
        sweep_ids = {visit["profile_id"] for visit in visits}
        assert len(sweep_ids) == len(visits) and sweep.profile_id not in sweep_ids
        assert sweep.document["short_interim"] == dict(code=67, slots=list(SHORT_SLOTS), scope="host tier 3",
                                                       status="assumed_pending_caliper", firmware_table_written=False)
        assert all(visit["codes"][slot] < 133 for visit in visits for slot in SHORT_SLOTS)
        assert all(sweep.codes[slot] < 133 for slot in SHORT_SLOTS)
        run.start_recorded(str(tmp_path))
        poll(run, lambda: run.recording_used)
        for _ in range(30):
            clock.now += .05
            run.tick()
        assert run._recording_apply_verified, run.error
        if stage2_model:
            assert run.controller._t.simulator._table == (0, None, (133,) * 20, "UNSET")
        profile = run._recording_calibration
        assert profile.profile_id not in sweep_ids | {sweep.profile_id}
        assert profile.codes == sweep.codes
        run.set_preview_config(replace(run.preview_config, filters=FilterConfig(lowpass=True, channels=("CH1",))))
        run.stop()
        poll(run, lambda: run.recorder_state == "FINALIZED")
        with pytest.raises(ValueError, match="fresh calibration"):
            run.start()
        sidecar = json.loads((tmp_path / "record_1.json").read_text(encoding="utf8"))
        saved = sidecar["raw"]["context_at_start"]["calibration"]
        assert saved["profile_id"] == profile.profile_id
        assert (saved["sweep_profile_id"], saved["sweep_profile_hash"]) == (sweep.profile_id, sweep.profile_hash)
        assert profile.document["sweep_profile_hash"] == sweep.profile_hash
        end = sidecar["raw"]["context_at_end"]["calibration_end"]
        assert f"profile_id=0x{profile.profile_id:08X}" in end["start_armed_readback"]["report"]
        assert f"profile_hash={profile.profile_hash}" in end["running"]["report"]
        assert sha256((tmp_path / "record_1.iref-v1.json").read_bytes()).hexdigest() == profile.profile_hash
        assert sum(e["kind"] == "preview_filter" for e in sidecar["events"]) == 1
        defaults = profile.document["default_table"]
        assert defaults["source"] == ("BUILD" if stage2_model else "legacy_get_profile")
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_calibration_rejects_start_ack_without_matching_application():
    run, clock = run_fake(start_outcome="profile_not_applied")
    try:
        run.calibrate()
        for _ in range(100):
            clock.now += .05
            run.tick()
        assert run.sweep is None and run.calibration is None
        assert "before profile application" in run.error
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_stage2_table_and_start_freshness():
    run, clock = run_fake()
    ctl = run.controller
    try:
        assert ctl.start().reply.status == p.STATUS_PRECONDITION_FAILED
        assert ctl.query_profile().reply.profile.start_gate == "NO_PROFILE"
        assert ctl.query_default_table().reply.default_table.source == "BUILD"
        # Bypass host encoding to exercise the firmware model's rule order independently.
        wrong = p.args_set_profile(7, b"x" * 32, (67,) * 20) + b"\x00"
        assert ctl._run("set_default_table", p.OP_SET_DEFAULT_TABLE, wrong).status == "INVALID_ARG"
        rejected = ctl.query_default_table().reply.default_table
        assert (rejected.last_reject, rejected.reject_slot, rejected.source) == ("LONG_NOT_DEFAULT", 2, "BUILD")
        codes = tuple(20 if slot in SHORT_SLOTS else 133 for slot in range(20))
        with pytest.raises(ValueError, match="strict"):
            ctl.set_default_table(7, b"x" * 32, (133,) * 20, "PROVISIONAL", SHORT_SLOTS)
        assert ctl.set_default_table(7, b"x" * 32, codes, "SET", SHORT_SLOTS).status == "LOCAL_REJECT"
        assert ctl.set_default_table(7, b"x" * 32, codes, "PROVISIONAL", SHORT_SLOTS).ok
        table = ctl.query_default_table().reply.default_table
        assert (table.source, table.id, table.hash, table.short_status, table.codes) == (
            "RAM", 7, (b"x" * 32).hex(), "PROVISIONAL", codes)
        assert ctl.set_default_table(7, b"x" * 32, codes, "SET", SHORT_SLOTS).ok
        assert ctl.set_default_table(7, b"x" * 32, codes, "PROVISIONAL", SHORT_SLOTS).status == "LOCAL_REJECT"
        assert ctl.set_profile(5, b"y" * 32, codes).ok
        assert ctl.start().ok
        clock.now += .01
        assert ctl.query_profile().reply.profile.session_table_id == 7
        assert ctl.stop().ok
        assert ctl.query_profile().reply.profile.state == "CONSUMED"
        assert ctl.start().reply.status == p.STATUS_PRECONDITION_FAILED
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_preflight_rejects_changed_profile_and_requires_another_sweep():
    run, clock = run_fake()
    try:
        complete_sweep(run, clock)
        assert run.controller.set_profile(42, b"x" * 32, (1,) * 20).ok
        with pytest.raises(ValueError, match="profile changed"):
            run.start()
        assert run.stopped and run.calibration is None
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_short_interim_fallback_passes_v_or_steps_down():
    params = Assumptions()
    weak = Level(67, (1,), (1,), (2000,), (0,), 2, 40)
    lower = replace(weak, code=32)
    higher = replace(weak, code=100)
    levels = [lower, weak, higher, replace(weak, code=133, mean=(1000,))]
    choice = empirical_choice(levels, [0], 133, 10, params, short=True)
    assert (choice.code, choice.tier) == (67, 3)
    assert {"SHORT_INTERIM_67", "ASSUMED_SHORT_DEFAULT"} <= set(choice.flags)

    # Crossing V without hitting the raw clip threshold also requires a lower code.
    fails_v = replace(weak, peak=(params.headroom - 10,), envelope=(100,))
    choice = empirical_choice([lower, fails_v, higher], [0], 133, 10, params, short=True)
    assert (choice.code, choice.tier) == (32, 3)
    assert {"DEFAULT_FAILS_V", "HEADROOM_LIMITED"} <= set(choice.flags)
    assert empirical_choice([fails_v, higher], [0], 133, 10, params, short=True).code is None

    # The interim default applies only to tier 3. A valid tier 2 can exceed 67.
    tier2 = replace(higher, mean=(1000,))
    assert empirical_choice([lower, weak, tier2], [0], 133, 10, params, short=True).code == 100


def test_short_interim_override_is_validated_and_measured(tmp_path):
    from noctinirs_host.calibration_sweep import load_configuration

    config = tmp_path / "calibration.json"
    config.write_text('{"parameters":{"short_interim_code":45}}', encoding="utf-8")
    params, plan = load_configuration(config)
    _, visits = plan.visits(SHORT_SLOTS, 0, params.short_interim_code)
    assert sum(all(vector[slot] == 45 for slot in SHORT_SLOTS) for vector in visits) == 2
    assert max(max(vector[slot] for slot in SHORT_SLOTS) for vector in visits) < 133
    assert max(max(vector[slot] for slot in range(20) if slot not in SHORT_SLOTS) for vector in visits) == 222
    with pytest.raises(ValueError):
        replace(params, short_interim_code=133)
    with pytest.raises(ValueError):
        replace(plan, short_codes=(1, 4, 8, 16, 32, 67, 133))


@pytest.mark.parametrize("status", ["BUSY", "INVALID_STATE"])
def test_start_refusal_consumes_sweep_and_requires_calibration(monkeypatch, status):
    run, clock = run_fake()
    try:
        complete_sweep(run, clock)
        sweep_id = run.calibration.profile_id
        device = run.controller._t.simulator
        execute = device._execute

        def refuse(req, op, args):
            if op == p.OP_START:
                return device._reply(req.req_id, op, p.STATUS_NAMES.index(status)), True
            return execute(req, op, args)

        monkeypatch.setattr(device, "_execute", refuse)
        with pytest.raises(ValueError, match="Run calibration again"):
            run.start()
        assert run.calibration is None and not run.snapshot()["calibration_ready"]
        assert device._profile[0] != sweep_id
        with pytest.raises(ValueError, match="fresh calibration"):
            run.start()
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_last_stage2_start_still_verifies_applied_but_reboot_is_needed_for_another():
    run, clock = run_fake()
    try:
        complete_sweep(run, clock)
        run.controller._t.simulator._next_session = 0xffffffff
        run.start()
        for _ in range(20):
            clock.now += .05
            run.tick()
        assert run.controller.session_id == 0xffffffff
        assert run._recording_apply_verified
        assert run.profile_view["report"].start_gate == "START_EXHAUSTED"
        run.stop()
        poll(run, lambda: run.stopped)
        with pytest.raises(ValueError, match="Reboot it"):
            run.calibrate()
        assert run.calibration is None and run.sweep is None
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def sweep_visit(run, slot, code, signal):
    """Feed scheduled stable phases into the real sweep accumulator."""
    codes = [67] * 20
    codes[slot] = code
    profile = run._new_calibration_profile(codes, {"purpose": "synthetic-visit"})
    visit = Visit(profile, count=2, map_id=run.geometry.geometry_map_id)
    samples = []

    def add(phase, index, count):
        samples.append(RawSample(len(samples), (count,) * 4, phase, index))

    add(PH_OUTSIDE, RAW_SLOT_NONE, 2000)
    for occurrence in range(2):
        for index in range(20):
            strength = signal if index == slot else "strong"
            step = {"weak": 2, "strong": 1000, "over-v": 100,
                    "partial": 2, "lost": 2}[strength] + 2 * occurrence
            peak = int(run.calibration_params.headroom - 10) if strength == "over-v" else 2000 + step
            add(PH_ON_STABLE, index, peak)
            if not (strength == "partial" and occurrence == 1):
                add(PH_OFF_STABLE, index, peak - step)
    add(PH_OUTSIDE, RAW_SLOT_NONE, 2000)

    # Simulate a lost sample at a packet boundary while keeping the slot labels.
    if signal == "lost":
        split = len(samples) // 2
        batches = (samples[:split], samples[split + 1:])
    else:
        batches = (samples,)
    for batch in batches:
        visit.feed(RawPacket(1, batch[0].sample_sequence, len(batch), False,
                             run.geometry.geometry_map_id, tuple(batch)), b"synthetic", 0)
    return visit


@pytest.mark.parametrize(("short", "levels", "expected", "tier", "flag"), [
    pytest.param(False, [(67, 1, "weak"), (133, 1, "partial"), (177, 5, "weak"), (222, 5, "weak")],
                 67, 3, "INCOMPLETE_DATA", id="A-partial-default-with-high-candidates"),
    pytest.param(False, [(222, 5, "weak")], None, None, "DEFAULT_UNVERIFIED",
                 id="F-only-high-code-measured"),
    pytest.param(False, [(67, 1, "weak"), (133, 1, "over-v"), (177, 5, "over-v"), (222, 5, "over-v")],
                 67, 3, "DEFAULT_FAILS_V", id="C-monotonic-headroom-limit"),
    pytest.param(True, [(32, 1, "weak"), (67, 1, "over-v")], 32, 3, "SHORT_INTERIM_67",
                 id="E-short-interim-steps-down"),
    pytest.param(False, [(133, 1, "weak"), (222, 5, "strong")], 222, 2, "HIGH_CURRENT_PROFILE",
                 id="G-high-code-with-tier2-evidence"),
    pytest.param(False, [(133, 1, "weak"), (222, 5, "weak")], 133, 3, "DEFAULT_TIER",
                 id="H-safe-default"),
    pytest.param(False, [(67, 1, "weak"), (133, 1, "lost")], 67, 3, "INCOMPLETE_DATA",
                 id="packet-loss-and-low-snr-without-high-candidates"),
])
def test_sweep_finish_selects_and_arms_only_valid_profiles(short, levels, expected, tier, flag):
    run, _ = run_fake()
    try:
        run.calibration_params = replace(run.calibration_params, min_occurrences=2)
        run.calibrate()
        sweep = run.sweep
        slot = next(slot for slot in range(20) if (slot in sweep.short_slots) == short)
        sweep.visits = [sweep_visit(run, slot, code, signal)
                        for code, repeats, signal in levels for _ in range(repeats)]
        sweep.order = [visit.profile.codes for visit in sweep.visits]
        # The normal worker path runs Sweep.finish and publishes readiness after profile readback.
        run.tick()
        assert run.sweep is None
        if expected is None:
            assert run.calibration is None and not run.snapshot()["calibration_ready"]
            assert flag in run.error and "DEFAULT_FAILS_V" not in run.error
            assert not run.controller.query_profile().reply.profile.armed
            with pytest.raises(ValueError, match="fresh calibration"):
                run.start()
            return
        assert run.snapshot()["calibration_ready"], run.error
        assert run.controller.profile.profile_code[slot] == expected
        slot_result = run.calibration.document["slots"][slot]
        choice = slot_result["selection"]
        assert (choice["code"], choice["tier"]) == (expected, tier)
        assert flag in choice["flags"]
        if flag == "INCOMPLETE_DATA":
            assert "DEFAULT_FAILS_V" not in choice["flags"]
        if short:
            assert {"DEFAULT_FAILS_V", "ASSUMED_SHORT_DEFAULT"} <= set(choice["flags"])
            assert run.calibration.document["short_interim"]["status"] == "assumed_pending_caliper"
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)


def test_start_exhaustion_reports_reboot_first(monkeypatch):
    run, clock = run_fake()
    try:
        complete_sweep(run, clock)
        device = run.controller._t.simulator
        execute = device._execute

        def exhaust_at_start(req, op, args):
            if op == p.OP_START:
                device._next_session = 0x100000000
            return execute(req, op, args)

        monkeypatch.setattr(device, "_execute", exhaust_at_start)
        with pytest.raises(StartExhausted, match=r"^The device has no Start sessions left\. Reboot"):
            run.start()
        assert run.calibration is None and not run.snapshot()["calibration_ready"]
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)
