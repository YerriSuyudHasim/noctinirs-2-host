# Stage D4 · handoff

**READY FOR LOCAL INDEPENDENT REVIEW.** Branch `feat/20-host-ui-modern-d4-offline`, local only.

Known limitations and owner decisions:
1. START / STOP moved to a top action bar so they stay visible when the sidebar collapses. Owner to confirm the placement.
2. The pyqtgraph Y grid is replaced by a dashed zero line (performance). Owner to confirm.
3. The 1920×1080 screenshots are resized grabs; offscreen test grabs have no fonts.
4. The exe was only launched (`--self-test` of the old window), not used with a device.
5. 100 Hz stress still costs ~75 % of one core; the display is capped at 25 Hz. Normal 10 Hz replay ≈ 17 %.
6. Six unrelated working-tree files stay untouched and uncommitted: README.md, project.yaml, docs/local-review/pr-c/review.md, docs/local-review/pr-d/review.md, docs/host-roadmap-offline.md, docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md.

Evidence: `..\pr-d4-evidence\` (screenshots, benchmark, logs, full-suite.xml, SHA256SUMS.txt).
