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

## Filed independent re-review — 2026-11-07 KST

Filed by Nori under Owner t232u/t233u. This entry supersedes the earlier
pending disposition, without deleting its historical finding or author response.

**AGENT07 technical verdict: READY / APPROVE. IR B1/P2: CLOSED** on exact
code/test SHA `7738593b3ea6e15d82d417b3954e1b6c24756ee4`.
Reviewed notes HEAD: `49a488e131f0778f0a3842cf5c4e57ca71665894`.
Offline baseline: `d90e00db1f51c05ad2309fd77547a560450cb91a`.

Attribution: AGENT07 — Independent Reviewer / Grok Bot, a non-author of the
candidate implementation/tests, assigned through this conversation via Nori.
The reviewer reported a separate review workspace and read-only author checkout
access. The actual reviewer session identifier is unavailable in the supplied
record; no session or underlying model identity is invented. Codex is the
implementation/revision author; existing author identity claims remain attributed
to their original entries, not newly verified by this filing.

### Basis and evidence limits

Static inspection established nonblocking open/reopen and every send, deadline
before send, bounded visible IO_ERROR with socket/local-ownership cleanup, and
byte-identical retries. AGENT07 inspected/hash-checked author evidence rather
than executing tests/builds/static checks independently. Five listed hashes
matched. Author results: **134 focused passed; 531 full-suite passed / 3 PySide6
UI collection skips**, including 78 UDP, 90 golden and 104 recorder/replay tests.
These editable local records are not immutable CI or independent reruns.
Reproduction lacked embedded SHA; early four-pass fixed-working-tree evidence
was pre-commit; exact-code focused/full evidence is separate.

Reviewer reported clean START 14:44:43, END 14:46:57 and recheck 14:50:50 KST,
2026-11-07, at notes HEAD above, with only five Markdown changes after code SHA.
Nori's filing precheck independently found that HEAD/branch clean and only those
five documentation differences; `git diff 7738593 -- src tests` was empty.
This filing is documentation, not a new technical review or test execution.

### Nonblocking residuals retained

- N1: connect/open failure returns IO_ERROR and closes transport but lacks the
  transaction path's consistent io_errors counter and link/command events.
- N2: contract/provenance precision improved but remains open; Proposed ADR,
  pinned firmware and host decisions must remain distinct. Installed firmware
  identity remains unverified.
- N3: simulator, socket doubles and loopback fake peer are not firmware C,
  device timing, Windows reliability, security or hardware validation.
- N4: transient send backpressure is treated as link loss, clearing local lease
  and closing the socket without automatic STOP/SAFE_OFF. Device streaming may
  persist to lease expiry (approximately 4.5 seconds from pinned-source inference,
  not bench evidence; other protections/actions can stop it sooner). A new local
  endpoint must not be assumed to reclaim the previous lease.
- N5: reopened-socket backpressure test absent; nonblocking is covered statically
  by open/send construction, not that specific test.
- N6: hash table omits fixed-working-tree.log/xml, focused-7738593.log,
  full-7738593.log and exact-head.txt. Reproduction lacks embedded SHA; pre-commit
  results must not be mislabeled exact-SHA evidence.
- N7: Windows ICMP/WSAECONNRESET teardown behavior is an unverified, unreproduced
  risk, not a newly demonstrated defect or required B1 correction.
- PR-A recorder PermissionError remains unresolved/nonblocking. Absence in recent
  runs is not proof of a fix.

Reviewer NOT RUN: tests/builds/static checks, independent reruns, Python 3.12.7,
PySide6 UI/build, other operating systems, online CI/remote freshness, firmware C,
hardware/bench/HIL/field. No new test run was performed for this notes filing.

### Governance is separate from technical approval

AGENT07 withheld governance acceptance: committed and modified/uncommitted hub
policies differ; host project registration was not established; reviewer-session
record is incomplete. The scoped Owner acceptance in owner-acceptance.md records
local baseline acceptance, not broad adoption of modified hub AGENTS.md, formal
host registration, satisfaction of legacy CI gates or merge/hardware authority.
No hub policy or project registration was changed. These questions remain open.
