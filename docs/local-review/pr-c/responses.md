# Stage C author responses and early failures

Codex (implementation author). No independent review findings have been
filed yet. Append responses referencing the finding ID, candidate/fix SHA,
changed paths and evidence. Preserve earlier results.

## AUTHOR-C01: early fake-peer teardown setup failure

The first focused candidate run returned **311 passed, 1 failed**. The new
loopback test requested its final STATUS immediately after the fake peer
ended its lease, before the B controller observed that transition. The first
request still carried the stale session ID, so the end snapshot correctly
had no successful STATUS observation. The original failure and traceback
remain in `pr-c-evidence/first-candidate.log` and `.xml` outside the clone.

The test now ticks the controller, asserts that it observed STOPPED with no
owned session, and then requires successful end diagnostics. The correction
uses the existing B lifecycle and preserves the final STATUS assertion.
The following focused run passed, with separate files for the rerun.

The B residuals N1–N7 and the PR-A recorder `PermissionError` remain in the
[B review](../pr-b/review.md). Stage C does not close those items.
