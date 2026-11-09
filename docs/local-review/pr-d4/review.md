# Stage D4 · review

**AGENT07 independent review: PENDING.** The author (Claude) cannot review this change.

Review notes for the reviewer:
- This branch sits on the unreviewed battery, D2 and D3 changes.
- Check that `git diff 7c1615d` touches only `src/noctinirs_host_ui/{engineering,preview_panel,widgets}.py`, the new `design/` package, tests and docs — no processing, protocol, battery or recording code.
- Judge the screenshots yourself (`..\pr-d4-evidence\screenshots\`); offscreen test grabs have no fonts and are not evidence.
- Verify the three-row rule and the channel order on a real window.
