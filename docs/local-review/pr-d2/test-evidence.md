# Stage D2 author evidence

Claude (Sonnet 5.5, author and test runner). Exact code/test candidate
`c756a73165ec212df380607ea555f473a7edeb63`. Classes: static, host/unit,
simulation. Artifacts are outside Git in `../pr-d2-evidence/` relative to the
host clone root, with `SHA256SUMS.txt`. They are editable local logs.

Environment: Windows 11 x64 build 26300, Python 3.13.5, venv
`venv-stageD-ui` (PySide6-Essentials 6.12.0, pyqtgraph 0.14.0, numpy 2.5.4,
pytest 9.2.0, PyInstaller 6.22.3). Local golden vectors from
`../pr-a-evidence/golden` via `NN2_GOLDEN_DIR`.

## Commands

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
$env:NN2_D_SCREENSHOTS = (Resolve-Path ../pr-d2-evidence/screens).Path
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-d2-evidence/full-suite.xml
../venv-stageD-ui/Scripts/python.exe -m compileall -q src tests packaging
../venv-stageD-ui/Scripts/python.exe packaging/build.py
```

| Run | Outcome |
| --- | --- |
| Full suite at the code commit (Stage D suite plus D2) | **672 passed, 0 skipped, 0 failed**, 247.8 s, 2026-11-09 |
| New core tests `tests/test_preview_core.py` | 34 passed |
| New Qt tests `tests/ui/test_engineering.py` | 11 passed |
| `test_raw_process_exit_without_finalize_recovers_flushed_archive` | passes (it failed with `No module named noctinirs_host` before the PYTHONPATH fix) |
| compileall, geometry CLI, `git diff --cached --check` | exit 0 |
| `packaging/build.py` | exit 0, see `build.log` |

During development the tests found two real defects, fixed before the commit:
a Start pressed while housekeeping disk work ran was rejected, and a
background destination refresh overwrote the visible result of SAFE_OFF. See
[responses](responses.md).

## What the core tests prove (simulation, not physics)

- MBLL against an independent comparator (decadic log10 forward form, Gauss
  elimination, not Cramer): 1000 random cases (ε ±30 % around the legacy table,
  d 5–40 mm, DPF 3–9), **max absolute difference 7.1e-15 µM** (requirement
  < 1e-12). With ε drawn over a very wide range the 2×2 system becomes ill
  conditioned and the difference grew to about 2e-9 µM. The claim is limited to
  the conditioned domain stated here.
- Known ΔHbO/ΔHbR recovered through intensity, baseline, `ln` OD and MBLL
  within 1e-9 µM for 5, 18.5 and 30 mm.
- `ln` versus log10: log10 OD fed to the ln chain scales the result by exactly
  1/ln10 (tested), and `optical_density(1, e) = 1`.
- DPF Scholkmann & Wolf: age 28 gives 6.255 (735 nm) and 5.177 (850 nm), range
  warning at 850 nm, age warning above 70.
- Regression: rolling OLS and Ridge equal a batch comparator within 1e-9
  relative; the 1-regressor OLS matches the closed form; window drop test;
  recovery of the injected signal; a zero-variance regressor is not ready.
- Invalid input rejected: None, "", "0.1", NaN, ±inf, bool, 0, negatives, zero
  ε, singular matrix, missing DPF age.
- Pipeline: dead slot, saturated slot, leaking slot, missing second wavelength,
  dropped cycle and link loss give INVALID channels with NaN, never 0; the time
  axis is receive time; SSR shows `SSR_WARMUP` without a value; an invalid short
  channel invalidates its long channels; live configuration changes reset only
  what they touch and are logged.
- Folder and naming: default folder, highest X + 1 including unrelated files and
  `.preview.json`, folder creation, no reuse.

## What the Qt tests prove (local RAW fake)

- 3 screens and 4 setup steps; Back keeps input; Live does not START (device
  remains STOPPED, zero packets).
- Default folder shown, created at START; a folder with `record_2` gives
  `record_3` then `record_4`; an existing file is untouched; an empty day gives
  `record_1`; sidecar `settings_at_start.preview` and `.preview.json` exist; the
  RAW archive replays.
- Browse (dialog stubbed) changes the folder and the recording lands there.
- A 300-character INFO token and a 150-character folder path are fully visible
  with no horizontal scroll; INFO is split one field per line.
- 16 plots filled from the fake RAW stream in Hb, OD and intensity modes;
  short-channel detail has the `SCx_I735` … names.
- Invalid channels (dead and saturated slots) are NaN series with a visible
  reason; disconnect appends a gap.
- Live settings (SSR "both", age DPF) reach the worker; a baseline reset is
  recorded in provenance.
- Retained Stage D behaviour: UDP START timeout recovery stays explicit and the
  GUI responsive; a recorder open failure is reported and does not START.

## Screenshots and exe

- `../pr-d2-evidence/screens-real/*.png`: nine screens from a run of the same
  source with the real Windows Qt platform and the local fake (welcome, four
  setup steps, live before START, live Hb with 8 mm regression, OD, intensity).
  In that run slots 4 and 5 are dead on purpose, so CH3 and CH4 show as INVALID
  gaps.
- `../pr-d2-evidence/screens/*.png`: written by the tests under the `offscreen`
  platform. That platform has no fonts, so text appears as boxes. Use them only
  as layout checks.
- Exe: `dist/NoctiNIRS-Host/NoctiNIRS-Host.exe` (sha256
  `BC55BADA9DF54862AA58D26F8417E3CFC4698CCA1EE1C987A8C3CA53E138C7EB`), zip
  `E5A6C940B34A6A5A5DD881E193E5F0E385A9AEED0C1D35CC16671083D5012D35`. It was
  launched, the window title and responsiveness were read, a desktop screenshot
  was saved (`exe-welcome-screen.png`) and the process was terminated. The exe
  was **not** driven through the fake run; the fake-run screenshots come from the
  source run. No hardware claim.

## Not run

Hardware, bench, HIL, Batch 5A firmware, real UDP with a powered device, other
OS, Python 3.12, CI.
