# Host R3 stage 1: local implementation record

Accountable implementation role: Codex, host developer, assigned by the owner in this chat.
Scope: offline-authorized host changes, author tests and replay tooling. No push, merge,
firmware edits, Wi-Fi/device access or acquisition. Independent reviewer and verification
agent are not assigned here; author tests do not constitute independent review.
The owner explicitly requested a local branch from 524127fc7c89effbf2f82b62dfaa1a0f6d0171b7.
Issue/PR/CI and human merge approval remain pending; no GitHub requests were made.

## A. Preview and RAW boundary

The session still sends original received datagrams directly to the recorder. Only the
preview enters a bounded 4096-packet reorder buffer (default 3 s; CLI range 2-5 s).
Samples are ordered with modular uint32 sequence arithmetic. Duplicate/late packets are
counted; expired holes and device gap flags are exposed to an optional RecoveryObserver.
There is no NACK opcode, transport callback or retransmit implementation.

Display time uses ADC sample sequence / 8000, not host arrival time. Device frame rate is
unchanged. No displayed or recorded samples are interpolated. This deliberately chooses
an interpolation limit of zero: at 10 frames/s even one fabricated frame would occupy
15-18% of a 1.5-1.8 Hz cardiac cycle. NaN/time gaps remain visible as breaks in all graph
modes. The existing graph renderer supplies NaN separators for long time holes.

Display filters internally hold the previous input across short missing intervals.
They reset per channel only after >5 seconds without a valid value, or explicit baseline,
configuration or connection reset. Holding is internal filter state evolution, not a
replacement measurement. Saturation/invalid values are never fed to the filter or written
as synthetic RAW. Initial warmup remains necessary.

The effective display BP upper edge and LP cutoff are at least 3 Hz; BP lower edge is at
most 0.1 Hz. At frame rates <=6 Hz LP/BP are disabled rather than changing Fs. Requested
settings are normalized visibly in the UI and persisted as effective settings. SSR
subtracts a 0.3 Hz low-pass version of the fitted correction, retaining cardiac content.
These are preview choices, not validated physiological processing. The pure filter-design
utility remains available to offline comparisons with historical settings.

## B. Diagnostics contract

Pinned firmware: b11ada2238e5cde7a25df712b098b7033e0a0e09.
Sources: docs/FW_R3_RAW_TX_RETRY.md section 4; ADR-0004 and its local implementation
record at that commit; tools/nn2ctl.py parse_diag and opcode 0x0032. No wire layouts,
Fs, ADC sample rate or device state transitions are changed. The existing base protocol
pin remains intact; R3 diagnostics are pinned separately in control/diagnostics.py.

Optional INFO/netdiag counters: raw_tx_failed_attempts, raw_tx_retried_ok,
raw_tx_abandoned_packets, raw_tx_abandoned_samples, frame_send_enomem. Missing/malformed
uint32 values are unknown, never zero. The host makes one netdiag request at recording
arm and one at finalize; disconnected or unresolved START states record unavailability.
There are no diagnostic retries. Raw netdiag text is not logged; persisted fields are
limited to diagnostic numeric keys and explicit protocol identifiers.

The end sidecar separates device abandoned packets/samples (only when hold_retry and
matching boot evidence establish R3) from observed message sequence holes. Coverage
includes received same-session control replies and corrects reorder within 4096 messages.
Message holes may include lost replies, so they are not asserted to be RAW packet loss.
R2 has unknown device/air attribution. Device abandoned counters reset at START; ENOMEM
is cumulative since boot. A missing end snapshot remains unknown.

## C. Empirical calibration

Tier 1 model_choice is unchanged and not enabled by this work. Tier 2 uses a configurable
empirical target SNR=20, floor=5, live-detector screen=5. These provisional values derive
from private owner replay; they are not literature claims. The legacy tier 1 ENBW/target
parameters remain untouched. The selected live target set is fixed across candidate codes;
weak/dead targets are flagged LOW_QUALITY_DETECTOR rather than dominating every score.

Visits keep complete valid occurrences even if another packet was missing. Default minimum
is 24 of the 31 requested rows (77.4%); loss ceiling is 25%. Rows crossing a gap are excluded.
Configuration mismatches still reject the visit. All observed extrema, including from
rejected visits, remain safety evidence. Drift needs a change greater than BOTH 5% of the
reference and 3*sqrt(reference_variance + current_variance), using full measured row noise.
This intentionally does not claim independent samples or divide by sqrt(N).

Noise uses contiguous per-visit DFT blocks, removes DC and bins at 0.8-3 Hz, and rescales
remaining energy by retained degrees of freedom. Short fragments without >=8 contiguous
rows use within-slot ADC variance of ON/OFF means, not across-frame pulse variance.
This fallback lacks a frame-noise spectrum and is a limited approximation. Spectral leakage, motion and
non-white noise remain limitations. Calibration never changes the displayed/RAW pulse.

Codes >133 retain the five-valid-visit path. A new path accepts >=3 valid visits only when
(max visit mean - min visit mean)/max(abs(mean),5*noise,1 count) <=0.10 for every target.
All clipping/headroom checks remain in force except the owner-approved board exclusion.
Long cap=222, short cap=132. Tier 3 fallback <=min(slot default,133 long /67 short).
Mandatory sweep before every START, RAM-only profiles, and unique START profile IDs remain.

Board 1 is selected with config/optical_geometry_board1.json. Its explicit short-channel
selection uses the existing geometry channel names, not hard-coded slot indices. The owner
approved exclude_unselected_short_from_safety for board 1: the nominal 8 mm short PD is
excluded from score/drift and short-slot headroom/clipping, with GEOMETRY_PD_EXCLUDED and
unmasked extrema/exclusions retained in the artifact. PD 5 mm, contralateral detectors and
all long-slot safety checks remain. The generic geometry retains all detectors.

OFF means default to every reported OFF_STABLE sample. Optional preset 29-38 uses local
positions relative to an observed whole slot, intersected with reported OFF_STABLE; partial
occurrences cannot supply a guessed position. Set parameters.off_window in calibration
configuration to apply the same window to sweep and preview. No phase is reclassified.

RAW sweep datagrams are retained in RAM during calibration (4 MiB/visit, 64 MiB total bounds;
overflow stops calibration). When a successful sweep is attached to a recording, the disk
worker writes <stem>.sweep.raw.bin and <stem>.sweep.json before opening that recording.
The index contains visit boundaries, codes, byte/packet offsets, gap markers, valid-row
counts and flags. Each raw_sha256 hashes exactly the received datagrams used by Visit.
Archives are exclusive-create and use the original RAW archive format/CRC. A canceled,
failed, or never-recorded sweep is not persisted; this remains an explicit limitation.

## Acceptance and handoff

- Original datagrams and archive bytes survive offline replay; no RAW interpolation.
- Reordering, duplicate/wrap/late handling, gap notification, filter continuity, long-gap
  restart, cardiac transfer, noise exclusion, minimum rows, noise-aware drift, stable
  high-code repeats, geometry scope, R2/R3 counters and sweep archive identity have tests.
- Private replay compares both recordings and produces plots and limited code estimates.
  Historical discarded sweep rows do not exist; eligibility counts are not recovered data.
- Full suite on the final commit is recorded in the owner's local report. Evidence classes:
  static, host/unit, simulation and offline replay only. No bench/HIL/field claim.
- Independent host/protocol review and specialist review of the geometry safety exclusion
  and empirical thresholds are required before device use/merge. No merge is authorized.

Run: python -m noctinirs_host_ui --geometry config/optical_geometry_board1.json --preview-delay 3.
Optional --calibration-config config/calibration_r3_off29.json selects the alternate OFF window.
The basic python -m noctinirs_host_ui command retains generic geometry.
Rollback without deleting work: git switch --detach 524127fc7c89effbf2f82b62dfaa1a0f6d0171b7.
The R3 branch and private evidence remain available. Do not reset a dirty workspace.
