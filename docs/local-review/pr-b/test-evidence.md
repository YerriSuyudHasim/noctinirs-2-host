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
