# RAW engineering recording and replay

The host saves validated RAW layout 3 FRAME datagrams in the order they reach
the recorder. All RAW is **ENGINEERING/non-science**, including samples whose
reported phase identifies an ON region. Phase and slot are stored as reported.
This capture targets firmware `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, image
`firmware/v4-idf61`, ADR-0004 Proposed rev 4 §13 and optical draft v0.4.3. The
existing RAW parser enforces the firmware's 16-byte prefix, 14-byte sample,
1–100 samples, four signed LE int24 ADC values, phase 0–8, OUTSIDE/255, and
slots 0–19 for other phases.

## Files and byte layout

`Recorder(..., record_raw=True)` creates the usual `stem.csv` (OPT2) and
`stem.json`, plus `stem.raw.bin` and `stem.raw.csv`. OPT2 retains its schema
and replay API. The RAW `noctinirs.raw/1` record lives under `raw` in the same
sidecar. A new stem is required for every recording. All data files and the
initial sidecar are created exclusively.

`stem.raw.bin` is the authoritative wire record. It starts with the eight
bytes `4e 4e 32 52 41 57 01 00` (`NN2RAW`, version 1, reserved zero), followed
by the following record layout, repeated until EOF. Integer fields are LE.

| Field | Size | Meaning |
| --- | ---: | --- |
| Wire length | u16 | 62–1448 bytes, including the NN2 envelope and its CRC |
| Capture flags | u8 | Bit 0 means observed boot context is available. Other bits are zero. These are file flags. |
| Host receive time | u64 | Microseconds on the host clock |
| Observed boot context | u64 | Latest correlated control reply's boot ID at routing when using `SessionController`. Zero with flag clear encodes unknown. A RAW datagram carries no boot ID. |
| Wire datagram | wire length | Original bytes, including envelope versions, flags, IDs, device timestamp, payload and wire CRC |
| Record CRC | u32 | CRC-32/ISO-HDLC over the record fields from wire length through the final wire byte, excluding the archive magic |

`stem.raw.csv` contains one row per sample, with a one-based archive packet
ordinal. It carries host receive time, observed boot context (blank if unknown),
session and message IDs, envelope flags, first-sample device timestamp,
`TS_ESTIMATED`, epoch, geometry ID, `GAP_BEFORE`, wrapped sample sequence,
four signed ADC counts, phase and slot. Packet-level fields repeat on each row.
CSV is a projection for inspection. Use the binary archive to preserve or
replay wire evidence. No per-sample times, channel mapping or physiological
values are inferred from the CSV.

## Continuity and clocks

- The tracker uses `first_sample_sequence + sample_count` modulo u32. It
  compares packet ranges to a forward frontier. `gap_packets` and
  `forward_gap_samples` count holes when the frontier advances. The envelope
  message sequence is shared with replies and plays no part in sample loss.
- Within the last 4096 sample positions, fully seen ranges are duplicates,
  partially seen ranges are overlaps, and unseen ranges behind the frontier
  are reorders. Coverage concerns sample positions and does not establish that
  two payloads agree. Late samples can fill already counted holes once each.
  `forward_gap_samples_not_seen_late` is provisional, with no claim about
  late arrivals outside the history. Packets beyond that history and jumps
  of exactly half the serial space have explicit unknown/ambiguous counts.
- The observed boot context and envelope session ID identify a stream segment.
  A forward u16 epoch change, including wrap, restarts the frontier. Older
  epoch packets and packets from the last 32 retired boot/session keys are
  retained in the archive and counted separately, without modifying the
  active frontier. Half-range epoch changes are ambiguous. Long outages,
  delayed packets beyond history and unknown boot contexts can limit restart
  identification. The tracker assumes ordinary changes of less than half a
  counter space, and attributes no gaps across a segment boundary.
- `observed_before_queue` counts validated RAW handed to the recorder,
  including packets the queue drops. It excludes packets rejected before
  the sink, which are covered by controller/transport counters. `persisted_archive`
  is computed on the original records the writer flushed and is recomputed
  in replay. It can have additional holes from recorder drops. A firmware
  `GAP_BEFORE` flag is counted separately from host sequence holes and provides
  no exact loss quantity. A host gap alone cannot identify firmware, transport, or host
  receive loss, or loss before the first or after the last accepted sample.
- Device `timestamp_us` is the first sample's anchor from this firmware profile.
  Its sender sets `TS_ESTIMATED`; preserve it even though the source is the
  sample latch. The host receive time is a separate clock. On UDP the host
  samples Unix epoch microseconds at userspace delivery before routing; it
  has OS scheduling, transport and clock uncertainty. Batch test transports
  can share a receive timestamp, and direct callers can provide a documented
  custom clock. The recorder counts host backsteps and nonincreasing device
  anchors on the forward path. It applies no positive-jump threshold.
- `config.fs_hz` is the nominal optical schedule rate from the control path,
  distinct from the RAW ADC sample rate. The profile carries no nominal ADC
  SPS observation in the capture metadata (`nominal_adc_sps` is null). Reported
  ADC rate estimates are `sample_steps * 1e6 / span_us` on consecutive,
  contiguous forward anchors with positive device deltas. Estimated and
  unflagged anchors are grouped separately, with pair count and summed span.
  Missing evidence gives null. A gap, segment change, forward-extending overlap,
  nonincreasing anchor, or change in timestamp flag prevents bridging that
  interval. These estimates carry latch and clock uncertainty and are not a
  measurement of device timing accuracy.

## Metadata and core API

The sidecar keeps the host version, optional caller-supplied host SHA, geometry
map ID/digest/status, contract target, capture context, events, and file facts.
A RAW firmware SHA supplied via `firmware_sha` is labelled `operator_provided`.
Neither the firmware contract pin nor an operator declaration verifies the
installed image. `recording_snapshot` can preserve the reported INFO ASCII
identity and fields, mode, configured Fs, LED currents, observed boot/session,
and both host and firmware diagnostics. A cached connection INFO is labelled
as such. Missing or invalid observations are JSON `null` (unknown).

In a localhost fake-peer harness with a connected `controller`, explicitly take
an initial snapshot while in ENGINEERING, attach the recorder, then drive the
existing session lifecycle from that same calling thread:

```python
from noctinirs_host.record.writer import Recorder

rec = Recorder(folder, geometry, record_raw=True, host_sha=build_sha)
rec.start(raw_context=controller.recording_snapshot(
    info=controller.refresh_info(), status=controller.refresh_status()))
controller.raw_capture_sink = rec.feed_raw
controller.listeners.append(rec.on_controller_event)
```

The capture sink takes precedence over the original bytes-only RAW sink. The
same endpoint and owned-session checks still apply. The recorder sends no
commands. The caller drives START, `tick()`, and STOP on the fake peer. After
the controller observes STOP completion and before switching out of ENGINEERING,
take the end snapshot and finish the writer:

```python
end = controller.recording_snapshot(
    info=controller.refresh_info(), status=controller.refresh_status())
controller.raw_capture_sink = None
controller.listeners.remove(rec.on_controller_event)
clean_close = rec.stop(timeout_s=5, raw_context=end)
```

Firmware reports `raw_packets_sent`, `raw_dropped_samples` and
`raw_buffer_highwater` in INFO on the pinned image. These are per-field device
snapshots; `raw_dropped_samples` concerns its RAW buffer. They reset on START
and can describe a prior run before the next START. Capture the end before
releasing the ENGINEERING buffer. The writer does not subtract the snapshots
or infer unavailable telemetry. STATUS counters, when requested successfully,
carry their own reply context. Direct callers can use `feed_raw(wire, rx_us,
observed_boot_id)` with a single source and timestamp basis. `feed(wire)` also
handles layout 3 when opted in, using the recorder's clock and unknown boot ID.

## Bounded writing, recovery and replay

The RAW packet counters describe one in-memory state, including in an active
or incomplete sidecar. `observed_before_queue.packets` equals `packets_enqueued`
plus `raw.queue_drops`. The top-level `counters.queue_drops` includes drops of
both RAW and other input, so it is at least `raw.queue_drops`.
`persisted_archive.packets` counts binary records whose writes and flushes the
writer has confirmed. It is at most `packets_enqueued`. The difference is
`enqueued_not_confirmed_in_archive`. It can include queued or in-flight records
and records left unconfirmed after a writer error. A clean close drains that
balance to zero even if there were earlier queue drops. `csv_rows_written`
counts flushed sample rows and can lag the binary sample count if CSV output
is in progress or fails. Rejections are reported separately from observed valid
packets. A published sidecar can lag on-disk bytes, especially
after a timeout or crash. Replay reports the valid binary records it recovers.

The receiver validates bounded datagrams, updates a bounded tracker and offers
them to the shared queue without waiting for free space or doing disk I/O.
The default is 512 items, plus the writer's current item. A queued RAW item
contains up to 1448 wire bytes plus decoded objects for up to 100 samples.
RAW metadata retains up to 1024 events and 256 session IDs, with omission
counters. Events are limited to 64 characters for kind and 512 for text.
The existing controller event list is outside these recorder bounds.

The worker flushes a binary record before writing its sample rows, periodically
syncs the files and atomically refreshes the incomplete sidecar. A clean stop
drains the queue, closes and hashes the files, and writes `complete: true`.
Startup can wait for disk. A stop timeout returns false and stops new feeds.
The worker can still drain after a stall, and the incomplete timeout update is
best effort if storage or sidecar replacement is blocked. A crash can leave a
stale or missing sidecar or fewer CSV rows than archive samples. Keep originals
and replay the valid binary prefix. The tests model interruption and disk
errors at the host level and establish no physical power-loss durability.

```python
from noctinirs_host.record.raw_archive import RawReplay

replay = RawReplay(rec.raw_path)
for packet in replay:
    wire = packet.datagram()
    host_rx_us = packet.host_rx_us
    # Inspect locally. Replay does not send packets to a network.
print(replay.diagnostics, replay.archive_problem, replay.meta_problem)
```

Replay checks file magic, bounded record length, file CRC, and strict wire
decoding. It stops at the first truncated or invalid record and does not scan
for a later sync point. Missing, malformed or incomplete metadata is reported
independently of the valid prefix. On a complete read with clean-close metadata
it compares archive size and SHA-256. The derived CSV is not read during RAW
replay. Replay streams one record at a time, with a fixed continuity window
and an 8 MiB JSON read cap. Iterating without retaining the yielded packets
keeps memory use independent of capture length.

## D2 desktop naming and preview provenance

The desktop (Stage D2) records into `Documents
octinirs\<YYYY-MM-DD>` as
`record_<X>`, where X is one more than the highest `record_<X>` already in the
folder. Existing files are never replaced. When a preview ran, the sidecar
`settings_at_start.preview` holds the preview configuration at arm time and a
separate `record_<X>.preview.json` holds every configuration change, baseline
reset and the stated limits. The RAW archive and its CSV never contain preview
values. The preview is not validated and is not science data.

