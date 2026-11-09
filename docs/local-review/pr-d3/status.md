# Stage D3 · local status

**READY FOR LOCAL INDEPENDENT REVIEW. AGENT07 review PENDING. Not merged, not pushed.**

Claude (Sonnet 5.5, author) revised the preview after the 9 Nov bench (record_1) and rebuilt the
Live window like the old UI. Everything was offline: no device, bench or acquisition, no
GitHub, CI, firmware or protocol change.

| Item | Local identity |
| --- | --- |
| Branch | `feat/20-host-preview-d3-offline` (new, cut from `3a343a6`) |
| **Depends on** | the battery change (code `d0b3d2b`, notes `3a343a6`) and D2 (code `c756a73`, notes `1c92772`). None of the three is reviewed yet. |
| D3 code/tests | `5a9402d39412ff419cd1116c195a5ab07da19697` |
| D3 notes | separate commit containing this folder |
| Input | the Owner's D3 brief and `BENCH_PREVIEW_GAP_ANALYSIS_2026-11-09.md`; `C:\Users\doeki\Documents\noctinirs\2026-11-09\record_1.raw.bin` read only (not changed or moved) |
| Evidence class | static, host/unit, simulation and replay of a recorded archive. No hardware. |

## Result

| Request | Status |
| --- | --- |
| A1 leak is a per-AIN warning, invalid only when large | Done |
| A2 online sigma, invalid only for `I ≤ 0` or not finite, `LOW_SIGNAL` warning | Done |
| A3 `AMBIENT_HIGH`, near-rail and flat-top `SATURATED` | Done |
| A4 uncorrected long channel instead of a gap when the short channel is unusable | Done |
| A5 invalid counts after SSR and MBLL, warnings counted apart | Done |
| A6 packet-loss counters in the snapshot and in the Live panel | Done |
| B Live layout: narrow settings panel left, large graphs right, detail on click, quality markers | Done |
| C window sizing: navigation row visible at 1280 × 720, 1366 × 768, maximized, fullscreen | Done and tested |
| Replay of record_1 before and after | Long channels 42.8 % → 74.1 % shown; with SCR 5 mm 0 % → 74.1 % shown (10.4 % of frames uncorrected). See [test evidence](test-evidence.md). |
| Full suite, compileall, exe, screenshots | **735 passed, 0 skipped, 0 failed.** Exe built and launched. Screenshots from a replay with the real Windows platform. |

The target "about 77 %" from the analysis was not reached exactly: 74.1 % of long-channel frames
are shown. The remaining gaps are not only baseline, lost packets and the two probe events: some
frames still hit `LEAK_SUSPECT` and `NO_LED_STEP` (details in the test evidence).

The preview stays **not validated** and is not science data. Pre-existing working-tree files not
in either D3 commit and not modified: `README.md`, `project.yaml`,
`docs/local-review/pr-c/review.md`, `docs/local-review/pr-d/review.md` (modified) and
`docs/host-roadmap-offline.md`, `docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md`
(untracked).

- [Implementation](implementation.md)
- [Test evidence](test-evidence.md)
- [Reviewer record (PENDING)](review.md)
- [Author responses](responses.md)
- [AGENT07 handoff](handoff.md)
