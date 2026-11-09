# Stage D3 implementation

Claude (Sonnet 5.5, author). Base: battery notes `3a343a6` (battery code `d0b3d2b`, D2 code
`c756a73`, D2 notes `1c92772`). Source of the requirements: the Owner's D3 brief and the bench
gap analysis of 9 Nov 2026 (`BENCH_PREVIEW_GAP_ANALYSIS_2026-11-09.md`, record_1). Nothing in
the protocol, the RAW parser, routing, recording format, START/STOP/SAFE_OFF, heartbeat, the
battery contract, the MBLL formula, ε, DPF or the geometry was changed.

## A. Preview quality (core, no Qt)

New module `preview/quality.py` holds the estimators and every host-chosen constant, each
named and commented with its source (the bench report). The constants are saved in the
provenance (`quality_constants`) together with the noise sigma per slot and AIN
(`noise_sigma_counts`, status "estimated online").

| Change | Behaviour now |
| --- | --- |
| A1 leak | Per AIN. For each same-side AIN, the opposite-side steps of the same slot are compared with `max(5 σ_c, 0.1 × same-side step)`. Above that the channel is `LEAK_SUSPECT` (invalid) — but only once σ_c is known (at least 8 earlier occurrences). Above `4 σ_c` it is the warning `LEAK_WARN`. One AIN never invalidates another same-side AIN. |
| A2 sigma | `SlotMeasurement` keeps sums of squares. σ = the larger of the within-slot standard error of ON−OFF and the between-occurrence sigma (robust MAD of successive differences / √2 over the last 64 occurrences, so slow drift cancels). The fixed 50-count threshold is gone. Invalid only if `I ≤ 0` or not finite (`NO_LED_STEP`). `I < 3σ` is the warning `LOW_SIGNAL`; the value stays on the plot. |
| A3 ambient and saturation | `AMBIENT_HIGH` (invalid): OFF mean differs from the running median (31 occurrences, event values excluded) by more than `max(20 × robust σ_OFF, 10 % of the median)`, or OFF is within 3 % of the ADC rail. A level that stays shifted for 30 occurrences is accepted as the new normal. `SATURATED` (invalid): any rail sample, ON mean at 97 % of the rail or more, or a flat top (identical ON samples above 25 % of the rail). |
| A4 SCR fallback | When the short regressor is not ready, its short channel is invalid, or the short channel is weak (`LOW_SIGNAL`), the long channel is shown without correction, with `ssr_state = "UNCORRECTED"` and the reason. It is not a gap. The old `SSR_WARMUP` and `NO_SHORT_REGRESSOR` outcomes no longer hide a channel. |
| A5 counters | `invalid_counts` are taken after SSR and MBLL (so an MBLL failure counts). New: `invalid_reason_counts`, `warning_counts`, `uncorrected_frame_counts`, all in the provenance and kept apart. |
| A6 loss | `PreviewPipeline.loss_stats()`: samples missing (jumps of `sample_sequence`), jumps, approximate missing messages (`msg_sequence`; replies share the counter), `gap_before` packets reported by the device. Run snapshot key `preview_loss`. |

`ChannelPoint` has new fields: `warnings`, `ssr_state`, `ssr_reason`, `sigma735`, `sigma850`
and the measured `snr735` / `snr850` (I/σ). `PreviewConfig` lost `min_phase_samples` and
`min_led_step_counts` (replaced by the constants above); the config metadata now has a
`quality` block instead of `evidence_thresholds`. `feed()` takes an optional `msg_sequence`.

## B. Live layout

- Left: a scrollable panel of foldable sections (Sesi dan kontrol, Tampilan, Pratinjau: DPF SCR
  baseline, Detail channel, Diagnostik). A splitter replaces the fixed 450 px width: the panel
  is 300–480 px wide and the operator can move the divider.
- Right: the graph panel, which takes the rest of the window. It has one banner line, the two
  hemisphere columns, the short-channel chips and a legend. The 52 px minimum plot height is
  gone; rows stretch with the window (the last row is taller because it carries the time axis).
  Lines are 2 px, titles and ticks are 8–9 pt.
- Quality on each plot: a gap is missing or invalid data. A drawn line carries amber triangles
  for `LOW_SIGNAL` / `LEAK_WARN` and violet diamonds for "tanpa koreksi SCR". The plot title
  ends with ▲ / ◆ (warning), ✕ (invalid now) or … (baseline); the long status text is in the
  plot tooltip and in the detail section.
- Clicking a plot or a short-channel chip fills the detail section (`CHn_I735`, `_I850`,
  `_OD735`, `_OD850`, ΔHbO/ΔHbR/ΔHbT, warnings, SCR state or its reason, β, σ and measured
  I/σ). Folding it away never changes the graph size.
- New scale choice "Auto (robust)" (default): the visible window is scaled to its central 96 %
  plus a margin, and points beyond are clamped to the edge and marked in red; "Full range"
  keeps the whole range and clamps nothing. A bench spike of ±100 µM no longer flattens the
  rest of the signal.
- The loss counter (samples, percent, jumps, messages, device `gap_before`) is in the session
  section. The battery label stays in the header so that it is visible on every screen.
- Banner shortened to one line; white/blue theme kept. The graph panel has its own white
  background so the system dark theme cannot make it unreadable.
- The note about the draft geometry now also says the 5/8 mm labels of the short channels are
  unverified (SC2 8 mm saturated, SC1 5 mm did not, on 9 Nov). The geometry file is unchanged.

## C. Window sizing

- Welcome, Setup and Live are all inside scroll areas, so `QStackedWidget` no longer takes the
  largest page's minimum size. `minimumSizeHint` of the window is about 640 × 420.
- The navigation row is outside the stack at the bottom. Feedback and error labels are capped at
  64 px so they cannot push it out.
- The initial size is limited to the screen's available area.
- Verified by tests at 1280 × 720, 1366 × 768 and 1024 × 600 on every screen, and in maximized
  and fullscreen mode.

## Replay tools

`docs/local-review/pr-d3/replay_preview.py` replays a `.raw.bin` through the preview code of a
chosen source tree, so the same recording can be measured before and after.
`screenshots_from_replay.py` renders the Live window from such a replay with the real Windows Qt
platform. Both only read the archive.

## Open points

1. All constants in `quality.py` are tuned on one bright-room recording that the report calls
   not valid as an optical baseline. They are not validated thresholds.
2. σ needs 8 occurrences (about 1 s at 10 Hz). Until then a large leak is only a warning.
3. `LOW_SIGNAL` short channels are excluded from SCR; a weak but useful short channel is not
   used. Whether that is right is a scientific question for later.
4. A weak channel with `I > 0` is shown with a warning, as asked. After a baseline made of weak
   values its OD can swing widely; the warning is the only guard.
5. Robust auto scale hides how large a spike is (it is marked, not drawn to scale). "Full range"
   shows it.
