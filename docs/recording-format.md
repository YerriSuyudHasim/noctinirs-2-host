# Recording format (host v0.0.1, schema `noctinirs.recording/1`)

An OPT2 recording is a pair of files with the same name. The same recorder can
also opt into a separate [RAW binary archive and sample CSV](raw-recording-format.md).

- `noctinirs_<UTC date>_<time>_<sim|dev>.csv` - what the device sent, frame by frame
- `noctinirs_<UTC date>_<time>_<sim|dev>.json` - context: settings, events, counters

`sim` / `dev` in the name is the caller's declared source; `unk` means undeclared.
Recorded envelope flags determine `simulated` in the JSON. Simulated frames have
`evidence_class: simulation`; absence of the SIMULATED flag leaves evidence `unknown`,
because packet flags alone cannot prove a device test. Mixed flags are explicitly labelled.
Recordings and tests from a simulator are **not** device evidence.

Recordings are never overwritten and never changed after they are closed. Results you
compute from them (OD, HbO/HbR, ...) belong in separate files.

## CSV

One header row, then **80 rows per frame** (20 slots x 4 detectors, slot-major). UTF-8,
comma separated, `\n` line ends, all data columns are integers.

| Columns | Meaning |
|---|---|
| `host_rx_us` | Host receive time, microseconds since the Unix epoch (host clock) |
| `session_id`, `msg_sequence`, `env_flags`, `device_ts_us` | Envelope fields as received. `msg_sequence` counts every message and is **not** a loss counter. `env_flags` bit 0 = SIMULATED, bit 1 = timestamp estimated |
| `adc_source`, `slot_plan_epoch`, `frame_sequence`, `first_sample_sequence`, `sample_count`, `frame_flags`, `geometry_map_id`, `gate_config_id` | Frame prefix. OPT2 continuity must use `frame_sequence`, rather than `msg_sequence`; this core preserves sequence values but does not attribute device/network loss. `frame_flags` bit 0 = WARMUP, bit 2 = ENGINEERING_UNGATED, bit 3 = STARTING_GATE: such frames are recorded, but their values are not science data |
| `slot`, `quality`, `reason`, `n_on_used`, `n_off_used`, `n_on_avail`, `n_off_avail`, `on_commit_late`, `off_commit_late`, `witness_ain`, `verified_mask` | Slot record as the device reported it (`quality` 0 invalid, 1 valid minimum, 2 valid target) |
| `ain`, `sum_on`, `sum_off`, `sd_on_q4`, `sd_off_q4`, `det_flags`, `stab_q` | Detector record. `sum_*` are sums over `n_*_used` samples (mean = sum / n). `sd_*_q4` is the standard deviation in 1/16 count units; 65535 = blank, 65534 = clipped. `-2147483648` = blank |
| `source`, `wavelength`, `pd`, `channel`, `host_note` | **Information only**, added by the host through the optical geometry file: source id, nominal wavelength label (735/850, identity not verified), photodiode name, channel name (empty when the pair is not a channel), and host notes (`non_science`, `host_invalid:...`, `geometry_map_id_differs`). Names are left empty when the frame's `geometry_map_id` differs from the geometry file |

Recording a value does not establish science eligibility. WARMUP, ENGINEERING and
invalid slots remain diagnostic data. The composed science gate and actual firmware
OPT2 producer integration are later work under roadmap #20 (PR-E/F).

Intensity as shown in the live view = `sum_on / n_on_used - sum_off / n_off_used` (ADC counts).

### Reading it

```python
import csv
with open("noctinirs_..._sim.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        ...  # row["frame_sequence"], row["slot"], row["ain"], row["sum_on"], ...
```

If the program or the PC stops in the middle of a frame, the last frame can be incomplete
(fewer than 80 rows or a cut-off line). Ignore incomplete frames. The host reader does this
and reports how many it dropped.
Identical complete frames are retained separately. Invalid numeric/range values and
malformed rows are counted instead of aborting salvage of later complete frames.
The reader reports missing/malformed metadata and clean-close size/SHA256 mismatches
in `meta_problem`, while still recovering complete frames. It retains decoded frames
in memory, so replay memory use grows with recording length.

## JSON sidecar

Written when recording starts (`"complete": false`), refreshed every few seconds, and
finished at a clean stop (`"complete": true`, with `csv_bytes` and `csv_sha256`). A file
with `"complete": false` was not closed cleanly; its CSV is still readable.

Main fields: `schema`, `host_version`, `created_utc`, `started_utc`, `stopped_utc`,
`simulated`, `evidence_class`, `firmware_pin`, `contract`, `geometry` (`map_id`, `sha256`,
`status`), `gate_config_ids`, `fs_hz` and `led_current_ma` (values when the recording
started), `session_ids`, `events` (every command, device state change and link event with
its time, so later setting changes are visible), `counters` and `csv_file`.

`counters`: `frames_written`, `rows_written`, `queue_drops` (datagrams that the writer
could not take in time, never silent), `rejected` (by reason), `not_frame`,
`raw_packets_not_recorded`, `geometry_mismatch_frames`.

Additional provenance fields (additive to schema 1):

- `host_sha` and `host_sha_source`: explicitly supplied by the caller; null when unknown.
  The recorder never runs Git or guesses the checkout used by a packaged application.
- `firmware_pin` is the control/envelope codec reference. In this host snapshot it is
  `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382` (ADR-0004 Proposed, rev 4 with the
  Batch 3 amendment). `firmware_pin_source` distinguishes the codec reference from device identity.
- `optical_firmware_pin` is `e42f2bc9b453b223bf4557516f63fcceadfddbc8`, matching
  `config/reference_pins.json`; `optical_contract` is the draft v0.4.3 / OPT2 layout 2.
  Golden fixtures and their hashes are tested at this optical pin. PR-A does not migrate
  control/session behavior to the newer firmware roadmap snapshot.
- `firmware.declared_sha`, `firmware.sha_source`, `firmware.observed_info` and
  `firmware.boot_id`: caller-supplied declarations/observations, null when unavailable.
  A declared SHA requires an explicit source, for example `operator_declaration`;
  it is never presented as a SHA read from the device. The recorder does no device I/O.
- `settings_at_start`: a copied settings/config snapshot; later mode/setting/fault
  changes can be supplied through timestamped `event()` calls. No faults or settings
  are invented when the caller supplies none.
- `host_time_basis`: `unix_epoch_us` for the default clock, `caller_supplied_unknown`
  for an injected clock. Host receive time and `device_ts_us` remain separate domains.
- `queue_capacity`, `writer_error`, `close_timed_out`: capacity is strictly positive;
  disk errors and timeout are explicit. `queue_drops` counts writer queue loss, never
  device or network loss. On crash, counters reflect the last sidecar refresh and can
  lag the CSV; recover frame counts from the CSV rather than inferring a loss cause.

`start()` creates files exclusively and writes an incomplete sidecar before accepting
input. Recorder instances are single-use. `feed()` does no disk I/O and never waits
for queue space. `stop(timeout_s)` stops accepting input, drains queued packets, and
waits at most the requested deadline for the worker, including flush/fsync/close/hash
and sidecar finalization. A timeout returns false and permanently leaves `complete`
false, even if the worker later recovers and closes its file. Timeout metadata is
published on a separate thread; if sidecar I/O itself is stuck, the existing incomplete
sidecar remains and the timeout update is best effort until disk recovery.
`start()` is synchronous and has no disk timeout. Atomic replacement and fsync improve
recovery; these host tests do not establish durability under physical power loss or
storage-controller failure.

## Limits of this version

- The default OPT2 recorder counts RAW layout 3 packets without storing them.
  Use `record_raw=True` to enable the stage C archive. Rejected datagrams are counted.
- The optical geometry is a draft (`draft_pending_caliper`); channel positions and
  distances are not final. Wavelength labels are nominal.
- The optical contract remains draft. The roadmap's reviewed active firmware snapshot
  `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382` emits RAW ENGINEERING; its actual OPT2
  producer/session integration is later work. This is distinct from PR-A's codec and
  fixture pins, and is not a claim about the image installed on any device.
- OPT2 evidence comes from host/unit tests, pinned golden vectors, and simulation.
