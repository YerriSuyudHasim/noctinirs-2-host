# Stage D implementation

Codex (GPT-6, author, agent /root). Target firmware is
`bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, image `firmware/v4-idf61`,
ADR0004/1, ADR-0004 Proposed file rev 4 with Batch 3 §13 RAW and one-byte
SET_MODE, plus the pinned local implementation record. RAW layout 3 uses
optical draft v0.4.3. The pin describes compatibility. The reported INFO
and any operator-supplied firmware SHA describe separate provenance.

## Desktop and capture

- The source and packaged entry points open the six-page PySide6 Engineering
  wizard. The earlier README S0 demo walkthrough uses `--demo` on this branch.
  Existing `--self-test` still exercises the legacy simulated OPT2 bundle.
- The wizard keeps input when navigating back, applies ENGINEERING explicitly,
  and lists only rates from INFO `fs_allowed`. Missing capability blocks
  configuration. Connected peers must report STOPPED before applying settings
  or arming. Before START the worker reads INFO, GET_CONFIG and STATUS to
  confirm the configuration and state. Another client can still change
  settings between transactions. The peer's session rules remain authoritative.
- The default source is the clearly labelled in-process RAW fake. It extends
  the existing B simulator and loopback transport with synthetic fixture
  counts and cadence. Real UDP uses the existing IPv4 transport and explicit
  endpoint. Wi-Fi is set up manually.
- The operator checks an existing destination and a new portable stem, then
  arms the C recorder explicitly. A file can appear after the check. C's
  exclusive creation is the authority for no-overwrite. START can run
  without an armed recorder. When recording, use one acquisition per
  recording and finalize after the device reports STOPPED.
- The archive, CSV projections and sidecar use the C writer unchanged. The
  host SHA is supplied via `--host-sha` when available. Firmware INFO,
  declared SHA, boot/session, device replies, host receive time, geometry and
  diagnostics keep their provenance. A START without a confirmed session
  may leave a cleanly closed archive with zero packets and an unresolved end
  context. Recorder completeness and acquisition outcome are independent.
- Live RAW uses ADC counts and reported phase/slot only. It separates
  observed continuity, queue drops and binary confirmations, firmware INFO
  RAW counters, STATUS counters, nominal optical Fs, and ADC anchor
  estimates. Optical Fs measured on the device is unavailable in this UI.
  The UI does not request the separate firmware NETDIAG command. MBLL, DPF
  and short separation are labelled inactive and not applied to RAW
  ENGINEERING. The legacy HTML/JS/CSS supplied navigation and the white/blue
  palette. Its defaults, channel labels, processing and quality indicators
  have no authority in this flow.

## START uncertainty and recovery

The pinned firmware refreshes the lease for successful requests from its
owner endpoint, including STATUS, and for valid matching cached START
retries. Its silence plus grace interval is 1.5 + 3 seconds. A timed-out
START can therefore have a real side effect despite session ID zero on the
host. See [the saved reproduction](test-evidence.md).

1. The original START uses the same request ID, envelope and bytes across B
   retries, with the original transaction capped at 1.5 seconds. Firmware
   cache size is four. The host sends no post-timeout START retry and never
   recovers a delayed ACK after that transaction.
2. If a START was sent and ends in TIMEOUT or transport error, the controller
   latches uncertainty, including across local disconnect. It may drain
   datagrams on an open link. It sends no ordinary commands or background
   polls. Late ACKs are unmatched and frames for a session the host does not
   own are rejected.
3. After 4.5 seconds of silence from the end of the transaction, the operator
   can request one correlated STATUS probe with zero retries. A sent probe
   without a conclusive reply restarts the quiet window because an accepted
   owner STATUS can itself renew the lease. Only a successful reply reporting
   STOPPED or SAFE_OFF with session ID zero resolves uncertainty. A probe
   timeout, uncorrelated reply, active state, fault, or boot mismatch leaves
   it unresolved. A boot mismatch follows B link teardown. A subsequent
   explicit probe can collect a fresh report after the quiet window.
4. Explicit SAFE_OFF remains available to the original endpoint while
   unresolved, including after disconnect. It carries the normal B retry
   budget. Its confirmation dialog warns it can affect another owner. A
   correlated successful SAFE_OFF report with session ID zero is required to
   resolve by this path. Physical LED state requires independent observation.
   CLEAR_FAULT and START are never sent automatically.

The quarantine is in memory for the life of the controller. The UI blocks
normal reconnect while it holds that unresolved controller and keeps the
original endpoint for recovery. After process exit, the operator must use
the saved cleanup result and bench recovery procedure. The 4.5-second delay
is a guard on host transmission, with no assertion about peer timing,
in-flight packets, firmware stop completion or physical output.

## Worker and cleanup

The Qt main thread submits commands and consumes snapshots. One QThread
owns all session calls and pumps/ticks on a roughly 20 ms loop when no
transaction is blocking. This keeps normal heartbeat servicing independent
of user interaction. A bounded synchronous UDP transaction occupies that
worker, so its retries and the OS can delay the next tick. During ambiguous
START the traffic suspension is deliberate. An executor owns the destination
check and recorder open/finalize, while C's writer thread performs capture
I/O. Session routing only offers packets to the existing C queue.

Disconnect/close attempts STOP only for an owned active session, waits for
the reported transition within a bounded confirmation window, and
finalizes the recorder separately. The UI stays open to report outcomes.
Transport failure, unconfirmed STOP or unresolved START stays explicit.
Recorder open can remain blocked in the OS, during which the UI remains
available and waits to clean up. C's finalization can return incomplete
while a stalled daemon writer is still draining. Disk startup, high-rate RAW
processing and network scheduling have no measured hardware bounds here.
