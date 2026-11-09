# Battery indicator · implementation

Claude (Sonnet 5.5, author). Base: D2 notes `1c92772`. The firmware repository was read only as
a reference. No C or Python firmware code was copied.

## Pins

`control/protocol.py` has a new `BATTERY_FIRMWARE_PIN = 519a96a2…` and `BATTERY_CONTRACT`
(ADR-0004 section 14, wire ADR0004/1, `nn2ctl.py get-battery`). `FIRMWARE_PIN`
(`bd880f5…`), which feeds the RAW recording metadata, is unchanged.

## Codec

- `OP_GET_BATTERY = 0x0033` with name `get_battery`. It is in neither `MUTATING_OPS` nor
  `TIMED_OPS`. The request has empty args.
- On an OK reply the extension is ASCII text, parsed with a strict `fullmatch`
  (`battery_valid battery_uv battery_mperm battery_age_ms battery_error`, fixed order, single
  spaces, bounded digit counts). `WireError` for: missing, extra or reordered fields,
  non-ASCII, trailing bytes, `valid=1` with age < 0, `valid=0` with uv, mperm, age ≠ −1 or
  error 0. An error status with an extension was already rejected.
- `BatteryReading(valid, uv, mperm, age_ms, error)` with `percent = mperm/1000` (not clamped)
  and `volts = uv/1e6`. `Reply.battery` holds it. Other opcodes are unchanged.

## `control/battery.py` (no I/O)

Display statuses: `UNKNOWN`, `NOT_AVAILABLE` (`valid=0`, never 0 %), `OK`,
`STALE_REFRESH_FAILED` (`valid=1`, error ≠ 0, old value kept), `UNSUPPORTED`, `ERROR`,
`NO_REPLY` (last value kept). Constants with their source: idle 30 s (ADR-0004 section 14),
scanning 60 s (host-chosen), first post-STOP query 7 s (firmware 5 s plus 2 s margin), 2 repeats
5 s apart, query timeout 0.25 s (host-chosen, below the 0.5 s heartbeat period), low-battery
mark 20 % (host-chosen, display only).

Post-STOP freshness: a reply counts as new when `valid=1`, `error=0` and
`battery_age_ms / 1000 < time since the host saw STOPPED`. Otherwise up to 2 repeats, then the
30 s schedule. After `UNKNOWN_COMMAND` it stops until reconnect.
`snapshot()` returns a plain dict: status, percent, volts, uv, mperm, estimated age (device age
at reply plus host time since), error, detail, host time, simulated, supported,
waiting_post_stop, refresh_failed.

## `SessionController`

- `query_battery()`: one transaction through the existing `_request` / `_accept` (so the
  req_id + opcode correlation, boot-id reboot detection and RAW/FRAME routing are the same
  code). Added `_request(timeout_s=…, count_timeout=…)`. It uses `retries=0` and 0.25 s and does
  not increment `counters["timeouts"]`.
- It does **not** call `_set_state`, so device state, fault, `simulated` and `session_id` are
  untouched. `_accept` exempts opcode 0x0033 from the "reply session differs from mine" check:
  the id is the device's current session and may belong to another endpoint.
- Battery data has its own `battery_counters` (queries, timeouts, unsupported, errors) and its
  own `battery_log`. It does not use `_log`, so nothing reaches the recorder listener or the
  sidecar events.
- `_battery_tick()` runs at the end of `tick()`, after the heartbeat and the idle status poll.
  It also skips when an owned session's heartbeat is within 0.25 s of due. It never runs while
  STARTING, STOPPING, START is uncertain, or the controller is busy.
- `connect()` queries once after INFO and get_config when `battery_enabled`; a failure never
  fails the connection. `_set_state` notes "owned session ended in STOPPED" for the post-STOP
  schedule.
- `battery_enabled` defaults to **False**, so Stage A–D controller behaviour and tests are
  unchanged. `EngineeringRun.connect` enables it.

## Engineering run and UI

- `EngineeringRun.snapshot()["battery"]` (dict above plus `connected`, `streaming`) or
  `{"status": "DISCONNECTED"}`. Battery log lines go to the session log only.
- `ui/battery_text.py` (no Qt, no protocol constants) turns the snapshot into text, tooltip and
  a level. Texts: `Baterai 50,5 % · 3,99 V · 12 s lalu`, `Baterai belum tersedia`,
  `Baterai 48,3 % · refresh gagal (263) · 95 s lalu`, `Baterai: tidak didukung firmware ini`,
  `Baterai: error <status>`, `Baterai: tidak ada jawaban` (with the last value when there is
  one), `Baterai —`. While scanning: `· nilai cache sebelum akuisisi`. After STOP:
  `· menunggu refresh setelah STOP`. Values above 100 % are shown as is. The tooltip carries
  mV, the refresh error code, the age, the source (SIMULATED for the fake) and states that no
  charging status is reported. No charging icon or word exists.
- The label sits in the header beside the banner, so it is visible on Welcome, all four setup
  steps and Live. Below 20 % the label is marked "rendah (ambang pilihan host)".

## Simulator, fake, test peer

`DeviceSimulator` models `battery_mode` `old` (UNKNOWN_COMMAND, default), `ok`, `silent`; the
sample refreshes 5 s after a stream ends and every 30 s idle; the query never touches the
owner lease; trailing args give INVALID_ARG. `EngineeringFake` defaults to `ok` and takes
`battery_mode`, `battery`, `battery_age_override_ms` options. `Peer` in `test_udp_session.py`
answers 0x0033 for any endpoint and any session id (UNKNOWN_COMMAND unless `peer.battery` is
set).

## One D2 behaviour touched

An explicit, clean disconnect now also appends the preview's disconnect gap marker once per
run of the preview. Before, the marker came only from link loss, and
`test_disconnect_draws_a_gap` depended on where the stream happened to be cut (it failed once in
a slower run). Nothing in the MBLL chain changed.

## Not changed

RAW/OPT2 parsers, FRAME and RAW routing, recording format and sidecar, START/STOP/SAFE_OFF,
heartbeat, preview chain, gates, geometry, firmware pin, protocol.

## Open points

1. INFO at 519a96a has no battery capability key. Support is detected only from the first
   reply (UNKNOWN_COMMAND or no reply). A capability key would be a request to the
   `noctinirs-2` repository.
2. The firmware sends no charging state. The host shows none.
3. `battery_age_ms` is on the device clock. The host estimates the current age by adding its
   own elapsed time. Clock drift during a scan is not modelled.
4. A malformed battery reply is counted as `malformed` and looks like a missing reply
   (`NO_REPLY`); it is not shown as a separate status.
5. The first query runs inside `connect()` and can wait up to 0.25 s on the session worker when
   the peer is silent.
