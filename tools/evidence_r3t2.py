"""Summarise replay outputs (tools/replay_quality_r3t2.py): achromatic flags, tiers, SSR usage, cardiac PSD on/off.
Usage: python tools/evidence_r3t2.py --rep DIR --out evidence.json"""
import argparse, json
from pathlib import Path
import numpy as np
from scipy import signal

ap = argparse.ArgumentParser(); ap.add_argument("--rep", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
rep = Path(a.rep)
COL = {n: i for i, n in enumerate(["valid", "i735", "i850", "od735", "od850", "hbo", "hbr", "s735", "s850", "sci",
                                   "card_db", "r_hbo_hbr", "tier", "motion_z", "warn", "ssr_state", "ssr_reason"])}

def load(tag):
    z = np.load(rep / f"{tag}.npz", allow_pickle=True)
    return z["t"], list(z["channels"]), z["data"]

def col(data, ci, name, f=float):
    return np.array([[ (f(row[ci][COL[name]]) if row[ci][COL[name]] is not None else np.nan) for ci in [ci]] for row in data]).ravel()

def runs(mask, minlen):
    idx = np.where(mask)[0]
    if not len(idx): return []
    br = np.where(np.diff(idx) > 1)[0]
    st = np.r_[idx[0], idx[br + 1]]; en = np.r_[idx[br], idx[-1]] + 1
    return [(s, e) for s, e in zip(st, en) if e - s >= minlen]

def band_power(t, y, valid, segs, lo, hi, nper=64):
    tot, n = 0.0, 0
    for s0, s1 in segs:
        m = valid & (t >= s0) & (t < s1)
        for r0, r1 in runs(m, nper):
            f, p = signal.welch(y[r0:r1], fs=10.0, nperseg=nper, detrend="linear")
            tot += p[(f >= lo) & (f < hi)].sum() * (f[1] - f[0]) * (r1 - r0); n += r1 - r0
    return tot / n if n else float("nan")

result = {}
for tag in sorted(p.stem for p in rep.glob("*.npz")):
    t, chans, data = load(tag)
    rec = {}
    for ci, n in enumerate(chans):
        valid = np.array([bool(r[ci][0]) for r in data])
        if valid.sum() < 50: continue
        warn = [str(r[ci][COL["warn"]]) for r in data]
        achro = np.array([("ACHROMATIC_SUSPECT" in w) for w in warn])
        tiers = [r[ci][COL["tier"]] for r in data]
        rec[n] = dict(valid_pct=round(100 * valid.mean(), 1), achromatic_pct_of_valid=round(100 * achro[valid].mean(), 1),
                      tier_pct={k: round(100 * sum(1 for x, v in zip(tiers, valid) if v and x == k) / valid.sum(), 1)
                                for k in ("good", "noisy", "bad", "not_assessable")},
                      sci_med=float(np.nanmedian(col(data, ci, "sci"))), card_db_med=float(np.nanmedian(col(data, ci, "card_db"))),
                      r_hbo_hbr_med=float(np.nanmedian(col(data, ci, "r_hbo_hbr"))))
    result[tag] = rec
# cardiac PSD, SSR on vs off (record_4, rest segments)
psd = {}
if (rep / "r4_ssroff.npz").exists() and (rep / "r4_ssr5mm.npz").exists():
    segs = [(25, 68), (77, 104)]
    t0, ch0, d0 = load("r4_ssroff"); t1, ch1, d1 = load("r4_ssr5mm")
    tn = {"nopp": load("r4_ssr5mm_nopp")} if (rep / "r4_ssr5mm_nopp.npz").exists() else {}
    for name in ("CH14", "CH16", "CH12", "CH6"):
        ci = ch0.index(name)
        v0 = np.array([bool(r[ci][0]) for r in d0]); v1 = np.array([bool(r[ci][0]) for r in d1])
        applied = np.array([str(r[ci][COL["ssr_state"]]) == "applied" for r in d1])
        v = v0 & v1
        row = {}
        for wl in ("od735", "od850"):
            y0 = col(d0, ci, wl); y1 = col(d1, ci, wl)
            row[wl] = {band: dict(off=band_power(t0, y0, v, segs, lo, hi), on=band_power(t0, y1, v, segs, lo, hi))
                       for band, (lo, hi) in {"cardiac_1.2-2.2Hz": (1.2, 2.2), "slow_0.01-0.3Hz": (0.01, 0.3)}.items()}
            for band in row[wl]:
                o, n_ = row[wl][band]["off"], row[wl][band]["on"]
                row[wl][band]["on_over_off"] = n_ / o if o else float("nan")
        row["frames_ssr_applied_pct_of_valid"] = round(100 * ((v & applied).sum() / max(1, v.sum())), 1)
        psd[name] = row
result["cardiac_psd_record4_ssr_on_vs_off"] = psd
Path(a.out).write_text(json.dumps(result, indent=1, default=float))
print("written", a.out)
