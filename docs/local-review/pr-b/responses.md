# Author responses (append-only)

Author: Codex / GPT-6, session `codex-pr-b-offline-20261107`.
Independent review has not occurred. No reviewer finding is closed or approved
by this author. Future entries must cite the review finding ID and exact fix SHA,
changed files and test evidence; keep reviewer observations in review.md.

## AUTHOR-001 â€” Windows oversize receive exception

Source: author socket-loopback run, not an independent reviewer finding.
Initial result: 1 failed / 59 passed, retained in `../pr-b-evidence/udp-first.log`.
The lifecycle was an owned STARTING session, heartbeat request, fake peer sending
a 65,500-byte datagram followed by a valid reply. Winsock consumed the datagram
and raised 10040 because recvfrom's bounded buffer was smaller. The controller
treated this as link IO_ERROR, so the valid reply could not complete the request.

Change: UDPTransport.receive counts/drops only EMSGSIZE/WSAEMSGSIZE and continues
within its existing packet/deadline bounds. Other socket errors remain visible
and trigger cleanup. No sleep, skip or assertion weakening.

Evidence: focused-precommit 137 passed; candidate-udp 74 passed. Exact code/final
test references are appended to test-evidence.md after committing.

## Standing PR-A residual

The recorder PermissionError is unresolved/nonblocking per Owner direction.
No proposed fix, hidden skip, delay or changed recorder assertion is included.
This is a carried limitation, not an author claim to have resolved a finding.

## Final author evidence attachment

AUTHOR-001 correction is included in `fe4c4f003e3ed01028eaab02ce651cb3b8f14969`.
The exact-commit full run passed 528 tests (including all 75 UDP tests); three
UI modules were skipped for missing PySide6. The failure log remains preserved,
with SHA-256 in test-evidence.md. No independent finding has been received.
No code/test change followed this candidate; any future fix requires re-review.
