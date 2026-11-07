# Stage C author test evidence

Author and test runner: Codex. Evidence classes are static, host/unit and
socket-loopback simulation. AGENT07 review is pending. All artifacts below
are local editable files in `../pr-c-evidence/` relative to the host clone
root. Keep the logs and JUnit XML together. They are author evidence.

## Environment and commands

Windows 11 x64 (build 26300), existing `.venv`, Python 3.13.5 and pytest 9.2.0.
PySide6, NumPy and pyqtgraph are absent. The firmware contract blobs and the four
golden files were hashed from local Git objects and `../pr-a-evidence/golden`
against `config/reference_pins.json`. All pinned hashes checked matched.
`environment-contract.json` includes package versions, clone and timestamp.

PowerShell from the host clone. Every pytest command used the following two
environment settings:

```powershell
$env:NN2_GOLDEN_DIR=(Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM='offscreen'
```

| Snapshot | Command arguments after `./.venv/Scripts/python.exe -m pytest` | Result |
| --- | --- | --- |
| Offline baseline `5fb8abd842214e316163134740bd8dd772b4e3ce` | `-q -rs --junitxml=../pr-c-evidence/baseline.xml` | 531 passed, 3 skipped in 17.26 s, exit 0 |
| Initial candidate working tree | `tests/test_raw_recording.py tests/test_raw.py tests/test_udp_session.py tests/test_record.py tests/test_record_resilience.py tests/test_record_uint32.py tests/test_golden.py -q -rs --junitxml=../pr-c-evidence/first-candidate.xml` | 311 passed, 1 failed in 20.21 s, exit 1. See [AUTHOR-C01](responses.md). |
| After the fixture sequencing correction, working tree | `tests/test_raw_recording.py tests/test_udp_session.py -q -rs --junitxml=../pr-c-evidence/second-candidate.xml` | 104 passed in 4.98 s, exit 0 |
| Final precommit working tree | `tests/test_raw_recording.py tests/test_udp_session.py tests/test_record_resilience.py -q -rs --junitxml=../pr-c-evidence/precommit.xml` | 134 passed in 9.09 s, exit 0 |
| Exact code/test SHA `eb890b93c17fb6af04b03d641bc92e0806c34e1e` | `-q -rs --junitxml=../pr-c-evidence/exact-full.xml` | **558 passed, 3 skipped** in 21.03 s, exit 0 |

The exact-code run began at 2026-11-07 08:31:25 UTC. Only the approved
unstaged `project.yaml` differed from HEAD. The full suite includes 26 new
RAW recorder cases, 15 existing RAW parser cases, 79 UDP/session cases
(including the new capture integration), 104 OPT2 recorder/replay cases and
90 pinned golden cases. All four required golden files were present and all
golden tests ran. The three skips were UI module collection in
`tests/ui/test_control_panel.py`, `tests/ui/test_live_view.py` and
`tests/ui/test_main_window.py` due to missing PySide6. The individual UI
cases behind those modules did not run.

Static checks on the candidate, all exit 0:

```powershell
git diff --check 5fb8abd HEAD
./.venv/Scripts/python.exe -m compileall -q src tests
./.venv/Scripts/python.exe -m noctinirs_host check-geometry config/optical_geometry_v1.json
```

The geometry check retained `draft_pending_caliper` and its warning. It
verifies the host file, not physical distances or wavelength identity.

**NOT RUN:** pinned Python 3.12.7, Qt UI and build/package checks, other OSes,
firmware C build/tests, online CI, remote freshness, device/bench, HIL, field,
physical power-loss tests and independent reviewer reruns. Injected disk
stall, short write and process exit cover host recovery only. The prior
PR-A recorder `PermissionError` and B residuals N1–N7 remain open.

## Local artifacts

| File | SHA-256 |
| --- | --- |
| `baseline-head.txt` | `5e53d23e9aa2a83e49581187df376bc72818d8c8dfbc5062b5da5e4b085dfae1` |
| `baseline.log` | `999b3f9e2041dc2b1cd7b5cc8866a365d9aa6eddb6572575e588539a2be9513c` |
| `baseline.xml` | `8a5b7187148062d0269d8941a83807c500198759d7faa10428f1ba2a5ffd61e9` |
| `environment-contract.json` | `13e6653080ed6f35eeeaa9dd3d43f0814a6e3ffeed2a81d9f9a44e1f2d6cb638` |
| `first-candidate-head.txt` | `0b9b5758b46dca1aee1d155ff54d82fd4e30dcede40b6abf97d9e82ed0bfb38f` |
| `first-candidate.log` | `02d5b86c92743e2d9b8a732a52e8a66e372c8cb08fc1ef04dfa17e1365414a63` |
| `first-candidate.xml` | `5b98c96f7ba6a9d4bc9ae13b0a35d26722e282cc801f26df05c35b7369e13b24` |
| `second-candidate.log` | `ee37798f91ca25f99eb95f11656fe4c3f32e9b8b7ee3c6a1e55752672451d5f3` |
| `second-candidate.xml` | `d3b321609fcbfe57b37198637dede35ee406ff3605fdb70a591afdf46ffd0155` |
| `precommit.log` | `94d1307077833e18b01f9462c8a910293f1a3a906343501502a3ea20aa5083ce` |
| `precommit.xml` | `0a7b6b6ac58a7ad4da3fbfc79974a5933cd25212185f3111e4818754b6243da9` |
| `exact-head.txt` | `1b23da0b95d79a0970c86e822023aa77b952907478c51596993783724193d70f` |
| `exact-full.log` | `c88dad91b22ce46ec1f34e8e7867c3495b306f4e1593738e5f75dabf5e2aa373` |
| `exact-full.xml` | `5b4f2e0a0d0f53c20c3b47070d2dbd9365c6988bc39ba5b24b03a71eb695dcf9` |
| `static.log` | `a985ecf6159c558af0c9437161694c9bfcc91a37dae3d78841806fc21773341e` |
