# Stage D2 handoff to AGENT07

Author: Claude (Sonnet 5.5). Reviewer must not be from the Claude family.
Status: **READY FOR LOCAL INDEPENDENT REVIEW. Review PENDING.**

## Identity

| Item | Value |
| --- | --- |
| Clone | `...\kerjakan-pr-a-pada-repo-yerrisuyudhasim\noctinirs-2-host` |
| Branch | `feat/20-engineering-ui-d2-offline` |
| Base | `b9bf025` (D notes) over D code `b4427e5` |
| D2 code/tests | `c756a73165ec212df380607ea555f473a7edeb63` |
| D2 notes | the commit that adds this folder (see `git log`) |
| Evidence | `../pr-d2-evidence/` with `SHA256SUMS.txt` (outside Git) |

## Reproduce

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs
```

Expected: 672 passed, 0 skipped (about 4 minutes, Qt tests included).

## Limits the reviewer must keep in mind

1. **LED evidence is not device-verified.** RAW carries the scheduled phase
   class only. The preview treats `mean(ON_STABLE) − mean(OFF_STABLE)` above a
   provisional threshold as step evidence. It never calls a sample "verified
   LED-ON".
2. The thresholds (step and leak, 50 counts) are the author's placeholders.
3. Geometry is `draft_pending_caliper`; ε, DPF and the 735/850 labels are
   unverified. The UI says so; no value is labelled validated.
4. Cycle assembly assumes one pass over slots 0..19 per optical frame. It was
   built from the contract text and the fake, not from Batch 5A traffic.
5. The x axis is host receive time of the packet carrying the slot.
6. The exe was launched only. The fake-run screenshots are from the source run.
   Offscreen screenshots saved by the tests have no fonts.
7. No hardware, bench, HIL, CI, GitHub or firmware interaction. The Batch 5A
   INFO text was only imitated in a test string.
8. Stage B N1–N7 residuals and the PR-A recorder PermissionError remain open and
   were not touched.

## Pre-existing working tree left alone

`README.md`, `project.yaml`, `docs/local-review/pr-c/review.md`,
`docs/local-review/pr-d/review.md` (modified) and
`docs/host-roadmap-offline.md`,
`docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md` (untracked) are
not in either D2 commit.

## Suggested review order

`preview/slots.py` → `preview/pipeline.py` → `preview/mbll.py`, `od.py`,
`dpf.py`, `shortsep.py` → `engineering.py` (`start_recorded`, `_poll_disk`,
`_stop_recorder`) → `control/engineering_fake.py` → UI
(`preview_panel.py`, `engineering.py`) → tests.
