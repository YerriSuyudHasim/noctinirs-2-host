# Offline Stage C handoff to AGENT07

From Codex, implementation author. To AGENT07 (Independent Reviewer / Grok
Bot), coordinated by Nori under the Owner's Stage C assignment.

**READY FOR LOCAL INDEPENDENT REVIEW.** The code/test candidate is
`eb890b93c17fb6af04b03d641bc92e0806c34e1e`, based on
`5fb8abd842214e316163134740bd8dd772b4e3ce` in
`feat/20-raw-recorder-offline`. `git log -2 --oneline` identifies the separate
notes commit. The Owner-approved, unstaged `project.yaml` is separate from
both commits. Review remains PENDING.

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
