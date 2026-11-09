# Stage D3 author evidence

Claude (Sonnet 5.5, author and test runner). Exact code/test candidate
`5a9402d39412ff419cd1116c195a5ab07da19697`. Classes: static, host/unit, simulation, replay of a
recorded archive. Artifacts are outside Git in `../pr-d3-evidence/` relative to the host clone
root, with `SHA256SUMS.txt`. They are editable local logs. No hardware.

Environment: Windows 11 x64 build 26300, Python 3.13.5, `venv-stageD-ui` (PySide6-Essentials
6.12.0, pyqtgraph 0.14.0, numpy 2.5.4, pytest 9.2.0, PyInstaller 6.22.3). Golden vectors from
`../pr-a-evidence/golden` via `NN2_GOLDEN_DIR`.

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
$env:NN2_D_SCREENSHOTS = (Resolve-Path ../pr-d3-evidence/screens).Path
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-d3-evidence/full-suite.xml
../venv-stageD-ui/Scripts/python.exe -m compileall -q src tests packaging docs/local-review/pr-d3
../venv-stageD-ui/Scripts/python.exe packaging/build.py
```

| Run | Outcome |
| --- | --- |
| Full suite at the code commit | **735 passed, 0 skipped, 0 failed**, 588 s (720 before D3, plus 15 new). The Qt tests dominate the time. |
| compileall | exit 0 |
| `packaging/build.py` | exit 0 (`build.log`) |
| Exe launch | window title read, responsive, then terminated (`exe-welcome-screen.png`). Exe sha256 `182C42AEE507DFC4756D113FC345D5F7A6043D6BC8C3F6D820642120DCBF4A48`, zip `F12547F8135872B51B7D4F4868EFF4CB348E73E237F211AC9EE5F9FC499A0B72`. |

## Replay of record_1 (read-only), before and after

`record_1.raw.bin` (15 915 packets, 636 644 samples, 822 frames) was replayed through the
preview code with `docs/local-review/pr-d3/replay_preview.py`. "Before" is the tree of
`3a343a6` (extracted with `git archive`, then removed), "after" is the D3 tree. The `.raw.bin`
was used instead of the `.raw.csv` because the CSV is a projection of the same datagrams; the
before run reproduces the analysis numbers (long 42.8 %, short 0 %, identical
`invalid_frame_counts`).

VALID % of 822 frames (a shown value, including those with a warning or without SCR). Full
per-channel table: `replay-before-after.md`; per-state tables: `replay-{before,after}-ssr-*.md`.

| Group | before, SCR off | after, SCR off | before, SCR 5 mm | after, SCR 5 mm |
| --- | ---: | ---: | ---: | ---: |
| long channels (mean of 16) | 42.8 | **74.1** | **0.0** | **74.1** |
| of which carry a warning | – | 17.1 | – | 17.1 |
| of which shown without SCR | – | – | – | 10.4 |
| short channels (mean of 4) | 0.0 | 38.0 | 0.0 | 38.0 |
| SC1L / SC1R | 0.0 / 0.0 | 80.4 / 70.6 | 0.0 / 0.0 | 80.4 / 70.6 |
| SC2L / SC2R (8 mm, at the ADC rail) | 0.0 / 0.0 | 0.6 / 0.6 | 0.0 / 0.0 | 0.6 / 0.6 |

Range over long channels, after: 56.9 % (CH11, weak signal) to 80.5 % (CH16); CH1, CH2, CH9 and
CH10, which were 9–16 % before, are now 66–76 %.

Where the remaining non-shown frames of the long channels go (after, SCR off;
`replay-after-reasons.txt`; one frame can name a reason for each wavelength, so the mentions
add up to more than the frame share):

| Reason | mentions (% of 13 152 channel-frames) |
| --- | ---: |
| `AMBIENT_HIGH` (the two probe events, 3.2–5.6 s and 66.5–71.6 s, about 52 frames) | 12.1 |
| `LEAK_SUSPECT` (large opposite-side step) | 7.7 |
| `NO_SLOT` (lost packets) | 6.4 |
| baseline (first 10 s) | 5.5 |
| `NO_LED_STEP` (`I ≤ 0`: event frames, weak CH11 and CH13) | 4.5 |
| `GAP` (a slot cut by a sample jump) | 2.8 |

Warnings on shown points: `LOW_SIGNAL` 14.9 %, `LEAK_WARN` 2.4 %. Loss seen by the host:
26 840 of 663 484 expected samples (4.05 %) in 110 jumps; 834 messages (the analysis' 834 of
16 749); the device reported `gap_before` in 0 packets.

Reading: the large improvement comes from the leak rule becoming per-AIN and relative, and from
the SCR fallback. It does not reach the analysis' 77 % estimate, and `LEAK_SUSPECT` /
`NO_LED_STEP` still remove frames that a gentler rule might keep. The constants were tuned on this
one recording, which the report calls not valid as an optical baseline.

## Unit tests (host/unit, simulation)

`tests/test_preview_core.py` (now 44 tests) adds: dead channel is invalid or flagged
`LOW_SIGNAL` and never plain; near-rail and flat-top saturation; exact rail samples; leak small
= `LEAK_WARN` with the value kept, leak large = invalid, another AIN of the slot not dropped;
no leak invalidation before sigma is known; sigma estimated online and saved with the
constants; `LOW_SIGNAL` keeps the value; `AMBIENT_HIGH` for an event and acceptance of a
sustained level shift; invalid counts taken after MBLL; sample and message loss counters
including the wrap case; SCR shows `UNCORRECTED` while warming up and applied afterwards; an
invalid or weak short channel gives an uncorrected long channel, not a gap. The earlier D2 core
tests (MBLL comparator 1000 cases, recovery, ln/log10, DPF, regression) are unchanged and pass.

## Qt tests (local fake, offscreen)

- Navigation: Back and Continue visible and inside the window on all six screens at 1280 × 720,
  1366 × 768 and 1024 × 600, maximized and fullscreen; window minimum size hint ≤ 1024 × 560.
- Layout: settings panel left (300–480 px, no fixed width, a splitter), graphs right and wider
  than twice the panel; plot height grows with the window (1366 × 768 to 1920 × 1080, at least
  25 %); folding the detail never changes the plot size; the diagnostics section starts folded.
- Quality: a warned channel keeps its line with amber markers and a ▲ title; a saturated channel
  is a pure gap with a ✕ title; with SCR 5 mm and dead short channels the left long channels carry
  violet "tanpa koreksi SCR" markers; a click on a chip or plot fills the detail section with
  `CHn_OD735`, the SCR reason and the measured I/σ.
- Robust auto scale clamps and marks a ±500 µM spike while keeping the signal readable;
  "Full range" clamps nothing.
- Packet-loss text shows "400 sampel" for two dropped cycles and `gap_before` 0.
- All D2 and battery Qt tests still pass (one was adapted: a dead channel may now be a flagged
  value instead of only a gap).

## Screenshots

`screens-real/` is from `screenshots_from_replay.py`: the real Windows Qt platform, the normal
window connected to the local fake, with the record_1 preview frames pushed to the graph panel
(no acquisition ran): `live-1366x768-first-sweep.png`, `live-1366x768-end-of-recording.png`,
`live-1366x768-od.png`, `live-1280x720.png`, `live-fullscreen.png` (1680 × 1050 screen). The
replay uses SCR 5 mm, so left-hemisphere long channels show violet diamonds where SC1L was
unusable. `screens/*.png` come from the tests under the `offscreen` platform, which has no fonts
(text shows as boxes); layout checks only. The exe was only launched; the screenshots come from
the source run. No hardware or scientific-validity claim.

## Not run

Any real device, bench, HIL, CI, other OS, Python 3.12, a second recording (record_2 was not
analysed by the Owner's report and was not used).
