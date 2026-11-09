# Stage D2 implementation

Claude (Sonnet 5.5, author). Base: Stage D at `b9bf025`. Protocol contract
unchanged: firmware pin and RAW layout 3 as in Stage D. No wire format, opcode
or state transition was added on the host side.

## Core: `noctinirs_host.preview` (no Qt, no numpy)

| Module | Role |
| --- | --- |
| `slots.py` | Assembles RAW samples into per-slot measurements. Only phase classes ON_STABLE (4) and OFF_STABLE (7) enter a mean. A slot that starts after stream start or a discontinuity, or ends in one, is `partial` and unusable. int24 rail values mark `saturated`. |
| `params.py` | `ExtinctionTable`, `DpfSetting`, `PreviewConfig`. Every set has a `source` and `status` (`unverified`). `to_metadata()` is the provenance record. |
| `dpf.py` | Scholkmann & Wolf 2013 Eq. 7 with warnings outside 690–832 nm and age 0–70. |
| `od.py` | `OD = ln(baseline / I)`. Rejects None, "", bool, NaN, inf, ≤ 0. Never labelled mOD. |
| `mbll.py` | 2×2 MBLL, decadic ε × ln10, mm→cm, result µM, change only. Rejects non-finite or missing input and a singular matrix. |
| `baseline.py` | Median over valid samples for an adjustable duration (default 10 s), minimum sample count, reset. |
| `shortsep.py` | Causal rolling OLS/Ridge in the OD domain, per wavelength, per hemisphere. Running sums, rebuilt exactly every window. 5 mm, 8 mm or both. `auto` = OLS for one regressor, Ridge for two (legacy rule). |
| `pipeline.py` | Cycle assembly (a cycle closes when the slot index wraps or the epoch changes), channel checks, baseline, OD, SSR, MBLL, provenance. |

Defaults carried from the legacy UI (ε 735: 420/1510, 850: 1060/800; DPF 6/6;
ridge λ 1e-3; window 30 s; minimum 30 samples) live only in the core and carry
the `unverified` status and their source. The UI package contains no MBLL, OD,
DPF or ε constant (a test scans for them).

### Checks before a sample reaches OD/MBLL (per channel, both wavelengths)

A channel is `INVALID` with a reason and NaN output (drawn as a gap, never 0)
unless all hold for the same cycle:

- both wavelength slots of the channel's source exist (`NO_SLOT`);
- neither is partial or gapped (`GAP`);
- ON and OFF stable samples exist (`NO_ON_OFF`);
- no int24 rail value (`SATURATED`);
- no contralateral detector shows an ON−OFF step (`LEAK_SUSPECT`, negative
  control from `Geometry.is_contralateral`);
- `I = mean(ON) − mean(OFF)` is finite (`NONFINITE`) and at least the
  provisional step threshold (`NO_LED_STEP`).

A short channel used for regression must be valid in the same cycle
(`NO_SHORT_REGRESSOR`). While the baseline or the regression is not ready the
state is `BASELINE` or `SSR_WARMUP` and no Hb is shown: there is no silent raw
to corrected switch.

### Mapping

Slot, AIN, source–detector, channel names and distances come only from
`Geometry` (`config/optical_geometry_v1.json`, status
`draft_pending_caliper`). The UI shows "DRAFT" next to distances and in the
panel note. AIN to PD translation still happens only in
`Geometry.pd_for_ain`.

## Recording

- `default_recording_folder()`: `Documents\noctinirs\<date>` (Windows known
  folder, honours redirection). `next_record_number()` scans `record_<X>` with
  the known suffixes and returns highest + 1. `RecordingTarget.prepare()`
  creates the folder, picks the stem and checks that no file exists.
- Start with recording runs on the single session worker: prepare (disk job),
  arm (C recorder, exclusive creation is still the no-overwrite authority),
  then START, without the UI thread touching disk. The recording finalizes
  automatically after the device reports STOPPED. A Start pressed while
  housekeeping disk work is running is queued instead of rejected.
- RAW files are written exactly as in Stage C. Preview provenance goes to
  `settings_at_start.preview` of the sidecar, to events for live changes, and
  to a separate `<stem>.preview.json` (config history, baseline resets, frame
  and invalid counts, stated limits). The latter is created exclusively and is
  never part of the RAW archive.
- Preview faults are caught and counted (`preview_errors`); they cannot
  disturb capture or recording.

## UI

Welcome → Setup (Device incl. ENGINEERING, MBLL, Short separation, Session with
readiness) → Live. All Stage D gates stay: STOPPED, ENGINEERING applied from an
advertised Fs, START recovery, SAFE_OFF confirmation, disconnect and close
cleanup. The Live panel shows 16 sweep plots, short-channel chips, a detail
view and the live settings form. Display modes: Δ Hb (µM), OD (ln), intensity
(ON−OFF counts).

- The x axis is real time (host receive time). A time hole larger than 2.5
  nominal periods breaks the line. A disconnect or link loss appends an
  explicit all-invalid marker.
- Fixed y scales clamp and mark clamped points in red. Auto scale never clamps.
- Titles show the channel state and reason instead of a quality meter.
- Wrapping boxes (`AutoText`) replace labels and line edits for INFO, paths
  and status text.

## Fake device

`EngineeringFake` now follows a 20-slot plan of 10 samples per slot (ON/OFF
phases), two 100-sample packets per cycle, one cycle per 1/Fs. Counts are
synthetic. Test hooks: `dead_slots`, `saturate`, `drop_cycles`, `leak`,
`info_extra`. No hardware timing is modelled.

## Not changed

C recorder, B controller and session rules, codec, wire, gate, geometry, pins,
firmware pin. One test fix: the subprocess in
`test_raw_process_exit_without_finalize_recovers_flushed_archive` now gets
`PYTHONPATH=<repo>/src`.

## Open limits (also in the handoff)

1. ON/OFF come from the reported scheduled phase classes. RAW carries no
   device-verified LED state, so "LED on" is only a host-observed ON−OFF step
   above a provisional threshold (50 counts, unmeasured, chosen by the author).
   The same threshold drives the leak check.
2. Geometry is DRAFT (distances pending caliper). ε, DPF and the 735/850 labels
   are unverified.
3. The cycle definition (slot index wraps, 20 slots) and the phase plan are
   modelled on the contract and the fake, not on observed Batch 5A traffic.
4. The time axis is the host receive time of the packet. Packet batching adds
   jitter of up to one packet.
5. Clamp marking exists only for fixed scales.
