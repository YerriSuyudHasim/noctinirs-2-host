# Stage D4 · test evidence

Evidence class: static, host/unit, simulation, replay. No hardware.

## Test runs (venv `venv-stageD-ui`, offscreen Qt)
| Run | Result |
| --- | --- |
| Full suite `pytest tests` (code state of the D4 code commit) | **673 passed, 6 skipped, 0 failed** in 192 s (JUnit: `..\pr-d4-evidence\full-suite.xml`) |
| Baseline D3 `7c1615d` (collect only) | 651 tests collected |
| New in D4 | 28 tests (`tests/test_design.py`, `tests/ui/test_design_ui.py`); existing `tests/ui/test_engineering.py` adapted to the new Live layout |
| `python -m compileall src tests` | OK |

Note: the brief mentioned 735 existing tests; this checkout has 651 at D3, so 679 = 651 + 28 collected now.

Covered by the new tests: token → QSS, contrast of tokens, theme switch + persistence, reduce motion
(all animations off), channel row order, exactly 3 rows at 4 sizes, shared linked X axis, shared
scale lock, off-screen rows not redrawn, peak decimation, START/STOP/navigation visible at all sizes
and with the sidebar collapsed, text elision.

Test hygiene fix: closed `EngineeringWindow` objects were never destroyed (~1,600 widgets per test),
which made the old window's theme change very slow when all UI tests ran in one process (1 h 44 min,
one failure). Fixed with `WA_DeleteOnClose`.

## Build
`python packaging/build.py` OK (log `..\pr-d4-evidence\build.log`). `NoctiNIRS-Host.exe --self-test`
returned `SELFTEST OK` (this exercises the old simulated window only; the exe was launched, not
used with a device).

| File | sha256 |
| --- | --- |
| `dist/NoctiNIRS-Host/NoctiNIRS-Host.exe` | `2ef97b8a658d8ba733cca8d35a93a49bd2845e55b6ccd88207bff6d8fa218871` |
| `dist/NoctiNIRS-Host-win32.zip` | `70efc0b6437b87f9aaf6d6dcf80a71ab3e55e66c4b7654c2c2965869297fa2c5` |

## Performance (`bench_live.py`, 1366×768, 3 rows, replay of `record_1.raw.bin`, 20 s per run)
| run | draws/s | draw mean / p95 / max ms | process CPU (1 core = 100 %) |
|---|---:|---|---:|
| idle harness | 0 | – | 3 % |
| replay 10 Hz | 9.9 | 5.3 / 8.1 / 21.7 | 17 % |
| stress 100 Hz | 24.9 (25 Hz cap) | 9.3 / 14.8 / 24.9 | 75 % |
| 10 Hz, scrolled to the end | 9.9 | 4.8 / 6.4 / 17.8 | 18 % |

Full table: `..\pr-d4-evidence\benchmark-live.md`.

## Screenshots (`..\pr-d4-evidence\screenshots\`, 48 files)
Names: `{light|dark}-{1280x720|1366x768|1920x1080|fullscreen}-{1-welcome|2-setup-device|3-setup-mbll|4-setup-ssr|5-setup-session|6-live}.png`.
Taken with the real Windows platform plugin and the replayed archive. Re-shot after three self-review
rounds (clipped text, axis alignment, contrast). Remarks: Welcome = three cards, theme toggle in the
header; setup steps = form sections with chips; Live = action bar, sidebar, 3 rows plus footer axis,
preview label visible. The 1920×1080 set is a resized grab of a larger window.
