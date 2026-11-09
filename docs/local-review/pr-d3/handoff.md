# Stage D3 handoff to AGENT07

Author: Claude (Sonnet 5.5). The reviewer must not be from the Claude family.
Status: **READY FOR LOCAL INDEPENDENT REVIEW. Review PENDING.**

## Identity

| Item | Value |
| --- | --- |
| Clone | `...\kerjakan-pr-a-pada-repo-yerrisuyudhasim\noctinirs-2-host` |
| Branch | `feat/20-host-preview-d3-offline` |
| Base | `3a343a6` (battery notes) over battery code `d0b3d2b`, D2 notes `1c92772`, D2 code `c756a73`. **None of these is reviewed.** |
| D3 code/tests | `5a9402d39412ff419cd1116c195a5ab07da19697` |
| D3 notes | the commit that adds this folder |
| Evidence | `../pr-d3-evidence/` with `SHA256SUMS.txt` (outside Git) |
| Input data | `C:\Users\doeki\Documents\noctinirs\2026-11-09\record_1.raw.bin` (read only, unchanged) |

## Reproduce

```powershell
$env:NN2_GOLDEN_DIR = (Resolve-Path ../pr-a-evidence/golden).Path
$env:QT_QPA_PLATFORM = 'offscreen'
../venv-stageD-ui/Scripts/python.exe -m pytest -q -rs          # 735 passed, about 10 minutes
../venv-stageD-ui/Scripts/python.exe docs/local-review/pr-d3/replay_preview.py `
  --src src --raw <path>\record_1.raw.bin --ssr off
```

For the "before" numbers extract `3a343a6` (`git archive 3a343a6 src config`) and pass its `src`
as `--src`.

## Limits to keep in mind

1. All quality constants (`preview/quality.py`) are host-chosen and were tuned on one bright-room
   recording that the bench report calls not valid as an optical baseline. The preview is not
   validated and not science data.
2. The replay result (74.1 % of long-channel frames shown) is below the analysis estimate
   (about 77 %). The remaining gaps include `LEAK_SUSPECT` and `NO_LED_STEP` frames.
3. A channel with no signal can appear as a flagged `LOW_SIGNAL` value (the brief says only
   `I ≤ 0` is invalid). The flag is the only guard.
4. The geometry is unchanged and still draft; the 5/8 mm short-channel labels are unverified
   (SC2 8 mm is at the ADC rail in 89 % of the slots in the recording).
5. ON/OFF still come from the scheduled phase classes, not a device-verified LED state.
6. The exe was only launched. The screenshots come from a replay run of the source with the real
   Windows Qt platform; the screenshots written by the tests use the font-less `offscreen`
   platform.
7. No device, bench, HIL, CI or second recording was used.

## Working tree left alone

`README.md`, `project.yaml`, `docs/local-review/pr-c/review.md`,
`docs/local-review/pr-d/review.md` (modified) and `docs/host-roadmap-offline.md`,
`docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md` (untracked) are in neither commit.

## Suggested review order

`preview/quality.py` → `preview/slots.py` → `preview/pipeline.py` (`_assess_slot`, `_intensity`,
`_finish`, `_count_final`, `_count_loss`) → `engineering.py` (loss in the snapshot) →
`ui/preview_panel.py` → `ui/engineering.py` (`_live`, window sizing) → `ui/widgets.py` →
tests → replay and screenshot scripts.
