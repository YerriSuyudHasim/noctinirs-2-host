# Independent local review record

AGENT07 reviewed `fe4c4f003e3ed01028eaab02ce651cb3b8f14969` and raised B1/P2,
as relayed by the Owner on 2026-11-07. This author transcription records the
finding from the Owner's message. The reviewer can append their session metadata
and original evidence here.

**B1 is open and awaiting AGENT07 re-review** on code/test commit
`7738593b3ea6e15d82d417b3954e1b6c24756ee4`. The author response and local
evidence are recorded separately in responses.md and test-evidence.md.

## Reviewer entry requirements

Append the reviewer name/model, distinct agent/session identity, assignment,
review workspace, author/coauthor identities and confirmation of no candidate
code/test authorship. Record full exact SHA, reviewed diff scope and environment.
For each finding use ID, severity, file/line, observation and evidence. State
whether tests were independently run or only author logs inspected.

| Finding ID | Severity | File:line | Reviewer observation | Evidence | Verdict |
| --- | --- | --- | --- | --- | --- |
| IR B1 (AGENT07) | P2 | `src/noctinirs_host/control/udp.py:45–59`, `src/noctinirs_host/control/session.py:182–198` at `fe4c4f0` | Fresh-socket `sendto` can wait in blocking mode. Later sends inherit the receive mode. The per-attempt deadline begins after send. | Owner-relayed AGENT07 finding. Author reproduction in `../pr-b-evidence/ir-b1/reproduction.log`. | Open, awaiting re-review |

## Non-blocking review observations

- Open-failure reporting in `SessionController.connect()` returns `IO_ERROR`
  and closes the transport. That path still omits the `io_errors` increment and
  command/link events emitted on transaction I/O errors.
- Contract references need precision about which details come from the Proposed
  ADR, the pinned active firmware and host client policy. Provenance labels in
  socket-double and loopback fixtures are synthetic inputs, and the pinned
  firmware SHA records a compatibility target. See the author's source pointers
  in responses.md.

Keep observations here; author replies belong in responses.md. Never erase prior
findings/failures. Record the exact SHA for any re-review. A code/test change
after review makes prior approval stale and requires re-review; a later notes-only
commit must be identified separately and its lack of code changes verified.
