# Preview quality metrics (PROVISIONAL)

Status: host-chosen, **provisional**, derived from one subject and the three sessions of 13 November 2026
(`record_3` to `record_6`). Nothing here is a validated threshold, a clinical statement, or a literature value.
Every number below is configurable (`QualityConfig`, `Assumptions`, `PreviewConfig`) and is written to the
session provenance. All indicators are display flags: none of them removes, corrects, or rewrites RAW, OD or Hb data.

Code: `src/noctinirs_host/preview/signal_quality.py` (indicators, motion), `preview/quality.py` (noise, ambient,
saturation), `calibration.py` (calibration selection).

## Names

| Name | Definition | Unit | Where |
|---|---|---|---|
| `I` | mean(ON_STABLE) − mean(OFF_STABLE) of one slot occurrence, one AIN | ADC counts | pipeline |
| `sigma` | noise of `I`: larger of the within-slot spread and the online step-to-step estimate (robust σ of successive differences / √2, 64 steps, ≥ 8 needed) | counts | `quality.py` |
| `SNR_frame` | `I / sigma` per 10 Hz frame, per wavelength | ratio | `ChannelPoint.snr735/850` |
| `CV_pct` | `100 / SNR_frame` | % | detail panel |
| `SNR_cal` | calibration step / spectral noise (0.8–3 Hz excluded), per level | ratio | calibration only |
| `OD` | `ln(I_baseline / I)` | dimensionless | `od.py` |
| `SCI` | zero-lag correlation of the two band-passed (0.5–2.5 Hz) OD signals, 10 s window | −1…1 | `signal_quality.py` |
| `cardiac_db` | `10 log10( variance of mean OD in 0.5–2.5 Hz / variance in 3.0–4.5 Hz )`, 10 s window | dB | `signal_quality.py` |
| `r_hbo_hbr` | correlation of ΔHbO and ΔHbR in the cardiac band, 30 s window | −1…1 | `signal_quality.py` |
| `motion_z` | frame deviation divided by the channel's own robust spread (see Motion) | ratio | `signal_quality.py` |
| `target level` | ON−OFF step on the brightest live target detector, as a fraction of full scale (FS = 8 388 607 counts) | fraction of FS | `calibration.py` |

`cardiac_db` is a peak-to-floor proxy of the mean OD. It is **not** the QT-NIRS "peak spectral power" number,
and values from the two are not comparable. An earlier comment that quoted "about 14–27 dB" for good channels
did not match this pipeline and was removed.

## Thresholds

### Cardiac visibility (`cardiac_db`) — tahap 2.1, Owner-set starting point

| Class | Rule | Meaning |
|---|---|---|
| `clear` ("jelas") | `cardiac_db > cardiac_db_min = 6 dB` | pulse is clearly visible |
| `weak` ("lemah") | `3 dB ≤ cardiac_db ≤ 6 dB` (`cardiac_db_weak = 3`) | faint pulse |
| `faint` ("sulit terlihat") | `cardiac_db < 3 dB` | pulse hard to see |

On records 4–6 the median `cardiac_db` of channels with a visibly pulsing signal was 6–7 dB, so the former 10 dB
rule was too high. `contact_ok` (used by the quality tier) is `SCI ≥ sci_min` or `cardiac_db > cardiac_db_min`.

### Quality tier (`tier_for`)

`SNR_frame` is the smaller of the two wavelengths. `bad`: `ACHROMATIC_SUSPECT` or `SNR_frame < snr_noisy` (20).
`good`: `SNR_frame ≥ snr_good` (100) and `contact_ok`. Otherwise `noisy`. No SNR: `not_assessable`.
`sci_min = 0.75` has not been checked against literature in this repository.

### Warnings that remain on a shown channel

`LOW_SIGNAL` (`I < 3 sigma`) and `ACHROMATIC_SUSPECT` (`r_hbo_hbr ≥ 0.8`). Both are warnings only. There is **no
cross-side leak rule** any more (Owner decision, 2026-11-13): light that reaches the detector on the other side of
the source is a legitimate tissue path, so no `LEAK_WARN` / `LEAK_SUSPECT` exists and no short-separation gate uses one.

### Invalid (drawn as a gap, never 0)

Missing or partial data, no ON or OFF samples, saturation, clipping at the calibration headroom, an ambient jump,
`I ≤ 0` or not finite.

### Motion markers (hemisphere, marker only)

Per channel, `motion_z = max(z_level, z_step)`:
`z_level` = |OD − median of the last 15 s| and `z_step` = |frame-to-frame OD change|, each divided by the
1.4826·MAD of the channel's own quiet history (60 s; only values below the threshold teach the scale).
Tahap 2.1 changes: verdicts start after 5 s of trailing level and 10 s of quiet history (was 7.5 s and 30 s); until
30 s of history exist the spread is inflated by √(30 s / n); the spread never falls below
`motion_noise_floor_k / SNR_frame` (the frame noise in OD units). A channel is only judged when both wavelengths
have `SNR_frame ≥ 20`.

A side is flagged in a frame when at least `motion_min_channels` (2) eligible channels and `motion_side_fraction`
(30 %) of them have `motion_z ≥ motion_z_threshold`. A marker needs `motion_persist` consecutive flagged frames.
Frames without enough eligible channels (burst loss) are neutral for up to `motion_hold_s` (1.5 s). A marker that
starts after a blind interval longer than `motion_gap_bridge_s` (1 s) starts at the last evaluated frame, so the
interval that was lost is marked as well (comparison of the level before and after the gap).

## Calibration level target (tahap 2.1)

The sweep selects, per slot, the code whose step on the brightest live target detector is closest, in log distance,
to the target level, among levels that:

1. pass the existing clipping and headroom checks (unchanged, including the tier-2 envelope rule above 133);
2. reach `SNR_cal ≥ empirical_floor_snr` (5) on the limiting live detector;
3. have at least `min_choice_visits` (3) clean visits. Thinner levels (≥ `min_visits_per_code`, 2) are used only if
   nothing else qualifies and are flagged `VISIT_FALLBACK` / `FEW_VISITS`.

Levels within `empirical_tie` (5 %) of the nearest distance count as equally close: the higher SNR wins, then the
lower code (`TIE_HIGHER_SNR`). If no level qualifies the existing tier-3 default applies (long 133, short 67 at most),
never the dimmest code.

| Slot kind | `target_level` | Basis (host-chosen, provisional) |
|---|---|---|
| long | 10 % of FS | Near detectors in the 13 Nov sweeps sit at about 5–20 % of FS across codes 67–222, so 10 % lies inside the observed range; far detectors (0.02–0.3 % of FS) never reach it and take the brightest safe code (`BELOW_TARGET_LEVEL`). It is about one seventh of the largest step the headroom rule allows (≈ 75 % of FS), which keeps margin for contact changes. |
| short | 20 % of FS | The short code is capped below 133, and at the 5 mm detector the capped codes give about 13–17 % of FS at code 132 and 5–7 % at code 67. A target of 20 % puts the cap-limited best level clearly nearest and avoids a target sitting at the geometric mid-point between 67 and 132, where session noise would flip the choice. |

These two numbers are placeholders chosen from the 13 Nov sweeps, not measured optima. They are meant to be revised
after the Owner's caliper measurements and further sessions.

## Observed values on the 13 Nov sessions

See the tahap 2.1 report for the replay tables (calibration codes per slot and session, `cardiac_db` distribution per
channel, motion segments against the known events, SSR activity).
