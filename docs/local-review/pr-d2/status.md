# Stage D2 · local status

**READY FOR LOCAL INDEPENDENT REVIEW. AGENT07 review PENDING. Not merged, not pushed.**

Claude (Sonnet 5.5, author) implemented the Owner's D2 revision of the Stage D
desktop after the 9 Nov bench feedback. Everything was done offline. No device,
bench, GitHub, CI, firmware or protocol change was touched.

| Item | Local identity |
| --- | --- |
| Branch | `feat/20-engineering-ui-d2-offline` (new, cut from D HEAD `b9bf025`) |
| D code baseline (AGENT07 APPROVE, D-G1 closed) | `b4427e50e968e6905a8b24e6bc8c573a206942c3` |
| D2 code/tests | `c756a73165ec212df380607ea555f473a7edeb63` |
| D2 notes | separate commit containing this folder |
| Bench, HIL, firmware execution, CI | NOT RUN |

Evidence class: static, host/unit and simulation (local RAW fake). A fake
device is simulation, not a device test.

## What the Owner asked, and where it stands

| Owner request | Result |
| --- | --- |
| Default folder `Documents\noctinirs\<YYYY-MM-DD>`, created automatically | Done. Created at START. |
| File `record_X`, X = highest in folder + 1, no overwrite | Done. Existing files are never touched. |
| Browse button, path box not truncated | Done. The path box wraps and grows. |
| Long INFO text not truncated | Done. One INFO field per line in a wrapping box. |
| Fewer screens: Welcome, staged Setup, Live | Done. 3 screens, 4 setup steps, Back/Continue keep input. |
| Live does not auto-START | Done. Tested. |
| 16 sweep plots, CH1–CH8 left, CH9–CH16 right, shorts SC1L/SC2L/SC1R/SC2R | Done. |
| Hb / OD / intensity modes, channel detail `CHn_I735` … | Done. |
| Legacy chain: median baseline, `ln`, 2×2 MBLL, ε/DPF, S&W DPF with warning | Done in a UI-independent core. |
| Short separation off / 5 / 8 / both, OLS/Ridge on OD | Done. |
| Live settings change and baseline reset | Done. |
| Safety rules (labels, INVALID = gap, no hard-coded quality, no UI constants) | Done. See [implementation](implementation.md). |
| Stage D suite rerun, PYTHONPATH fix | Done. 672 passed, 0 skipped. |
| Exe build and screenshots | Exe built and launched. Fake-run screenshots come from the source run of the same code. See limits. |

Four pre-existing working-tree files remain outside both commits and were not
modified: `README.md`, `project.yaml`, `docs/local-review/pr-c/review.md`,
`docs/local-review/pr-d/review.md`. Two untracked files also remain:
`docs/host-roadmap-offline.md` and
`docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md`.

The preview is **not validated**. It must not be described as validated or as
science data.

- [Implementation](implementation.md)
- [Test evidence](test-evidence.md)
- [Reviewer record (PENDING)](review.md)
- [Author responses](responses.md)
- [AGENT07 handoff](handoff.md)
