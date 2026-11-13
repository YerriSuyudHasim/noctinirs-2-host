"""Tahap 2.1 comparison of two replay output folders (baseline vs candidate). Reads .npz/.summary.json/sweep JSON only.

Usage: python tools/analyze_r3t21.py --base DIR --final DIR [--out FILE.md]
Each folder holds rN_ssroff.npz / rN_ssr5mm.npz / *.summary.json from tools/replay_quality_r3t2.py and
sweep_rN.json from tools/replay_sweep_r3t2.py (records 4-6). Nothing is written next to the inputs.
Event windows are the ones the Owner named on 13 Nov 2026; they are inputs, not measurements.
"""
import argparse
import json
from pathlib import Path

import numpy as np

EVENTS = {4: [(70, 72), (108, 114)], 5: [(33, 39), (124, 131)], 6: [(130, 147)], 3: []}
MARGIN = 3.0
CARDIAC_BAND = (1.0, 2.0)
LEFT_LONG = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8"]   # filled from the geometry below when available


def load(folder, n, tag):
    d = np.load(Path(folder) / f"r{n}_ssr{tag}.npz", allow_pickle=True)
    cols = list(d["cols"])
    return d["t"], [str(c) for c in d["channels"]], d["data"], {c: i for i, c in enumerate(cols)}


def column(data, ci, j, name):
    return np.array([r[j][ci[name]] for r in data], dtype=object)


def finite(a):
    a = np.array([x for x in a if x is not None], dtype=float)
    return a[np.isfinite(a)]


def valid_percent(folder, n, name, after_s=10.0):
    t, names, data, ci = load(folder, n, "off")
    j = names.index(name)
    ok = column(data, ci, j, "valid").astype(bool)
    m = t >= after_s
    return 100.0 * ok[m].mean()


def channel_summary(folder, n, name):
    t, names, data, ci = load(folder, n, "off")
    j = names.index(name)
    ok = column(data, ci, j, "valid").astype(bool)
    i1, i2, s1, s2 = (column(data, ci, j, c).astype(float) for c in ("i735", "i850", "s735", "s850"))
    with np.errstate(all="ignore"):
        snr = np.minimum(i1 / s1, i2 / s2)
    hbo, hbr = (column(data, ci, j, c).astype(float) for c in ("hbo", "hbr"))
    m = ok & np.isfinite(hbo) & np.isfinite(hbr)
    card = finite(column(data, ci, j, "card_db")[ok])
    out = dict(valid_pct=100.0 * ok[t >= 10].mean(), snr_median=float(np.nanmedian(snr[ok])) if ok.any() else None,
               hbo_p1_p50_p99=[float(np.percentile(hbo[m], p)) for p in (1, 50, 99)] if m.any() else None,
               hbo_hbr_corr=float(np.corrcoef(hbo[m], hbr[m])[0, 1]) if m.sum() > 50 else None,
               cardiac_db_median=float(np.median(card)) if card.size else None)
    warns = column(data, ci, j, "warn")[ok]
    out["achromatic_pct"] = 100.0 * np.mean(["ACHROMATIC_SUSPECT" in str(w) for w in warns]) if warns.size else None
    return out


def segments_of(folder, n, tag="off"):
    s = json.loads((Path(folder) / f"r{n}_ssr{tag}.summary.json").read_text())
    return s["motion"]["segments"], s


def motion_row(folder, n):
    segs, summ = segments_of(folder, n)
    hits = [any(s[1] <= b + 1.0 and s[2] >= a - 1.0 for s in segs) for a, b in EVENTS[n]]
    false = [s for s in segs if not any(s[1] <= b + MARGIN and s[2] >= a - MARGIN for a, b in EVENTS[n])]
    return dict(segments=len(segs), events=f"{sum(hits)}/{len(hits)}", unexplained=len(false),
                unexplained_s=round(sum(s[2] - s[1] + 0.1 for s in false), 1),
                list=[(s[0], round(s[1], 1), round(s[2], 1)) for s in false], raw_identical=summ["raw_copy_byte_identical"],
                input_unchanged=summ["input_unchanged"])


def cardiac_distribution(folder, n):
    t, names, data, ci = load(folder, n, "off")
    out = {}
    for j, nm in enumerate(names):
        ok = column(data, ci, j, "valid").astype(bool)
        c = finite(column(data, ci, j, "card_db")[ok])
        if c.size < 50:
            continue
        out[nm] = dict(median=float(np.median(c)), p25=float(np.percentile(c, 25)), p75=float(np.percentile(c, 75)),
                       clear=float(100 * np.mean(c > 6)), weak=float(100 * np.mean((c >= 3) & (c <= 6))),
                       faint=float(100 * np.mean(c < 3)), old_gate_10db=float(100 * np.mean(c >= 10)))
    return out


def band_power(t, x, valid, band=CARDIAC_BAND, min_len=200):
    """Mean periodogram power in the band over contiguous valid stretches (no interpolation across gaps)."""
    total, weight = 0.0, 0
    k = 0
    while k < len(t):
        if not valid[k]:
            k += 1
            continue
        e = k
        while e + 1 < len(t) and valid[e + 1] and abs(t[e + 1] - t[e] - 0.1) < 0.03:
            e += 1
        if e - k + 1 >= min_len:
            seg = x[k:e + 1] - np.mean(x[k:e + 1])
            w = np.hanning(len(seg))
            f = np.fft.rfftfreq(len(seg), 0.1)
            p = np.abs(np.fft.rfft(seg * w)) ** 2 / np.sum(w ** 2)
            sel = (f >= band[0]) & (f <= band[1])
            total += p[sel].sum() * (len(seg))
            weight += len(seg)
        k = e + 1
    return total / weight if weight else float("nan")


def ssr_row(folder, n, side_channels):
    t, names, data, ci = load(folder, n, "5mm")
    t0, names0, data0, ci0 = load(folder, n, "off")
    res = {}
    for label, group in side_channels.items():
        frames_applied, frames_valid, ratios = 0, 0, []
        for nm in group:
            j = names.index(nm)
            ok = column(data, ci, j, "valid").astype(bool)
            st = column(data, ci, j, "ssr_state")
            frames_valid += int(ok.sum())
            frames_applied += int(sum(1 for k in np.where(ok)[0] if st[k] == "applied"))
            hbo_on = column(data, ci, j, "hbo").astype(float)
            j0 = names0.index(nm)
            ok0 = column(data0, ci0, j0, "valid").astype(bool)
            hbo_off = column(data0, ci0, j0, "hbo").astype(float)
            n_common = min(len(hbo_on), len(hbo_off))
            m = ok[:n_common] & ok0[:n_common] & np.isfinite(hbo_on[:n_common]) & np.isfinite(hbo_off[:n_common])
            p_on = band_power(t[:n_common], hbo_on[:n_common], m)
            p_off = band_power(t[:n_common], hbo_off[:n_common], m)
            if np.isfinite(p_on) and np.isfinite(p_off) and p_off > 0:
                ratios.append(p_on / p_off)
        res[label] = dict(applied_pct=100.0 * frames_applied / frames_valid if frames_valid else None,
                          band_ratio_median=float(np.median(ratios)) if ratios else None, n=len(ratios))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--out")
    ap.add_argument("--geometry", default=str(Path(__file__).resolve().parents[1] / "config/optical_geometry_board1.json"))
    a = ap.parse_args()
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from noctinirs_host.geometry import load_geometry
    geo = load_geometry(a.geometry)
    side = {c.name: next(d.side for d in geo.detectors if d.pd == c.pd) for c in geo.channels}
    longs = {s: [c.name for c in geo.channels if c.kind == "long" and side[c.name] == s] for s in "LR"}
    lines = []
    emit = lines.append
    emit("## CH1 / CH2 (valid %, median SNR, dHb sanity)")
    for n in (3, 4, 5, 6):
        for name in ("CH1", "CH2"):
            b, f = channel_summary(a.base, n, name), channel_summary(a.final, n, name)
            emit(f"record_{n} {name}: base {json.dumps(b, default=float)} | final {json.dumps(f, default=float)}")
    emit("\n## Valid % (all channels, t>=10 s): base -> final")
    for n in (3, 4, 5, 6):
        t, names, _, _ = load(a.final, n, "off")
        emit(f"record_{n}: " + ", ".join(f"{nm} {valid_percent(a.base, n, nm):.0f}->{valid_percent(a.final, n, nm):.0f}" for nm in names))
    emit("\n## Motion markers")
    for n in (3, 4, 5, 6):
        emit(f"record_{n}: base {json.dumps(motion_row(a.base, n))} | final {json.dumps(motion_row(a.final, n))}")
    emit("\n## cardiac_db (final): median [p25, p75], % clear/weak/faint, % that passed the old 10 dB gate")
    for n in (4, 5, 6):
        for nm, v in cardiac_distribution(a.final, n).items():
            emit(f"record_{n} {nm}: {v['median']:.1f} [{v['p25']:.1f}, {v['p75']:.1f}] clear {v['clear']:.0f}% weak {v['weak']:.0f}% faint {v['faint']:.0f}% (old>=10: {v['old_gate_10db']:.0f}%)")
    emit("\n## SSR 5 mm: % of valid long frames with SSR applied; cardiac-band (1-2 Hz) power ratio dHbO SSR-on/SSR-off")
    for n in (3, 4, 5, 6):
        emit(f"record_{n}: base {json.dumps(ssr_row(a.base, n, longs))} | final {json.dumps(ssr_row(a.final, n, longs))}")
    text = "\n".join(lines)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
