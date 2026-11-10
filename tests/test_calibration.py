from dataclasses import replace
from hashlib import sha256
import json
from math import sqrt

import pytest

from noctinirs_host.calibration import Assumptions, DetectorModel, Level, empirical_choice, model_choice, x_min
from noctinirs_host.calibration_sweep import SweepPlan
from noctinirs_host.control.session import SessionController
from noctinirs_host.engineering import EngineeringRun
from noctinirs_host.geometry import load_geometry
from test_engineering_run import GEOMETRY, poll
from test_udp_session import Clock


def test_rev22_section_8():
    params = Assumptions()
    assert x_min(86, 30, .005) == pytest.approx(2857.684, abs=.001)
    for k, sigma, expected_snr in [(70, 30, 100.70), (1730 / 30, sqrt(144**2 - 8.65**2), 19.94)]:
        detector = DetectorModel(.225 * k, 200000, 0, sigma, .005)
        choice = model_choice([detector], [0], float, 1, 222, 10, params)
        assert choice.code == 222 and "LOW_SIGNAL" in choice.flags
        assert detector.snr(detector.k * choice.code) == pytest.approx(expected_snr, abs=.01)
    detectors = [DetectorModel(.225 * k, 200000, 0, 100, .005) for k in (29700, 273000)]
    for up, down, delta, code in [(1, 0, 0, 10), (1, 0, .05, 9), (.2, .2, .05, 13), (1, .5, .05, 13)]:
        p = replace(params, upward_drift=up, downward_drift=down, prediction_error=delta)
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


def test_fake_calibration_is_consumed_and_archived(tmp_path):
    run, clock = run_fake()
    try:
        with pytest.raises(ValueError, match="fresh calibration"):
            run.start()
        complete_sweep(run, clock)
        profile = run.calibration
        run.start_recorded(str(tmp_path))
        poll(run, lambda: run.recording_used)
        for _ in range(30):
            clock.now += .05
            run.tick()
        assert run._recording_apply_verified, run.error
        run.stop()
        poll(run, lambda: run.recorder_state == "FINALIZED")
        with pytest.raises(ValueError, match="fresh calibration"):
            run.start()
        sidecar = json.loads((tmp_path / "record_1.json").read_text(encoding="utf8"))
        assert sidecar["raw"]["context_at_start"]["calibration"]["profile_id"] == profile.profile_id
        assert sha256((tmp_path / "record_1.iref-v1.json").read_bytes()).hexdigest() == profile.profile_hash
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
