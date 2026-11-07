# Stage D author evidence

Codex (GPT-6, author and test runner, agent /root). Exact code/test candidate
`b4427e50e968e6905a8b24e6bc8c573a206942c3`. Evidence classes are static,
host/unit and simulation. Artifacts are in `../pr-d-evidence/` relative to
the host clone root. See `SHA256SUMS.txt` there. These are editable local
author logs and JUnit reports.

## Commands and results

Windows 11 x64 build 26300, existing `.venv` with Python 3.13.5 and pytest
9.2.0. PySide6, shiboken6, NumPy, pyqtgraph and PyInstaller were unavailable.
The exact-head full run occurred on 2026-11-07, 19:08:00–19:08:24 KST. The
four golden files came from the sibling local evidence directory and were
hash-checked by the suite. The ten control-profile blobs also matched the pins
when read from local firmware Git objects. See `pinned-contract.json`.

PowerShell from the host clone:

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
./.venv/Scripts/python.exe -m pytest -q -rs --junitxml=../pr-d-evidence/exact-full.xml
./.venv/Scripts/python.exe -m pytest tests/test_uncertain_start.py -v --junitxml=../pr-d-evidence/lost-start-after.xml
./.venv/Scripts/python.exe -m pytest tests/ui/test_engineering.py -q -rs --junitxml=../pr-d-evidence/ui-blocked.xml
./.venv/Scripts/python.exe -m compileall -q src tests packaging
$env:PYTHONPATH = 'src'
./.venv/Scripts/python.exe -m noctinirs_host check-geometry config/optical_geometry_v1.json
git diff HEAD^ HEAD --check  # Run while HEAD is the code commit.
```

| Run | Outcome | Evidence stem |
| --- | --- | --- |
| Baseline full suite | 560 passed, 3 Qt module collection skips, exit 0 | `baseline` |
| Old B control plus new lost-START test | 1 expected failure, exit 1 | `lost-start-before` |
| Initial recovery + existing session/control suite | 143 passed, 1 failed, exit 1 | `lost-start-fix-initial` |
| Initial combined focused suite | 157 passed, 1 failed, exit 1 | `stage-d-targeted-first` |
| Follow-up focused suite before additional coverage | 158 passed, exit 0 | `stage-d-targeted-second` |
| Precommit full suite | 584 passed, 4 Qt module collection skips, exit 0 | `precommit-full` |
| Final precommit targeted suite | 159 passed, exit 0 | `candidate-targeted` |
| Exact candidate full suite | 584 passed, 4 Qt module collection skips, exit 0 | `exact-full` |
| Exact candidate deterministic START suite | 10 passed, exit 0 | `lost-start-after` |
| Exact candidate Qt-only collection | BLOCKED. 1 skipped module, zero UI tests executed, exit 5 | `ui-blocked` |
| Candidate static checks | Diff check, compileall and geometry CLI exit 0 | `static.log` |

The focused suite comprised `tests/test_uncertain_start.py`,
`tests/test_engineering_run.py`, `tests/test_udp_session.py` and
`tests/test_control.py`. The exact full run includes 14 Stage D non-Qt
workflow cases, 79 UDP/session, 28 RAW recorder including C-N1, 15 RAW
parser, 90 OPT2, 90 local golden and 104 retained recorder,
resilience and uint32 cases. See [responses](responses.md) for initial
candidate failures and corrections.

## Lost START failure and fixed outcome

Before changing B production code, the author ran
`pytest tests/test_uncertain_start.py -q` with only the new reproduction
test in that module. The pre-fix branch and working tree are in
`lost-start-before-head.txt`. The deterministic fake accepted START once
and replayed its cached response for byte-identical retries. The host received
zero START replies and kept session ID zero. Five STATUS sends at fake times
102, 104, 106, 108 and 110 seconds refreshed the owner endpoint. The fake
reported STARTING, retained owner A and recorded zero lease expirations.

The same reproduction passes after the fix. It observes zero host
transmissions after the original START, one fake lease expiration and a
latched host outcome until a correlated explicit STATUS proves terminal
state. The rest of the deterministic suite exercises late ACK/RAW rejection,
the original reply window, exact in-flight retry bytes, recovery timeout and
rate limiting, SAFE_OFF success, lost SAFE_OFF ACK, firmware rejection,
reboot, isolation from another owner, normal heartbeat and STOP. The fake
models the audited lease, peer identity and cached START response subset.
It runs in process with a fake clock and does not exercise the firmware
binary. Socket-loopback tests separately exercise the actual UDP transport,
file lifecycle and byte-exact replay. A deliberately blocked RAW write
allows heartbeat before release, then produces an incomplete writer result.

## UI and unrun checks

Qt launch, Qt responsiveness, visual QA, screenshots, and packaged build
are **BLOCKED / NOT RUN** in the available environment. The three new Qt tests
cover wizard navigation, fake-device capture, responsive timeout recovery,
and distinct device/recorder states when the dependencies are available.
To capture fake screenshots then, set
`$env:NN2_D_SCREENSHOTS = (Resolve-Path ../pr-d-evidence).Path` before the
Qt-only run. The optional images show simulation only.

Python 3.12.7, other platforms, firmware execution, hardware and physical LED
measurements, bench, HIL, CI and release checks were NOT RUN.
