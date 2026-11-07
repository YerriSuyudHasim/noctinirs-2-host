# Implementation and contract traceability

Target: firmware `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, image
`firmware/v4-idf61`, ADR0004/1. ADR-0004 is Proposed, file rev 4 plus the Batch 3
one-byte SET_MODE amendment. This is a scoped host implementation of concrete
firmware behavior, not acceptance of every proposed feature.

`config/reference_pins.json.control_profile` records the exact SHA and SHA-256
of ten locally read source/documents. Existing optical v0.4.3 / `e42f2bc9...`
and golden pins are preserved. Firmware source is not copied into this repository.
An external local read snapshot is under `../pr-b-evidence/contract-bd880f5/`
(paths here are relative to the host clone unless specified).

## Source-to-behavior map

| Pinned firmware source | Implemented host behavior |
| --- | --- |
| ADR-0004 section 3.3; local implementation envelope/body binding; `nn2_core_protocol.c` | Existing 28-byte envelope, 1440-byte payload / 1472-byte datagram cap, CRC32, versions and reserved flags retained; exact reply prefix and opcode-dependent lengths validated. |
| `tools/nn2ctl.py`, `Client` / `request` | Default 3333, .5 s per-attempt timeout, two retries; one outstanding request; source, req_id/opcode, successful timestamp echo, boot and session matching. Each retry sends identical bytes, including req_id/sequence/timestamp. Random initial request ID follows the client watermark strategy; subsequent IDs increase with uint32 wrap. |
| `components/nn2_net/nn2_net.c`, `nn2_net_poll` | Full IPv4 + port endpoint identity for replies and streams on one socket; no discovery or separate legacy stream port. |
| `nn2_app_comm.c`, `execute` and `nn2_app_comm_process` | START alone acquires a lease; INFO/GET_CONFIG observations never adopt another session. INVALID_STATE with another session clears the held ID. STOP accepts owned STARTING/STREAMING/STOPPING; STOPPING ACK retains ownership until observed teardown. Exact cached STOP acceptance can arrive after teardown. SAFE_OFF remains an explicit ownership exception; SAFE_OFF/CLEAR_FAULT success confirms zero session. |
| `nn2_core_protocol.h`, command registry; `nn2_app_comm.c:execute` SET_MODE | Opcode 0x0102, one byte 0=ACQ / 1=ENGINEERING, STOPPED only. Update host mode on OK only. PRECONDITION_FAILED (RAW allocation failure) stays a visible failure. No new mode payload or opcode. |
| `firmware/v4-idf61/main/v4_nn2_control.inc`, `control_init` policy_info | Active image advertises `fs_allowed=2,4,5,8,10`. Controller exposes the actual parsed capability tuple and validates membership. Missing/malformed capability disables Fs writes; no fs_min/fs_max or hard-coded fallback. 1 Hz is rejected. Current maximum is narrowed by INFO when advertised. |
| `nn2_app_comm.h` timing constants and `nn2_app_comm.c:tick` | Heartbeat 500 ms while the host owns STARTING/STREAMING/STOPPING. Firmware alone owns 1500 ms silence + 3000 ms grace behavior; host does not emulate a firmware timer or assume a STOP completed. |
| `nn2ctl.py`, `run` stop polling | Nonblocking state observation via `tick`: 50 ms polling cadence during transitions (if called that frequently), 2 s otherwise. No automatic START and no invented wire timeout. CommandResult OK for START/STOP means ACK acceptance; inspect state for completion. No universal host transition-completion deadline is asserted. |
| `nn2_app_comm_frame`, Batch 3 RAW amendment | FRAME is not REPLY. Active streams have session ID but no req_id/opcode or boot_id. Only held-session FRAMEs from the chosen endpoint pass. RAW layout 3 uses existing strict `parse_raw`; `raw_sink` receives original datagram bytes. No storage, scientific interpretation, or invented RAW ordering policy. |

## Host structure and lifecycle

`control/udp.py` uses Python standard library only, accepts a literal unicast IPv4
and port, owns an ephemeral local socket and filters exact source tuples. Reads
and transaction work have both a deadline and packet-count bound. MAX+1 receive
detects oversize; Winsock WSAEMSGSIZE is also counted/dropped because Windows
consumes the oversized packet while raising that error. Other socket errors are
visible IO_ERROR/link events and release the socket.

Session controller and transport are single-caller APIs. The caller schedules
`tick` regularly and serializes commands; no Qt, background thread, Wi-Fi join,
automatic acquisition or hidden hardware operation is introduced. An active
transaction rejects nested commands from stream/event callbacks. `disconnect`
closes/drains local transport state and forgets identity/capabilities. Explicit
`connect` reopens a socket, reads INFO/GET_CONFIG and never sends START. An already
connected controller rejects a second connect. Failed connect and correlated boot
change close the socket and require explicit reconnect. For compatibility, a reboot
currently returns TIMEOUT with a distinct reboot link event, rather than a new
CommandResult status. Ordinary transaction timeout preserves the last observed
state as uncertain; it never infers success, adopts a lease or retries a new START.

`raw_sink` takes precedence for validated RAW. If absent, the existing generic
`frame_sink` may receive RAW (its existing consumers count it without storage).
OPT2 handling in simulator/replay remains available. **No active firmware OPT2
producer is connected or claimed.** Streams have no boot field, so boot changes
are detected by correlated control replies; session identity is not authentication.

## Historical model and document drift

ADR-0004's local implementation document contains a historical SET_FS set with
1 Hz, and a later historical any-integer 2..10 amendment. The pinned real-samples
image implementation and its advertised discrete set explicitly supersede those
histories for this target; this host does not modify the contract. The in-process
simulator keeps its historical cbec2da synthetic 2..10 model and now explicitly
advertises those supported values, tagged `sim_model=cbec2da`. It supports the
one-byte mode command for tests, but emits synthetic OPT2 only in ACQ; it does
not simulate RAW allocation/production or claim to be the active firmware.
The desktop still selects only this simulator; no UI flow was expanded.

Rollback: stop using this local branch and retain the original contributor branch.
No firmware/device/configuration migration is performed. Integration and any
future hardware validation need an Owner decision after independent review.
