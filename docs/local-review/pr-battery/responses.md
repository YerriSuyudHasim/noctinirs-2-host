# Battery indicator · author responses

No reviewer findings yet (AGENT07 PENDING). This file records the author's decisions and
the issues the author's own checks found, for the reviewer to verify.

## Decisions

| Topic | Decision |
| --- | --- |
| Where the query logic lives | In `SessionController` with a separate pure `BatteryMonitor`, because correlation and reboot detection already live in `_request` / `_accept`. A second transaction path would duplicate them. |
| Default | `battery_enabled = False` on the controller. Only `EngineeringRun` turns it on. This keeps every earlier stage test and behaviour unchanged. |
| Counters and log | Separate `battery_counters` and `battery_log`. The controller `_log` also feeds the recorder, which would put battery lines in the RAW sidecar. The Owner said not to write the battery to the sidecar, so battery messages go only to the run's session log. |
| Reply session id | Accepted as data, never adopted, never compared with the host session for this opcode. |
| Malformed reply | Counted as `malformed` and shown as no reply. A separate "bad reply" status was not added. |
| `valid=0` after a good value | Shown as "belum tersedia" and the old value is dropped (the contract says not to show 0 %; it does not define this transition, and the firmware keeps `valid` once set). |
| "Fresh after STOP" | `valid=1`, `error=0` and device age shorter than the time since STOPPED was seen, as the assignment says. |
| Idle status polling | The existing 2 s STATUS poll while not streaming is unchanged. Battery is a separate, slower query. |
| Low-battery mark | 20 %, constant, labelled host-chosen. Display only. |
| Decimal comma | Used in the visible text (`50,5 %`, `3,99 V`) as in the examples. |

## Checks that found problems (fixed before the commit)

1. A test peer that answers unknown opcodes with an empty OK reply made the strict parser reject
   the reply. The peer now answers 0x0033 explicitly (UNKNOWN_COMMAND by default), as the real
   old firmware does.
2. A test replying with a fixed boot id made the controller treat it as a reboot. The test now
   uses the peer's boot id. This also confirmed that boot-id reboot detection still applies to
   battery replies, as the assignment allows.
3. **D2 test flake.** `test_disconnect_draws_a_gap` failed once in a slower full run: after a
   clean STOP the last frame happened not to contain an invalid CH1. The D2 claim is "a
   disconnect shows as a gap", but only link loss appended the gap marker. The marker is now
   also appended once on an explicit disconnect and the test checks for it. This is a small D2
   behaviour change and is listed in the review scope.

## Open for the reviewer

- Whether the first query inside `connect()` (up to 0.25 s wait when the peer is silent) is
  acceptable, or should move to the first `tick()`.
- Whether an unusable reply deserves its own display status.
- Whether the post-STOP wait should also end when the device age is below the time since STOP
  only by a small margin (clock rate differences are not modelled).
- A capability key for the battery query in INFO, and a charging state, would be requests to
  `noctinirs-2`. Neither was invented here.
