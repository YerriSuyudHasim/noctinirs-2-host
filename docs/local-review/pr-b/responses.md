# Author responses (append-only)

Author: Codex / GPT-6, session `codex-pr-b-offline-20261107`.
AGENT07 re-review is pending. Future entries must cite the review finding ID and exact fix SHA,
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
with SHA-256 in test-evidence.md. This records the initial candidate's handoff.

## IR B1/P2 from AGENT07

Reviewed code `fe4c4f003e3ed01028eaab02ce651cb3b8f14969`.
Author fix `7738593b3ea6e15d82d417b3954e1b6c24756ee4`.
**Awaiting AGENT07 re-review.**

UDP opens in nonblocking mode and restores that mode for every `sendto`.
Backpressure returns immediately through the existing `IO_ERROR` path, which
logs the failure, closes the transport and clears the host's lease state.
`CommandResult.sent` reports whether any attempt was accepted by the transport.
A failed first send reports `False`. The attempt deadline begins before send,
so the time spent sending reduces the receive budget. Reply-timeout retries
reuse the original datagram bytes and their session ID.

The initial-connect and timed-receive socket doubles reproduced the inherited
blocking/timeout modes. After pump, the socket already used nonblocking mode,
but the failing send reported `sent=True`. The deadline test exposed the extra
budget granted after send. The correction passes all four checks. Exact-commit
results are 134 focused passes and 531 full-suite passes with three UI skips.
Original failures and artifacts are listed in test-evidence.md.

### Non-blocking items

Open-failure accounting is a follow-up. `control/session.py:332` handles the
explicit open during `connect()`. The `except OSError` branch returns an
`IO_ERROR` with `sent=False` and closes the transport. Its event and counter
reporting differs from the transaction error path at `control/session.py:303`.

The wire reference is firmware `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`,
`firmware/v4-idf61`, and Proposed ADR-0004 rev 4 with its Batch 3 amendment.
ADR-0004 §3.3 specifies the envelope. `nn2_core_protocol.h:33` identifies
SET_MODE, `v4_nn2_control.inc:422` gives the active capability strings, and
`nn2_app_comm.h:7–9` specifies firmware heartbeat and lease timing. Paths refer
to the pinned firmware tree. `tools/nn2ctl.py:173` supplies host client defaults.
The per-attempt budget covering send and the nonblocking send policy are host
decisions. In particular, `nn2ctl.py:196–197` starts its own deadline after send.

`control/protocol.py:211` checks consistency between the reply's SIMULATED flag
and body. The in-process model identifies itself as `sim_model=cbec2da`. The
loopback fixtures can set SIMULATED clear to exercise parsing with synthetic
inputs. Firmware source hashes establish source provenance and the device
firmware identity remains unverified. These distinctions apply to the earlier
implementation notes and the B1 evidence.
