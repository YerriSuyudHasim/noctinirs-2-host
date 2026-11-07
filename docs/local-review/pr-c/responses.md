# Stage C author responses and early failures

Codex (implementation author). Append responses with the finding ID,
candidate and fix SHAs, changed paths, and evidence. Preserve earlier results.

## C-N1 (P3) | FIX IMPLEMENTED / PENDING RE-REVIEW

AGENT07's technical APPROVE and C-N1 finding apply to the original code/test
SHA `eb890b93c17fb6af04b03d641bc92e0806c34e1e`. The author fix is
`9406521d018ea71e60180b851e39a19c252908ef`. AGENT07's original review remains
in `review.md` as the reviewer's separate unstaged addition.

The deterministic test pauses a live metadata reader immediately after it
releases the input lock, lets the normal receiver and worker write packet 2,
and then resumes the reader. The original writer returned observed 1, queued
1, and persisted 2. The same test now gets 1/1/1 from the paused live snapshot
and 2/2/2 after clean close. Both logs are retained in the [test evidence](test-evidence.md).

The recorder now owns the producer and writer RAW accounting. The worker
publishes binary continuity and CSV row counts under the input lock after each
corresponding flush. Metadata copies those counters, the aggregate queue drop
count, and close/error state while holding the same lock. It derives the pending
balance by subtraction. Binary and CSV I/O, syncing, hashing, sidecar
serialization, and worker joins execute outside that lock. Event and session
bookkeeping still uses its own lock. The archive bytes and replay are unchanged.

Accounting still distinguishes queue drops, binary records confirmed by the
writer, and flushed CSV rows. The new tests cover active, failed, timed-out,
recovered, and clean-close states, including a feeder progressing while the
sidecar serializer is stalled. The previous independent verdict remains on
the original SHA. C-N1 awaits re-review on the fix SHA.

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
