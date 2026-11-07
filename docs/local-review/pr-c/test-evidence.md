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

## C-N1 follow-up author evidence

The original review covers code/test SHA `eb890b93c17fb6af04b03d641bc92e0806c34e1e`.
The fixed candidate is `9406521d018ea71e60180b851e39a19c252908ef`, based on
notes HEAD `9b558d5045f529b5da26b9fea9e3311029e57d79`. These are Codex author
runs. Independent re-review is pending. The original evidence table above
continues to describe the original Stage C candidate.

Windows 11 x64 build 26300, existing `.venv`, Python 3.13.5 and pytest 9.2.0.
All four local golden files matched their configured SHA-256 pins. Evidence
is in `../pr-c-evidence/c-n1/` from the clone root. `environment.json` records
the versions and hashes. `before-head.txt` records that the production source
matched the reviewed SHA when running the new reproduction. `exact-head.txt`
records the fix SHA, UTC times and the two preexisting unstaged documentation
changes at the start of the exact-code runs.

PowerShell from the clone root used `$out='../pr-c-evidence/c-n1'` for logs
and JUnit XML. The regression and full runs set
`NN2_GOLDEN_DIR` to the resolved `../pr-a-evidence/golden` path and
`QT_QPA_PLATFORM` to `offscreen`.

| Run | Arguments after `./.venv/Scripts/python.exe -m pytest` | Result |
| --- | --- | --- |
| Reproduction before the fix | `tests/test_raw_recording.py::test_active_metadata_keeps_one_packet_accounting_cut -q --junitxml="$out/before.xml"` | 1 failed, exit 1. The paused live snapshot contained 1 observed, 1 enqueued and 2 persisted. |
| Same reproduction with the fix in the working tree | `tests/test_raw_recording.py::test_active_metadata_keeps_one_packet_accounting_cut -q --junitxml="$out/after-repro.xml"` | 1 passed, exit 0. Active 1/1/1 and clean-close 2/2/2. |
| Focused working tree | `tests/test_raw_recording.py tests/test_record_resilience.py -q -rs --junitxml="$out/targeted.xml"` | 57 passed, exit 0. |
| Exact fix SHA, regression | `tests/test_raw_recording.py tests/test_raw.py tests/test_record.py tests/test_record_resilience.py tests/test_record_uint32.py tests/test_udp_session.py tests/test_golden.py -q -rs --junitxml="$out/regression.xml"` | 316 passed, 0 skipped, exit 0. |
| Exact fix SHA, full suite | `-q -rs --junitxml="$out/exact-full.xml"` | **560 passed, 3 skipped**, exit 0. |

The regression ran the RAW byte-exact capture/replay and continuity checks,
the failure and timeout accounting tests, 104 OPT2 recorder/replay cases,
79 UDP/session cases, and all 90 local golden cases. Tests use synthetic
data, injected disk behavior and socket loopback. `git diff --check
9b558d5045f529b5da26b9fea9e3311029e57d79 HEAD` and
`./.venv/Scripts/python.exe -m compileall -q src tests` returned 0. The full
suite skipped collection of `tests/ui/test_control_panel.py`,
`tests/ui/test_live_view.py` and `tests/ui/test_main_window.py` because PySide6
is absent.

**NOT RUN:** Python 3.12.7, Qt UI and packaging checks, other OSes, firmware C,
online CI, device/bench, HIL, field, physical power-loss tests and independent
re-review runs. The PR-A recorder `PermissionError` remains unresolved.
No other residual was addressed by this fix.

| C-N1 artifact | SHA-256 |
| --- | --- |
| `before-head.txt` | `4ae7ba45b5109c9f9504cbf6c702ae4a06c1b191ccdc96fe47cc2e9e16d945d5` |
| `repro-test.diff` | `11ca9ac67e177ded3723d94d154b5c78dcebb42209e9084fb44667269d941655` |
| `before.log` | `074956e20f9bb610578092b2b73b7e94ecb15bf22a3243e937f2b551ca993cf5` |
| `before.xml` | `b8977aff874090828d77606e426d87eafaca77fcf9cbadbb37606f956a9d92a7` |
| `after-repro.log` | `023471b8bec00a76a9977e27f760f63f1335def4250e63adb75ca7deec3987fb` |
| `after-repro.xml` | `f73a250f123819db085dcae6569a3fefa936561d8ea756f0a1dc19a9f92377a5` |
| `targeted.log` | `31aa3eb8064004bd31823f768013c7fa251c3cf7c1d53f5439f86dbff115ccaa` |
| `targeted.xml` | `9f739d98b46784cbc10f6db44810a26bc1923f21d905c304ca9592903ebf3424` |
| `exact-head.txt` | `c2041546d7e3c0d6770e0ebf7573ca70c764b7c7a0fe979a09ff6cd71511a89c` |
| `environment.json` | `0ba7a6887f9fe2c39186febf5f3f424d8ff0f79df79b5d3ec37b687dd2487657` |
| `regression.log` | `e81639b2864676ddfba508428ebfb4778d42dca7fe36f92ced1c98d92dbbba77` |
| `regression.xml` | `a1ee0c6d8eaeee7458dd76842b190d1ca0a0dde4e85d45f69982069a3fd85504` |
| `exact-full.log` | `152399bef8f5bca5595b7ed932f4ce6578f06276f942fbfe378ac3df83ffd5fe` |
| `exact-full.xml` | `2ea8f066875d0c997ae0019ff77b7d1ceb73c22782cf526c09acfd2daf085bb6` |
| `static.log` | `327b84ae4180404be961cf94c4f689274a72113952ba33b4e520cf6045f13a42` |
| `fix.diff` | `dea7185fe97d3665e7b4d92f1b00845942e97963189fe87cddfd583665f9f53d` |
