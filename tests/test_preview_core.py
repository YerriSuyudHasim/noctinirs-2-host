"""Stage D2 core PREVIEW tests. Simulation / host unit evidence only; nothing here is a device test.

Comparators are written independently of the code under test (different algorithm, decadic
log10 forward model, plain Gauss elimination).
"""

import math
import random
from dataclasses import replace
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
from noctinirs_host.preview import quality as Q
from noctinirs_host.preview.pipeline import (BASELINE, INVALID, LEAK_WARN, LOW_SIGNAL, SSR_WARMUP, VALID,
                                              PreviewPipeline)
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
    assert "PREVIEW" in meta["label"] and meta["quality"]["status"].startswith("host_chosen")


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

def run_pipeline(config=None, cycles=40, fs=10, mutate=None, **fake):
    """Feed fake RAW cycles. `mutate(cycle, slot, phase, ain)` may return changed AIN counts."""
    f = EngineeringFake(geometry=GEOMETRY, **fake)
    f.fs_hz = fs
    pl = PreviewPipeline(GEOMETRY, config or PreviewConfig(baseline_s=1.0), fs)
    frames = []
    for c in range(cycles):
        if c in fake.get("drop_cycles", ()):
            continue
        for wire in f._cycle_packets(c, c / fs):
            env = parse_envelope(wire)
            raw = parse_raw(env.payload)
            if mutate is not None:
                raw = replace(raw, samples=tuple(
                    replace(x, ain=tuple(mutate(c, x.slot_index, x.phase_class, list(x.ain)) or x.ain))
                    for x in raw.samples))
            frames += pl.feed(raw, int(c * 1e6 / fs), env.msg_sequence)
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


def test_dead_slot_is_invalid_or_flagged_low_signal_never_plain_valid_or_zero():
    _, frames = run_pipeline(dead_slots=frozenset({4, 5}), cycles=60)
    seen_invalid = seen_low = 0
    for fr in frames:
        p = fr.points["CH3"]
        assert p.d_hbo != 0.0 or math.isnan(p.d_hbo)
        if p.state == INVALID:
            seen_invalid += 1
            assert all(math.isnan(v) for v in (p.d_hbo, p.d_hbr, p.od735, p.od850))
        elif p.state == VALID:
            assert "LOW_SIGNAL" in p.warnings        # a value with no usable signal is always flagged
            seen_low += 1
    assert seen_invalid and seen_low


def test_saturation_near_rail_and_flat_top_are_invalid():
    rail = Q.ADC_RAIL
    on = lambda ph: ph in (3, 4)  # noqa: E731

    def near_rail(c, slot, phase, ain):      # slot 6 AIN 3 just under the rail, never exactly at it
        if slot in (6, 7) and on(phase):
            ain[3] = int(0.98 * rail)
        return ain

    def flat_top(c, slot, phase, ain):       # slot 8 AIN 3 identical samples at a high level
        if slot in (8, 9) and on(phase):
            ain[3] = int(0.5 * rail)
        return ain

    _, frames = run_pipeline(mutate=near_rail, cycles=30)
    assert frames[-2].points["CH5"].state == INVALID and "SATURATED" in frames[-2].points["CH5"].reason
    _, frames = run_pipeline(mutate=flat_top, cycles=30)
    assert frames[-2].points["CH7"].state == INVALID and "SATURATED" in frames[-2].points["CH7"].reason
    assert frames[-2].points["CH1"].state == VALID


def test_exact_rail_samples_are_saturated():
    _, frames = run_pipeline(saturate=frozenset({(6, 3)}), cycles=30)
    assert frames[-2].points["CH5"].state == INVALID and "SATURATED" in frames[-2].points["CH5"].reason


def test_leak_is_a_warning_per_ain_and_invalid_only_when_large():
    _, small = run_pipeline(leak={0: 500.0}, cycles=60)        # 0.8 % of the same-side step
    last = small[-2].points
    assert last["SC1L"].state == VALID and LEAK_WARN in last["SC1L"].warnings
    assert last["CH1"].state == VALID and LEAK_WARN not in last["CH1"].warnings
    _, large = run_pipeline(leak={0: 20000.0}, cycles=60)     # above 10 % of the same-side step
    last = large[-2].points
    assert last["SC1L"].state == INVALID and "LEAK_SUSPECT" in last["SC1L"].reason
    assert last["CH1"].state == VALID                         # another AIN is not dropped for it


def test_leak_never_invalidates_before_sigma_is_known():
    _, frames = run_pipeline(leak={0: 20000.0}, cycles=4)
    assert all("LEAK_SUSPECT" not in fr.points["SC1L"].reason for fr in frames)


def test_sigma_is_estimated_online_and_saved():
    pl, frames = run_pipeline(cycles=40)
    prov = pl.provenance()
    sig = prov["noise_sigma_counts"]
    assert sig and all(0 < v < 30 for v in sig.values())
    assert prov["quality_constants"]["LOW_SIGNAL_SIGMA"] == Q.LOW_SIGNAL_SIGMA
    p = frames[-2].points["CH1"]
    assert p.sigma735 > 0 and p.snr735 > 100


def test_low_signal_is_a_warning_and_the_value_is_still_shown():
    def weak(c, slot, phase, ain):           # CH1 = source L2 (slots 2,3), AIN 3: step of about 3 counts
        if slot in (2, 3):
            noise = (c * 37 + phase * 11 + slot) % 5 - 2
            ain[3] = 1800 + 90 * 3 + noise + (3 if phase in (3, 4) else 0)
        return ain

    _, frames = run_pipeline(mutate=weak, cycles=60)
    shown = [fr.points["CH1"] for fr in frames if fr.points["CH1"].state == VALID]
    assert shown and all(LOW_SIGNAL in p.warnings and math.isfinite(p.d_hbo) for p in shown)


def test_ambient_jump_is_invalid_and_a_sustained_shift_is_accepted_later():
    def event(c, slot, phase, ain):          # a bright-room event on every AIN for 6 cycles
        return [v + 500_000 for v in ain] if 25 <= c < 31 and phase in (3, 4, 6, 7) else None

    _, frames = run_pipeline(mutate=event, cycles=50)
    assert all("AMBIENT_HIGH" in frames[c].points["CH1"].reason for c in range(26, 30))
    assert frames[40].points["CH1"].state == VALID           # normal again after the event

    def shift(c, slot, phase, ain):          # the whole level moves and stays there
        return [v + 500_000 for v in ain] if c >= 25 and phase in (3, 4, 6, 7) else None

    _, frames = run_pipeline(mutate=shift, cycles=80)
    assert "AMBIENT_HIGH" in frames[27].points["CH1"].reason
    assert frames[75].points["CH1"].state == VALID           # accepted after AMBIENT_ACCEPT_AFTER cycles


def test_invalid_counts_are_taken_after_ssr_and_mbll():
    singular = ExtinctionTable({"735": {"HbO": 1.0, "HbR": 2.0}, "850": {"HbO": 1.0, "HbR": 2.0}}, "x")
    pl, frames = run_pipeline(PreviewConfig(baseline_s=0.5, extinction=singular), cycles=30)
    after = [fr for fr in frames if fr.points["CH1"].reason.startswith("MBLL")]
    assert after and pl.invalid_counts["CH1"] >= len(after)
    assert pl.provenance()["invalid_reason_counts"]["CH1"]["MBLL"] >= len(after)


def test_loss_counters_count_missing_samples_and_messages():
    pl, _ = run_pipeline(drop_cycles=frozenset({10, 11}), cycles=30)
    loss = pl.loss_stats()
    assert loss["samples_missing"] == 400 and loss["sample_jumps"] == 1
    assert loss["gap_before_packets"] == 0
    assert loss["samples_missing_percent"] == pytest.approx(100 * 400 / (28 * 200 + 400))
    clean, _ = run_pipeline(cycles=10)
    assert clean.loss_stats()["samples_missing"] == 0 and clean.loss_stats()["msg_missing"] == 0


def test_message_sequence_gaps_are_counted():
    pl = PreviewPipeline(GEOMETRY, PreviewConfig(baseline_s=1.0), 10)
    f = EngineeringFake(geometry=GEOMETRY)
    f.fs_hz = 10
    packets = [parse_raw(parse_envelope(w).payload) for w in f._cycle_packets(0, 0.0)]
    pl.feed(packets[0], 0, 5)
    pl.feed(packets[1], 1, 9)                 # messages 6, 7, 8 never arrived
    assert pl.loss_stats()["msg_missing"] == 3
    pl.feed(packets[1], 2, 0xFFFFFFFF)        # a wrap or reorder is not counted as a huge loss
    assert pl.loss_stats()["msg_missing"] == 3


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


def test_short_separation_shows_uncorrected_while_warming_then_applied():
    cfg = PreviewConfig(baseline_s=0.5, ssr_mode="8mm", ssr_min_samples=30)
    _, frames = run_pipeline(cfg, cycles=80)
    pts = [fr.points["CH1"] for fr in frames if fr.points["CH1"].state == VALID]
    assert pts[0].ssr_state == "UNCORRECTED" and math.isfinite(pts[0].d_hbo)   # shown, not hidden
    assert any("warming up" in p.ssr_reason for p in pts)
    assert pts[-1].ssr_state == "applied" and pts[-1].ssr_applied and pts[-1].ssr_beta
    assert all(p.state != SSR_WARMUP for p in pts)


def test_invalid_or_weak_short_channel_gives_uncorrected_long_channel_not_a_gap():
    cfg = PreviewConfig(baseline_s=0.5, ssr_mode="5mm", ssr_min_samples=10)
    pl, frames = run_pipeline(cfg, cycles=40, dead_slots=frozenset({0, 1}))   # SC1L / SC2L dead
    left = frames[-2].points["CH1"]
    assert left.state == VALID and left.ssr_state == "UNCORRECTED" and "SC1L" in left.ssr_reason
    assert math.isfinite(left.d_hbo)
    assert frames[-2].points["CH9"].ssr_state == "applied"    # right hemisphere uses SC1R
    assert pl.uncorrected_counts["CH1"] > 0 and pl.invalid_counts.get("CH1", 0) < 5


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
