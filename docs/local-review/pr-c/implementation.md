# Stage C implementation and traceability

The [RAW format specification](../../raw-recording-format.md) defines the
host-only archive, snapshot API, clock domains, writer bounds and recovery.
Stage C attaches explicitly to the B controller. The original bytes-only sink
and OPT2 APIs remain available. The recorder shares PR-A's queue, worker,
exclusive creation, sidecar and timed shutdown. RAW replay streams wire
records and uses the existing envelope and layout parsers. It shares the
continuity tracker with the writer.

| Local pinned firmware source | Use in this candidate |
| --- | --- |
| `docs/adr/0004-host-protocol.md` §3.3, §13; `docs/adr/0004-local-implementation.md`; `docs/BATCH3_RAW_ENGINEERING.md` | NN2 envelope and RAW binding, common sequencing, engineering status, first-sample anchor, diagnostics and the lease boundary. |
| `docs/drafts/OPTICAL_DATA_CONTRACT_DRAFT.md` v0.4.3; `components/nn2_core/src/nn2_core_optical_codec.c`; `tools/nn2_raw_capture.py` | Reuse strict host layout 3 parsing for count, signed int24, phase, slot, flags and reserved bytes. The host archive wrapper adds receive time and boot context around the untouched wire bytes. |
| `components/nn2_core/src/nn2_core_raw_buffer.c`; `components/nn2_app/src/nn2_app_comm.c` | RAW sequence and epoch boundaries, the first sample's timestamp, actual `TS_ESTIMATED` flag, INFO raw counters and read-only STATUS. |
| `firmware/v4-idf61/main/v4_nn2_control.inc` | Reported INFO identity, engineering capability and RAW counters. The configured optical Fs and LED currents are observed through the B control path. |

The source was read with `git show <firmware-sha>:<path>` from the local
firmware clone at `C:/Users/doeki/Documents/GitHub/noctinirs-2`. The ten
existing control-profile blob hashes and optical draft hash matched the host
pins. Additional inspected RAW sources have hashes in the external
`pr-c-evidence/environment-contract.json`. This identifies a compatibility
target and supplies no installed firmware SHA.

The host archive intentionally tracks observed and persisted continuity
separately. A sequence gap is an observation, with explicit uncertainty for
reordered samples beyond the fixed window. The archive records boot ID as
associated control context rather than claiming the field was in RAW.
Nonincreasing device anchors and host backsteps are separate. The candidate
applies no threshold to positive timestamp jumps. Its ADC rate estimates use
only adjacent positive contiguous device-anchor deltas with the same timestamp
flag. Nominal optical scheduling Fs is stored separately.

To roll back local adoption, keep the B branch at `5fb8abd` and stop using
the candidate branch. Existing OPT2 recordings continue to use their prior
reader. Keep original RAW archives with their JSON sidecars for later replay;
the candidate has no device or data migration.
