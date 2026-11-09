# Battery indicator · reviewer record

**AGENT07 independent review: PENDING.**

The author (Claude, Sonnet 5.5) cannot review this change. Per AGENTS.md and hub section 5.1 the
reviewer must be from a different model family. Nothing here is a review result.

## Requested scope

Candidate `d0b3d2b59099f73e4ec5b30b140fbec490abc369` on `feat/20-host-battery-offline`, base
`1c92772`. **This change sits on top of the unreviewed D2 change** (`c756a73` and notes
`1c92772`); a verdict on D2 is a prerequisite for merging this one.

1. Codec against the contract: strictness of the ASCII parser, the validity rules, and the
   golden bytes (regenerated from `nn2ctl.py` @ 519a96a).
2. `SessionController.query_battery`: no effect on session, state, lease, control timeout
   counter or heartbeat; reuse of `_request` / `_accept` (correlation, boot-id reboot detection,
   RAW and FRAME routing); the exemption of 0x0033 from the session-id check.
3. Schedule and post-STOP freshness rule; the 0.25 s timeout against the 0.5 s heartbeat period;
   the heartbeat guard in `_battery_tick`.
4. Separation of counters and log from the recorder and sidecar.
5. UI texts against the Owner's rules (no 0 % when not available, no clamp, no charging claim).
6. The one D2 behaviour touched (gap marker on explicit disconnect).

## Findings

None recorded yet. Author notes for the reviewer are in [responses](responses.md) and the
[handoff](handoff.md).
