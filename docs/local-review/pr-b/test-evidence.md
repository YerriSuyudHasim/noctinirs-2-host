# Author test evidence

Author: Codex / GPT-6, session `codex-pr-b-offline-20261107`; not an independent
verification agent. Classes: static, host/unit, socket-loopback simulation only.
No firmware C execution, online CI or hardware result is claimed.

Environment: Windows x64, Python 3.13.5, pytest 9.2.0, existing `.venv`.
Repository `.python-version` and online workflows specify 3.12.7; that exact
environment is NOT RUN. No package or fixture was downloaded or installed.
PySide6, pyqtgraph and NumPy are absent from this venv.

Raw logs/XML are retained in sibling `../pr-b-evidence/`; environment/package and
golden hashes are in `environment-precommit.json`. Final exact-commit results and
artifact hashes will be appended after the implementation commit is created.

## Precommit chronology (working tree, not exact-commit claims)

1. `python -m pytest tests/test_control.py tests/test_live.py -q -rs`:
   **77 passed**, 0 skipped.
2. `python -m pytest tests/test_udp_session.py -q -rs`:
   **1 failed, 59 passed**. Original failure/traceback: `udp-first.log`.
   Oversize socket test hit Winsock 10040; correction is recorded in responses.md.
   This failure is retained and is not a PR-A PermissionError.
3. `python -m pytest tests/test_udp_session.py tests/test_control.py tests/test_live.py -q -rs --junitxml=../pr-b-evidence/focused-precommit.xml`:
   **137 passed**, 0 skipped (`focused-precommit.log`, 0.85 s).
4. With `NN2_GOLDEN_DIR=../pr-a-evidence/golden`, `QT_QPA_PLATFORM=offscreen`,
   `python -m pytest -q -rs --junitxml=../pr-b-evidence/full-precommit.xml`:
   **513 passed, 3 skipped**, 16.36 s (`full-precommit.log`).
5. Expanded UDP checks: **70 passed**, 0 skipped (`expanded-precommit.log/xml`).
6. Expanded candidate UDP checks: **74 passed**, 0 skipped (`candidate-udp.log/xml`).
7. Final precommit focused control/live/UDP checks: **152 passed**, 0 skipped
   (`final-candidate-focused.log/xml`, 1.81 s), including 75 UDP tests.

Each command used `.venv/Scripts/python.exe`; `Tee-Object` preserved output when
a log is listed. Precommit runs have different working-tree revisions and must
not be relabeled as final-SHA evidence.

## Coverage and exclusions

Socket tests use literal 127.0.0.1 and ephemeral peer ports only. The fake peer is
self-made test code, independent of DeviceSimulator, not compiled firmware. It
injects CRC/length/type/flags/provenance faults, oversize, wrong endpoint, request,
opcode/session/echo mismatch, lost/duplicate/late reply, exact-byte retries,
two-endpoint ownership, boot changes, missing/malformed/narrowed capabilities,
STARTING/STOPPING, heartbeat and cleanup. Deterministic fake-clock coverage also
checks that unrelated replies cannot restart a transaction deadline. No arbitrary
sleep or relaxed assertion was added to hide a failure.

All four existing golden fixture hashes match the unchanged pinned manifest.
The full run includes golden, recorder resilience and bit-exact replay tests.
No golden skip is reported. The three skips are collection-level UI modules:
`tests/ui/test_control_panel.py`, `test_live_view.py`, `test_main_window.py`,
all because PySide6 is missing. Their individual UI cases were NOT RUN, not pass.

NOT RUN/BLOCKED (missing local dependencies): Qt UI execution, UI executable
build/smoke/notices checks. NOT RUN (scope/environment): exact Python 3.12.7,
other operating systems, online CI, hardware/bench/HIL/field, firmware C tests.
Existing workflows were read as check definitions, never executed online.

PR-A's PermissionError remains unresolved/nonblocking. It did not recur in the
precommit full run. Recorder source and tests remain untouched; any recurrence
must keep its original traceback and lifecycle context in a new log entry.


## Final exact-commit author checks

Code/test SHA: `fe4c4f003e3ed01028eaab02ce651cb3b8f14969`. Recorded UTC and package list are in
`checks-fe4c4f0.json`; exact HEAD is also in `exact-head.txt`. Working tree was
clean when this run started. Later changes are documentation-only.

PowerShell commands from the clone root (no download or remote command):

```powershell
$env:NN2_GOLDEN_DIR=(Resolve-Path '../pr-a-evidence/golden').Path
$env:QT_QPA_PLATFORM='offscreen'
git rev-parse HEAD
./.venv/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-b-evidence/full-fe4c4f0.xml 2>&1 | Tee-Object -FilePath ../pr-b-evidence/full-fe4c4f0.log
git diff --check d90e00db1f51c05ad2309fd77547a560450cb91a HEAD
./.venv/Scripts/python.exe -m compileall -q src tests
./.venv/Scripts/python.exe -m noctinirs_host check-geometry config/optical_geometry_v1.json
```

Results: full suite **528 passed, 3 skipped, 0 failed/errors**, 16.84 s, exit 0.
JUnit contains 90 golden cases, 104 recorder/replay cases (59 record + 29
resilience + 16 uint32) and 75 new UDP cases, all passed. All golden fixtures
were available, hash-checked and run; none was skipped. The three UI collection
skips remain exactly the missing-PySide6 modules listed above. Individual UI
cases behind those modules were NOT RUN. The residual PR-A PermissionError did
not recur in either full run; it remains unresolved, not declared fixed.

All three static commands exited 0. Geometry validation retains the existing
`draft_pending_caliper` warning; it does not validate physical distances.
Using only local `git show <firmware-SHA>:<path>`, all 10 control-profile source
SHA-256 values matched `config/reference_pins.json.control_profile.files`.
No recorder source/test, golden fixture or geometry byte changed against base.

Raw evidence is local, provisional review material, not a remote CI artifact or
independent attestation. Hashes anchor the copies supplied for this local handoff:

| File under sibling `pr-b-evidence/` | SHA-256 |
| --- | --- |
| `full-fe4c4f0.log` | `1dfeac3a9f3dedc3da556f6b2764e3e211c910006618d490c0b212c9e659d245` |
| `full-fe4c4f0.xml` | `bf83f6f4e41724ec24be515ce1d4dfcb56a4db88a5cc8f4140b4e07ba7e9cabb` |
| `checks-fe4c4f0.json` | `69d53c9767cd9af1d8cfd8f4434801acdf13b1657d9220ccacd240757d7d85c4` |
| `environment-precommit.json` | `8b3f83fe16274feba3fa7af2fd80ba3e7fe6970865a72b4edd04b49d8af50316` |
| `udp-first.log` | `76258592306931a85ffe21206563d9571c9c8b2303a388b1d4f5e4e77d04885d` |

`checks-fe4c4f0.json` preserves command argv, exit status and stdout/stderr.
The environment differs from repository CI Python 3.12.7 as disclosed above.
No UI packaging checks, online CI or hardware checks were run.

## IR B1 regression evidence

Reviewed code `fe4c4f003e3ed01028eaab02ce651cb3b8f14969`. Reproduction ran
with the new test assertions on that implementation and preserved the four
original failures. The fix was verified on clean code/test commit
`7738593b3ea6e15d82d417b3954e1b6c24756ee4`. Environment was Windows 11 x64,
Python 3.13.5 and pytest 9.2.0. The exact environment, static command output and
firmware hash checks are in `../pr-b-evidence/ir-b1/checks-7738593.json`.

Commands from the clone root used the existing venv and local golden fixtures.
Each pytest invocation saved stdout and stderr to a matching `.log` file.

```powershell
./.venv/Scripts/python.exe -m pytest -q -rs tests/test_udp_session.py -k 'send_backpressure or transaction_deadline' --junitxml=../pr-b-evidence/ir-b1/reproduction.xml
./.venv/Scripts/python.exe -m pytest -q -rs tests/test_udp_session.py -k 'send_backpressure or transaction_deadline' --junitxml=../pr-b-evidence/ir-b1/fixed-working-tree.xml
$env:NN2_GOLDEN_DIR=(Resolve-Path '../pr-a-evidence/golden').Path
$env:QT_QPA_PLATFORM='offscreen'
./.venv/Scripts/python.exe -m pytest -q -rs tests/test_udp_session.py tests/test_control.py --junitxml=../pr-b-evidence/ir-b1/focused-7738593.xml
./.venv/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-b-evidence/ir-b1/full-7738593.xml
git diff --check fe4c4f003e3ed01028eaab02ce651cb3b8f14969 HEAD
./.venv/Scripts/python.exe -m compileall -q src tests
```

| Check | Result |
| --- | --- |
| Reproduction on prior code with new assertions | 4 failed, 74 deselected, exit 1 |
| Fixed working tree, same selection | 4 passed, 74 deselected |
| Focused on `7738593` | 134 passed, 1.09 s |
| Full suite on `7738593` | 531 passed, 3 skipped, 16.88 s, exit 0 |
| Static checks and local firmware-source hash comparison | Passed. All 10 source hashes matched |

The three skips were `tests/ui/test_control_panel.py`, `test_live_view.py` and
`test_main_window.py` at collection due to missing PySide6. The full suite
included golden and recorder/replay regressions. The PR-A PermissionError did
not recur in this run. Socket-double checks exercise send backpressure directly
with a mode-sensitive `sendto`. Tests used synthetic peers. Hardware timing,
Python 3.12.7, UI/build checks and online CI were outside this run.

| File under `../pr-b-evidence/ir-b1/` | SHA-256 |
| --- | --- |
| `reproduction.log` | `d5c34c22c866aeedadc717f6f9dd9dae295609441ed1a78c5ba950c45d68508e` |
| `reproduction.xml` | `4c9b66803aff62c51ef34ab6b3f741dd4d28eb9fd1192247a696abfd6ecced39` |
| `focused-7738593.xml` | `d4bd62ce903b6fe0ea1690073bb1f8d1882bd908c40622c6a15b245448c7de48` |
| `full-7738593.xml` | `90677f4ea2473eb19f6fefee668b33793c42808d23b1933895cd073a7b4f807c` |
| `checks-7738593.json` | `bfed1bf68382fdd23bc2cfbfd5a747238c23385a2d5eed202b2df39e8384e49e` |
