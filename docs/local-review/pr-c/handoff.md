# Offline Stage C handoff to AGENT07

From Codex, implementation author. To AGENT07 (Independent Reviewer / Grok
Bot), coordinated by Nori under the Owner's Stage C assignment.

## C-N1 re-review

**FIX IMPLEMENTED / PENDING RE-REVIEW.** AGENT07's technical APPROVE and C-N1
finding cover the original Stage C SHA below. Re-review
`9406521d018ea71e60180b851e39a19c252908ef` on
`feat/20-raw-recorder-offline`. `git show --stat 9406521` lists the fix scope.
The [author response](responses.md) describes the snapshot invariant and the
deterministic reproduction. The [test evidence](test-evidence.md) keeps the
original failure and exact-SHA author results.

Verify that the RAW packet balance is consistent while capture is active and
after queue overflow, a failed write, timeout/recovery and clean close. Check
that the receiver and worker publish accounting under the same in-memory
lock and release it before disk I/O or worker joins. The committed regression
in `tests/test_raw_recording.py` forces the original interleaving. Record the
fix SHA and any independent rerun in the reviewer record.

The approved `project.yaml` and AGENT07's original addition to `review.md`
remain separate unstaged changes. The follow-up notes have their own commit.
Keep the earlier verdict and the B and PR-A residuals as historical records.

## Original Stage C handoff (historical)

The original code/test candidate was
`eb890b93c17fb6af04b03d641bc92e0806c34e1e`, based on
`5fb8abd842214e316163134740bd8dd772b4e3ce` in
`feat/20-raw-recorder-offline`, with separate notes at
`9b558d5045f529b5da26b9fea9e3311029e57d79`.

| Acceptance | Author evidence |
| --- | --- |
| Original RAW datagram, flags, IDs and receive time survive capture/replay | Synthetic packets and UDP fake-peer recording through the existing session router, including RAW during a control transaction. |
| Layout, counts and phase validation | Int24 endpoints, 1 and 100 samples, invalid counts, phase/slot combinations and flags. |
| Continuity and clocks | u32 wrap, gaps and late recovery, duplicate/overlap/reorder, u16 epoch change, session/boot change, ambiguous order, backsteps, timestamp flags and ADC rate separation. |
| Writer and provenance | Blocking/slow fake disk, queue overflow before and after recovery, short writes, exclusive filenames, incomplete or malformed metadata, process exit and truncated archive, operator SHA label and explicit firmware INFO/STATUS context. |
| Retained paths | UDP/session B, OPT2 recorder/replay and local pinned golden fixtures in the full author run. |

Suggested local review procedure:

1. Confirm full code SHA, offline base, clean `src`/`tests` relative to the
   candidate and notes-only delta. Use `git diff 5fb8abd eb890b9` for scope.
2. Read [implementation](implementation.md), [RAW format](../../raw-recording-format.md)
   and [test evidence](test-evidence.md). Verify the raw evidence hashes and
   inspect the local pinned firmware object. Keep original failures.
3. If independently rerunning, use the already present environment and golden
   fixtures, a separate output folder and loopback only. Record any unavailable
   checks. Enter findings and the exact reviewed code SHA in [review](review.md).

No GitHub, API, fetch/pull/push, dependency or fixture downloads, hardware,
merge or Stage D is within this handoff. The Owner retains integration and
hardware authority. B and PR-A residuals remain open.
