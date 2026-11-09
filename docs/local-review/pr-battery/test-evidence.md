# Battery indicator · author evidence

Claude (Sonnet 5.5, author and test runner). Exact code/test candidate
`d0b3d2b59099f73e4ec5b30b140fbec490abc369`. Classes: static, host/unit, simulation. Artifacts are
outside Git in `../pr-battery-evidence/` relative to the host clone root, with `SHA256SUMS.txt`.
They are editable local logs. A fake device is simulation, not a device test.

Environment: Windows 11 x64 build 26300, Python 3.13.5, `venv-stageD-ui` (PySide6-Essentials
6.12.0, pyqtgraph 0.14.0, numpy 2.5.4, pytest 9.2.0, PyInstaller 6.22.3). Golden vectors from
`../pr-a-evidence/golden` via `NN2_GOLDEN_DIR`.

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
$env:NN2_D_SCREENSHOTS = (Resolve-Path ../pr-battery-evidence/screens).Path
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-battery-evidence/full-suite.xml
../venv-stageD-ui/Scripts/python.exe -m compileall -q src tests packaging
../venv-stageD-ui/Scripts/python.exe packaging/build.py
```

| Run | Outcome |
| --- | --- |
| Full suite at the code commit | **720 passed, 0 skipped, 0 failed**, 336 s (672 from D2, plus 42 core/unit and 6 Qt battery tests) |
| compileall, geometry CLI, `git diff --cached --check` | exit 0 |
| `packaging/build.py` | exit 0 (`build.log`) |
| Exe launch | window title read, responsive, then terminated |

An earlier full run had 1 failure, `test_disconnect_draws_a_gap` (a D2 test whose result depended
on where the fake stream was cut). It was fixed (see [responses](responses.md)) and the whole
suite was rerun to the result above.

## Golden bytes

`tests/fixtures/battery_vectors_519a96a.json` holds the request and four replies (valid,
initial/error 259, stale/error 263 in STREAMING with session 0x2A, UNKNOWN_COMMAND). They
were regenerated with `tools/nn2ctl.py` (`encode`, `encode_envelope`, `describe_reply`) from the
firmware checkout at 519a96a on 2026-11-09 and compared with the vectors in the assignment:
**all five identical byte for byte.** The reference client also decoded the three OK replies to
the field values in the fixture (checked in that run, not by a repository test, because
`nn2ctl.py` is not part of this repository). The repository tests check host encoding against the
bytes and host decoding of the bytes.

## Coverage map

- Codec: request bytes, reply bytes both ways, units, no clamp above 100 %, 16 negative cases
  (missing, extra, reordered, spacing, newline, bad flag, negative value, non-ASCII, valid with
  negative age, invalid with values, invalid with error 0, over-long digits, empty, binary),
  extension on an error status.
- Display status: valid, not available (259 and 262), stale refresh, no reply keeps the last
  value, other status error, unsupported stops polling, reset on reconnect.
- Schedule with a fake clock and a recording transport: query right after INFO and
  get_config; connect survives UNKNOWN_COMMAND and silence; 30 s idle; 60 s scanning; heartbeat
  count over 60 s of scanning stays at its cadence; no query while STARTING, STOPPING or START is
  uncertain; about +7 s after STOP, fresh data judged by `battery_age_ms`; stale data gives at
  most 2 repeats then the 30 s schedule; UNKNOWN_COMMAND ends polling.
- Isolation: simulator owner lease unchanged by a query; device state, fault and session
  unchanged; trailing args rejected by the simulator; plain controller leaves it off by default.
- UDP peer: RAW valid / foreign-session / malformed between request and reply (valid RAW
  forwarded unchanged to `raw_sink`; counters `raw_routed`, `wrong_session`, `malformed`; reply
  still accepted); wrong req_id, wrong opcode and a late reply are not results and do not move
  the state; a silent peer gives `battery_timeouts` but `counters["timeouts"]` stays 0, session
  kept and at least 28 heartbeats in 15 s; UNKNOWN_COMMAND; a foreign session id in the reply
  is accepted as data and not adopted; a second endpoint gets OK, adopts no session, sends no
  heartbeat and the owner keeps its session.
- Text: all variants, no "charging" or "mengisi" claim, low mark.
- Qt (local fake): label visible on Welcome, the four setup steps and Live; OK text; scanning
  note; disconnect shows `Baterai —`; variants not available, refresh failed, above 100 %,
  unsupported, silent; no control timeout; the recording sidecar contains no "battery".

## Screenshots and exe

`screens-real/` (real Windows Qt platform, local fake): `ok-0-welcome`, `ok-1-setup`,
`ok-2-live` (scanning, cached note), and setup views for unsupported, silent, refresh-failed,
not-available and low (12 %). `screens/*.png` come from the tests under `offscreen`, which has no
fonts (text shows as boxes); layout only. Exe `NoctiNIRS-Host.exe` sha256
`112B21C01DC02883805CBA65CE9D9080F3A5577098C3287F3D2089B803432692`, zip
`D8D091E532550A4433674FCD4C047E99EE61889B8DAF1434206DD6E517DC1F35`. The exe was only launched
(`exe-welcome-screen.png`); the fake-run screenshots come from the source run. No hardware claim.

## Not run

Any real device, the flashed 519a96a build, bench, HIL, CI, other OS, Python 3.12.
