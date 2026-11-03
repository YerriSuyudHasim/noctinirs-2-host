# Recording format (host v0.0.1, schema `noctinirs.recording/1`)

A recording is a pair of files with the same name:

- `noctinirs_<UTC date>_<time>_<sim|dev>.csv` - what the device sent, frame by frame
- `noctinirs_<UTC date>_<time>_<sim|dev>.json` - context: settings, events, counters

`sim` in the name means the data came from the built-in simulator. `dev` means a real
device. A recording of simulated data is **not** device evidence. The JSON says so in
`evidence_class`.

Recordings are never overwritten and never changed after they are closed. Results you
compute from them (OD, HbO/HbR, ...) belong in separate files.

## CSV

One header row, then **80 rows per frame** (20 slots x 4 detectors, slot-major). UTF-8,
comma separated, `\n` line ends, all data columns are integers.

| Columns | Meaning |
|---|---|
| `host_rx_us` | Host receive time, microseconds since the Unix epoch (host clock) |
| `session_id`, `msg_sequence`, `env_flags`, `device_ts_us` | Envelope fields as received. `msg_sequence` counts every message and is **not** a loss counter. `env_flags` bit 0 = SIMULATED, bit 1 = timestamp estimated |
| `adc_source`, `slot_plan_epoch`, `frame_sequence`, `first_sample_sequence`, `sample_count`, `frame_flags`, `geometry_map_id`, `gate_config_id` | Frame prefix. **Loss is counted in `frame_sequence`.** `frame_flags` bit 0 = WARMUP, bit 2 = ENGINEERING_UNGATED, bit 3 = STARTING_GATE: such frames are recorded, but their values are not science data |
| `slot`, `quality`, `reason`, `n_on_used`, `n_off_used`, `n_on_avail`, `n_off_avail`, `on_commit_late`, `off_commit_late`, `witness_ain`, `verified_mask` | Slot record as the device reported it (`quality` 0 invalid, 1 valid minimum, 2 valid target) |
| `ain`, `sum_on`, `sum_off`, `sd_on_q4`, `sd_off_q4`, `det_flags`, `stab_q` | Detector record. `sum_*` are sums over `n_*_used` samples (mean = sum / n). `sd_*_q4` is the standard deviation in 1/16 count units; 65535 = blank, 65534 = clipped. `-2147483648` = blank |
| `source`, `wavelength`, `pd`, `channel`, `host_note` | **Information only**, added by the host through the optical geometry file: source id, nominal wavelength label (735/850, identity not verified), photodiode name, channel name (empty when the pair is not a channel), and host notes (`non_science`, `host_invalid:...`, `geometry_map_id_differs`). Names are left empty when the frame's `geometry_map_id` differs from the geometry file |

A value is usable as science data only when the frame is a science frame and the slot is
valid and verified for that detector (the host applies the same rule in the live view).
Do not use rows of WARMUP or ENGINEERING frames, or rows with `quality` 0, as measurements.

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

## Limits of this version

- Raw layout-3 packets and rejected datagrams are counted but not stored.
- The optical geometry is a draft (`draft_pending_caliper`); channel positions and
  distances are not final. Wavelength labels are nominal.
- The firmware frame format is still a proposal (ADR-0004 3.17) and the pinned firmware
  has no frame producer yet, so every recording made so far is from the simulator.
