# Battery indicator · handoff to AGENT07

Author: Claude (Sonnet 5.5). The reviewer must not be from the Claude family.
Status: **READY FOR LOCAL INDEPENDENT REVIEW. Review PENDING.**

## Identity

| Item | Value |
| --- | --- |
| Clone | `...\kerjakan-pr-a-pada-repo-yerrisuyudhasim\noctinirs-2-host` |
| Branch | `feat/20-host-battery-offline` |
| Base | `1c92772` (D2 notes) over D2 code `c756a73`. **D2 is not reviewed yet.** |
| Battery code/tests | `d0b3d2b59099f73e4ec5b30b140fbec490abc369` |
| Battery notes | the commit that adds this folder |
| Firmware pin for 0x0033 | `519a96a26ede6517595d4f8a87cd64ab6b7a6fa5` (`feat/local-v4-battery-refresh`), ADR-0004 section 14, wire ADR0004/1 |
| Not changed | `FIRMWARE_PIN` `bd880f5…` used by the RAW recording metadata |
| Evidence | `../pr-battery-evidence/` with `SHA256SUMS.txt` (outside Git) |

## Reproduce

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs
```

Expected: 720 passed, 0 skipped (about 6 minutes). Battery only:
`pytest tests/test_battery.py` (42) and `pytest tests/ui/test_engineering.py -k battery` (6).

## Limits to keep in mind

1. Simulation and unit evidence only. The flashed 519a96a build was never contacted. The app
   SHA-256 in the assignment is an Owner report and is not checked by the host.
2. The golden bytes come from the reference client at 519a96a, not from a device capture.
3. The scanning behaviour (no refresh, age keeps growing) and the idle and post-STOP refresh
   timing come from the contract. The host schedule is built on them and was not observed.
4. No battery capability key exists in INFO at 519a96a, so support is learned from the first
   reply. No charging state exists, so none is shown.
5. The 60 s scan interval, 7 s post-STOP delay, 0.25 s timeout and 20 % warning are host-chosen
   constants (named and commented).
6. One D2 behaviour was touched (gap marker on explicit disconnect); see
   [responses](responses.md).
7. The exe was only launched; fake-run screenshots are from the source run.

## Working tree left alone

`README.md`, `project.yaml`, `docs/local-review/pr-c/review.md`,
`docs/local-review/pr-d/review.md` (modified) and `docs/host-roadmap-offline.md`,
`docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md` (untracked) are in neither commit.

## Suggested review order

`control/protocol.py` (battery parts) → `control/battery.py` → `control/session.py`
(`query_battery`, `_battery_tick`, `_request`, `_accept`, `_set_state`) →
`control/simulator.py`, `engineering_fake.py` → `engineering.py` (snapshot, log, gap marker) →
`ui/battery_text.py`, `ui/engineering.py` → `tests/test_battery.py`, the fixture, the Qt tests.
