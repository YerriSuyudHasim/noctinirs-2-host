# Stage D4 · local status

**READY FOR LOCAL INDEPENDENT REVIEW. AGENT07 review PENDING. Not merged, not pushed.**

Claude (Sonnet 5.5, author) gave the host a modern look (light/dark theme, smooth motion) and
rebuilt the Live screen as stacked rows. Everything was offline: fake device and replay of a
recorded archive only. No device, bench, acquisition, GitHub, CI, firmware or protocol change.

| Item | Local identity |
| --- | --- |
| Branch | `feat/20-host-ui-modern-d4-offline` (new, cut from D3 `7c1615d`) |
| **Depends on** | battery change, D2 and D3. None of the three is reviewed yet. |
| D4 code/tests | see the commit list in [handoff.md](handoff.md) |
| D4 notes | separate commit containing this folder |
| Evidence class | static, host/unit, simulation, replay. No hardware. |

## Result

| Request | Status |
| --- | --- |
| Design tokens → generated QSS, light (default) and dark | Done (`noctinirs_host_ui/design/`) |
| Live theme switch, saved in QSettings, plots follow the theme | Done and tested |
| Smooth subtle motion, "reduce motion" setting, plots never animated | Done and tested |
| Live: collapsible settings sidebar left, plots right | Done |
| START / STOP / navigation visible at every window size | Done and tested (moved to a top action bar) |
| Stacked rows, HbO + HbR per row, exactly 3 visible, scroll for the rest | Done and tested |
| Order: left hemisphere then right, dividers, linked X axis | Done and tested |
| Per-row autoscale + optional shared lock, smooth scroll, 25 Hz refresh, peak downsampling, off-screen rows skipped | Done, benchmarked |
| Welcome + 4 setup steps themed, text truncation fixed | Done |
| Folder field with Browse, default `Documents\noctinirs<date>` | Already present; kept and tested |
| "Unvalidated preview, not scientific data" label visible on Live | Kept |
| Processing, protocol, battery, recording code | **Unchanged** (presentation only) |

See [implementation](implementation.md), [test evidence](test-evidence.md), [review](review.md),
[responses](responses.md) and [handoff](handoff.md).
