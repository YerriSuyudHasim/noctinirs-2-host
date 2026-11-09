"""Stage D2 core PREVIEW tests. Simulation / host unit evidence only; nothing here is a device test.

Comparators are written independently of the code under test (different algorithm, decadic
log10 forward model, plain Gauss elimination).
"""

import math
import random
from datetime import date
from pathlib import Path

import pytest

from noctinirs_host.control.engineering_fake import EngineeringFake
from noctinirs_host.engineering import (RecordingTarget, default_recording_folder,
                                        next_record_number)
from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.baseline import MedianBaseline
from noctinirs_host.preview.dpf import scholkmann_wolf_dpf
from noctinirs_host.preview.mbll import LN10, mbll_delta
from noctinirs_host.preview.od import optical_density
from noctinirs_host.preview.params import (LEGACY_PREVIEW_EXTINCTION, DpfSetting, ExtinctionTable,
                                           PreviewConfig, resolve_dpf)
from noctinirs_host.preview.pipeline import BASELINE, INVALID, SSR_WARMUP, VALID, PreviewPipeline
from noctinirs_host.preview.shortsep import RollingRegressor, fit_beta
from noctinirs_host.wire.envelope import parse_envelope
from noctinirs_host.wire.optical import parse_raw

GEOMETRY = load_geometry(Path(__file__).resolve().parents[1] / "config/optical_geometry_v1.json")


def dpf_of(a=6.0, b=6.0):
    return resolve_dpf(DpfSetting(manual={"735": a, "850": b}))


def gauss(matrix, vec):
    """Independent 2x2 solver: Gauss elimination with partial pivoting (not Cramer)."""
    m = [row[:] + [v] for row, v in zip(matrix, vec)]
    if abs(m[1][0]) > abs(m[0][0]):
        m[0], m[1] = m[1], m[0]
    f = m[1][0] / m[0][0]
    m[1] = [m[1][k] - f * m[0][k] for k in range(3)]
    x1 = m[1][2] / m[1][1]
    return [(m[0][2] - m[0][1] * x1) / m[0][0], x1]


def comparator(od735_ln, od850_ln, d_mm, dpf, eps):
    """Decadic OD (log10) and decadic epsilon, concentration in uM, no ln10 on epsilon."""
    od = [od735_ln / math.log(10), od850_ln / math.log(10)]
    path = [d_mm / 10 * dpf[0], d_mm / 10 * dpf[1]]
    a = [[eps[0][0] * path[0], eps[0][1] * path[0]], [eps[1][0] * path[1], eps[1][1] * path[1]]]
    hbo, hbr = gauss(a, od)
    return hbo * 1e6, hbr * 1e6


# ---- MBLL -------------------------------------------------------------------------------

def test_mbll_matches_independent_comparator_on_1000_random_cases():
    rng = random.Random(20261109)
    worst = 0.0
    for _ in range(1000):
        # Around the legacy table (+-30 %), so the 2x2 system stays well conditioned.
        eps = [[420 * rng.uniform(.7, 1.3), 1510 * rng.uniform(.7, 1.3)],
               [1060 * rng.uniform(.7, 1.3), 800 * rng.uniform(.7, 1.3)]]
        table = ExtinctionTable({"735": {"HbO": eps[0][0], "HbR": eps[0][1]},
                                 "850": {"HbO": eps[1][0], "HbR": eps[1][1]}}, "test")
        d, p1, p2 = rng.uniform(5, 40), rng.uniform(3, 9), rng.uniform(3, 9)
        y1, y2 = rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05)
        got = mbll_delta(y1, y2, d, dpf_of(p1, p2), table)
        ref = comparator(y1, y2, d, (p1, p2), eps)
        worst = max(worst, abs(got.d_hbo_uM - ref[0]), abs(got.d_hbr_uM - ref[1]))
    assert worst < 1e-12


def test_known_delta_hb_is_recovered_through_intensity_chain():
    ext = LEGACY_PREVIEW_EXTINCTION.eps
    dpf = dpf_of(6.0, 6.0)
    for d_mm in (5.0, 18.5, 30.0):
        for d_hbo, d_hbr in ((3.0, -1.0), (-2.0, 0.5), (0.0, 4.0)):
            od = {}
            for wl in ("735", "850"):  # forward model in decadic units, then to ln
                dec = (ext[wl]["HbO"] * d_hbo + ext[wl]["HbR"] * d_hbr) * 1e-6 * d_mm / 10 * dpf.values[wl]
                od[wl] = dec * math.log(10)
            ref = 10000.0
            i735, i850 = ref * math.exp(-od["735"]), ref * math.exp(-od["850"])
            got = mbll_delta(optical_density(i735, ref), optical_density(i850, ref), d_mm, dpf,
                             LEGACY_PREVIEW_EXTINCTION)
            assert got.d_hbo_uM == pytest.approx(d_hbo, abs=1e-9)
            assert got.d_hbr_uM == pytest.approx(d_hbr, abs=1e-9)
            assert got.d_hbt_uM == pytest.approx(d_hbo + d_hbr, abs=1e-9)


def test_ln_versus_log10_od_differ_by_exactly_ln10():
    dpf = dpf_of()
    ln_result = mbll_delta(0.02, -0.01, 30.0, dpf, LEGACY_PREVIEW_EXTINCTION)
    wrong = mbll_delta(0.02 / LN10, -0.01 / LN10, 30.0, dpf, LEGACY_PREVIEW_EXTINCTION)
    assert wrong.d_hbo_uM == pytest.approx(ln_result.d_hbo_uM / LN10, rel=1e-12)
    assert optical_density(1.0, math.e) == pytest.approx(1.0)  # natural log, not log10


@pytest.mark.parametrize("bad", [None, "", "0.1", math.nan, math.inf, -math.inf, True])
def test_mbll_rejects_invalid_od(bad):
    with pytest.raises(ValueError):
        mbll_delta(bad, 0.0, 30.0, dpf_of(), LEGACY_PREVIEW_EXTINCTION)
    with pytest.raises(ValueError):
        mbll_delta(0.0, bad, 30.0, dpf_of(), LEGACY_PREVIEW_EXTINCTION)


@pytest.mark.parametrize("bad", [0, -1.0, None, "", math.nan, math.inf, True])
def test_od_and_distance_reject_invalid_values(bad):
    with pytest.raises(ValueError):
        optical_density(bad, 100.0)
    with pytest.raises(ValueError):
        optical_density(100.0, bad)
    with pytest.raises(ValueError):
        mbll_delta(0.0, 0.0, bad, dpf_of(), LEGACY_PREVIEW_EXTINCTION)


def test_parameters_are_validated_and_carry_status():
    with pytest.raises(ValueError):
        dpf_of(0.0, 6.0)
    with pytest.raises(ValueError):
        ExtinctionTable({"735": {"HbO": 0.0, "HbR": 1.0}, "850": {"HbO": 1.0, "HbR": 1.0}}, "x")
    with pytest.raises(ValueError):
        ExtinctionTable({"735": {"HbO": 1.0}}, "x")
    singular = ExtinctionTable({"735": {"HbO": 1.0, "HbR": 2.0}, "850": {"HbO": 1.0, "HbR": 2.0}}, "x")
    with pytest.raises(ValueError):
        mbll_delta(0.0, 0.0, 30.0, dpf_of(), singular)
    meta = PreviewConfig().to_metadata()
    assert meta["extinction"]["status"] == "unverified" and meta["dpf"]["status"] == "unverified"
    assert meta["extinction"]["source"] and meta["dpf"]["source"]
    assert "PREVIEW" in meta["label"] and meta["evidence_thresholds"]["status"] == "provisional_unmeasured"


# ---- DPF --------------------------------------------------------------------------------

def test_scholkmann_wolf_age_28_and_range_warnings():
    v735, w735 = scholkmann_wolf_dpf(735.0, 28.0)
    v850, w850 = scholkmann_wolf_dpf(850.0, 28.0)
    assert v735 == pytest.approx(6.255, abs=5e-4) and v850 == pytest.approx(5.177, abs=5e-4)
    assert not w735 and any("outside the published fit range" in w for w in w850)
    assert any("0-70" in w for w in scholkmann_wolf_dpf(735.0, 80.0)[1])
    for bad in (-1.0, math.nan, None):
        with pytest.raises(ValueError):
            scholkmann_wolf_dpf(735.0, bad)
    cfg = resolve_dpf(DpfSetting(mode="age", age_years=28.0))
    assert cfg.warnings and cfg.values["735"] == pytest.approx(6.255, abs=5e-4)
    with pytest.raises(ValueError):
        resolve_dpf(DpfSetting(mode="age"))


# ---- short separation --------------------------------------------------------------------

def _series(n, rng, betas, noise=0.0):
    rows, truth = [], []
    for i in range(n):
        x = tuple(math.sin(0.11 * i + 0.9 * k) + 0.3 * math.sin(0.37 * i * (k + 1)) for k in range(len(betas)))
        cerebral = 0.4 * math.sin(0.23 * i + 0.4)
        y = cerebral + sum(b * v for b, v in zip(betas, x)) + rng.gauss(0, noise)
        rows.append((y, x))
        truth.append(cerebral)
    return rows, truth


def test_rolling_regression_recovers_signal_and_matches_batch_comparator():
    rng = random.Random(7)
    for betas, method in (((1.7,), "ols"), ((1.2, -0.6), "ols"), ((1.2, -0.6), "ridge")):
        rows, truth = _series(300, rng, betas)
        reg = RollingRegressor(len(betas), 300, 30, method, 1e-3)
        out = [reg.push(y, x) for y, x in rows]
        assert all(o is None for o in out[:29]) and out[29] is not None
        beta, xm = fit_beta(rows, method, 1e-3)
        expected = rows[-1][0] - sum(b * (x - m) for b, x, m in zip(beta, rows[-1][1], xm))
        assert out[-1] == pytest.approx(expected, rel=1e-9, abs=1e-12)
        if method == "ols":  # cerebral part is orthogonal-ish to the short signal
            late = [o - t for o, t in zip(out[150:], truth[150:])]
            assert max(abs(v) for v in late) < 0.2
    # explicit 1-regressor closed form
    rows, _ = _series(120, rng, (2.0,))
    beta, _ = fit_beta(rows, "ols", 0.0)
    xs, ys = [r[1][0] for r in rows], [r[0] for r in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    closed = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sum((a - mx) ** 2 for a in xs)
    assert beta[0] == pytest.approx(closed, rel=1e-12)


def test_rolling_window_drops_old_rows():
    rng = random.Random(3)
    rows, _ = _series(500, rng, (1.5,), noise=0.01)
    reg = RollingRegressor(1, 100, 30, "ols", 0.0)
    for y, x in rows:
        out = reg.push(y, x)
    beta, xm = fit_beta(rows[-100:], "ols", 0.0)
    assert reg.last_beta[0] == pytest.approx(beta[0], rel=1e-8)
    assert out == pytest.approx(rows[-1][0] - beta[0] * (rows[-1][1][0] - xm[0]), rel=1e-8, abs=1e-12)


def test_regression_without_variance_is_not_ready():
    reg = RollingRegressor(1, 50, 5, "ols", 0.0)
    assert all(reg.push(0.1 * i, (1.0,)) is None for i in range(20))


# ---- baseline ----------------------------------------------------------------------------

def test_median_baseline_waits_for_duration_ignores_invalid_and_resets():
    b = MedianBaseline(2.0, 3)
    for t, v in ((0.0, 10.0), (0.5, math.nan), (1.0, 0.0), (1.5, -4.0), (1.9, 30.0), (2.0, 20.0)):
        b.push(t, v)
    assert b.value == 20.0 and b.collected == 3
    b.push(3.0, 999.0)
    assert b.value == 20.0
    b.reset()
    assert not b.ready


# ---- RAW -> preview chain ---------------------------------------------------------------

def run_pipeline(config=None, cycles=40, fs=10, **fake):
    f = EngineeringFake(geometry=GEOMETRY, **fake)
    f.fs_hz = fs
    pl = PreviewPipeline(GEOMETRY, config or PreviewConfig(baseline_s=1.0), fs)
    frames = []
    for c in range(cycles):
        if c in fake.get("drop_cycles", ()):
            continue
        for wire in f._cycle_packets(c, c / fs):
            frames += pl.feed(parse_raw(parse_envelope(wire).payload), int(c * 1e6 / fs))
    frames += pl.flush()
    return pl, frames


def test_all_channels_become_valid_and_physiology_is_finite():
    pl, frames = run_pipeline()
    last = frames[-2].points   # the final frame is cut by flush(): its open slot is partial
    assert len(last) == 20 and {p.state for p in last.values()} == {VALID}
    assert frames[-1].points["CH15"].state == INVALID
    assert all(math.isfinite(p.d_hbo) and math.isfinite(p.od735) for p in last.values())
    assert frames[0].points["CH1"].state in (BASELINE, INVALID)
    assert pl.provenance()["geometry"]["draft"] is True  # DRAFT geometry is reported, not hidden
    assert any("not device-verified" in s or "does not verify" in s for s in pl.provenance()["limits"])


def test_dead_saturated_and_leaking_channels_are_invalid_gaps_never_zero():
    _, frames = run_pipeline(dead_slots=frozenset({4, 5}), saturate=frozenset({(6, 3)}),
                             leak={0: 500.0})
    last = frames[-1].points
    assert last["CH3"].state == INVALID and "NO_LED_STEP" in last["CH3"].reason
    assert last["CH5"].state == INVALID and "SATURATED" in last["CH5"].reason
    # slot 0/1 (source L1) leaks to the opposite side detectors -> SC1L/SC2L excluded
    assert last["SC1L"].state == INVALID and "LEAK_SUSPECT" in last["SC1L"].reason
    assert last["CH1"].state == VALID
    for name in ("CH3", "CH5", "SC1L"):
        for fr in frames:
            p = fr.points[name]
            if p.state == INVALID:
                assert all(math.isnan(v) for v in (p.d_hbo, p.d_hbr, p.d_hbt, p.od735, p.od850))


def test_missing_second_wavelength_makes_channel_invalid():
    pl = PreviewPipeline(GEOMETRY, PreviewConfig(baseline_s=0.2), 10)
    f = EngineeringFake(geometry=GEOMETRY)
    f.fs_hz = 10
    frames = []
    for c in range(8):
        for wire in f._cycle_packets(c, c / 10):
            raw = parse_raw(parse_envelope(wire).payload)
            kept = tuple(s for s in raw.samples if s.slot_index != 3)   # slot 3 = L2 / 850 nm lost
            if kept:
                raw = type(raw)(raw.slot_plan_epoch, raw.first_sample_sequence, len(kept),
                                raw.gap_before, raw.geometry_map_id, kept)
            frames += pl.feed(raw, int(c * 1e5))
    frames += pl.flush()
    assert any(fr.points["CH1"].state == INVALID for fr in frames)
    assert all(fr.points["CH1"].state == INVALID for fr in frames[2:])
    assert all(math.isnan(fr.points["CH1"].d_hbo) for fr in frames)


def test_dropped_packets_and_link_loss_leave_gaps():
    pl, frames = run_pipeline(drop_cycles=frozenset({20}), cycles=40)
    assert any(fr.points["CH16"].state == INVALID and "GAP" in fr.points["CH16"].reason for fr in frames)
    assert any(fr.points["SC1L"].state == INVALID and "GAP" in fr.points["SC1L"].reason for fr in frames)
    ts = [fr.t_s for fr in frames]
    assert max(b - a for a, b in zip(ts, ts[1:])) > 0.15   # the missing cycle is a hole on the time axis
    marker = pl.note_gap("link lost")
    assert marker is not None and marker.gap_marker == "link lost"
    assert all(p.state == INVALID and math.isnan(p.d_hbo) for p in marker.points.values())
    assert marker.t_s > ts[-1]


def test_time_axis_uses_host_receive_time():
    _, frames = run_pipeline(cycles=12)
    ts = [fr.t_s for fr in frames]
    assert ts[0] == 0.0 and ts[1] == pytest.approx(0.1)
    assert ts[-1] == pytest.approx(1.1, abs=0.11)


def test_short_separation_is_explicit_about_warmup_and_state():
    cfg = PreviewConfig(baseline_s=0.5, ssr_mode="8mm", ssr_min_samples=30)
    _, frames = run_pipeline(cfg, cycles=80)
    states = [fr.points["CH1"].state for fr in frames]
    assert SSR_WARMUP in states and states[-1] == VALID
    assert states.index(SSR_WARMUP) < len(states) - states[::-1].index(SSR_WARMUP)
    last = frames[-1].points["CH1"]
    assert last.ssr_applied and last.ssr_beta
    warm = next(fr.points["CH1"] for fr in frames if fr.points["CH1"].state == SSR_WARMUP)
    assert math.isnan(warm.d_hbo)  # no silent raw -> corrected switch: warm-up shows no value


def test_ssr_requires_valid_short_channel():
    cfg = PreviewConfig(baseline_s=0.5, ssr_mode="5mm", ssr_min_samples=10)
    _, frames = run_pipeline(cfg, cycles=30, dead_slots=frozenset({0, 1}))   # SC1L / SC2L dead
    left = frames[-1].points["CH1"]
    assert left.state == INVALID and left.reason == "NO_SHORT_REGRESSOR"
    assert frames[-1].points["CH9"].state == VALID  # right hemisphere uses SC1R


def test_live_config_change_resets_what_it_touches_and_is_logged():
    pl, frames = run_pipeline(cycles=30)
    assert pl.baseline_resets == [{"t_s": 0.0, "reason": "initial"}]
    pl.configure(PreviewConfig(baseline_s=1.0, dpf=DpfSetting(mode="age", age_years=28.0)))
    assert len(pl.baseline_resets) == 1                 # DPF change alone keeps the baseline
    assert pl.dpf.values["735"] == pytest.approx(6.255, abs=5e-4)
    pl.configure(PreviewConfig(baseline_s=4.0))
    assert len(pl.baseline_resets) == 2
    pl.reset_baseline()
    assert pl.baseline_resets[-1]["reason"] == "operator reset"
    assert len(pl.config_log) == 3 and pl.config_log[1]["config"]["dpf"]["mode"] == "age"
    with pytest.raises(ValueError):
        pl.configure(PreviewConfig(ssr_mode="bogus"))


def test_ui_package_holds_no_mbll_constants():
    ui = Path(__file__).resolve().parents[1] / "src/noctinirs_host_ui"
    text = "\n".join(p.read_text(encoding="utf-8") for p in ui.glob("*.py")
                     if p.name not in ("app.py", "control_panel.py", "live_view.py", "theme.py",
                                       "synthetic.py"))
    for forbidden in ("1510", "1060", "223.3", "0.8493", "5.723e-7", "0.05624", "LN10", "math.log",
                      "np.log", "scholkmann_wolf"):
        assert forbidden not in text, forbidden


# ---- recording folder and naming ---------------------------------------------------------

def test_default_folder_and_record_numbers(tmp_path):
    assert default_recording_folder(tmp_path, date(2026, 11, 9)) == tmp_path / "noctinirs" / "2026-11-09"
    day = tmp_path / "d"
    day.mkdir()
    assert next_record_number(day) == 1
    for name in ("record_1.json", "record_7.raw.bin", "record_3.preview.json", "record_x.json",
                 "other_9.json", "record_12.txt"):
        (day / name).write_text("x")
    assert next_record_number(day) == 8


def test_prepare_creates_folder_and_never_reuses_a_stem(tmp_path):
    folder = tmp_path / "new" / "day"
    first = RecordingTarget.prepare(str(folder))
    assert folder.is_dir() and first.name == "record_1"
    (folder / "record_1.json").write_text("{}")
    assert RecordingTarget.prepare(str(folder)).name == "record_2"
    for bad in ("relative/dir", "", " " + str(folder)):
        with pytest.raises(ValueError):
            RecordingTarget.prepare(bad)
