"""Bounded RAW sample continuity in capture order, shared by recording and replay."""

from collections import deque

from ..wire.envelope import Envelope
from ..wire.optical import RawPacket

HISTORY_SAMPLES = 4096
SESSION_HISTORY = 32
_HISTORY_MASK = (1 << HISTORY_SAMPLES) - 1
_U32_MASK = (1 << 32) - 1
_U32_HALF = 1 << 31


class RawContinuity:
    """Track the forward frontier and recent coverage without buffering datagrams."""

    def __init__(self) -> None:
        self._counts = dict(packets=0, samples=0, segments=0, session_changes=0,
                            boot_context_changes=0, epoch_changes=0, old_session_packets=0,
                            old_epoch_packets=0, ambiguous_sequence_packets=0,
                            ambiguous_epoch_packets=0, gap_packets=0, forward_gap_samples=0,
                            late_gap_samples_observed=0, duplicate_packets=0, overlap_packets=0,
                            reorder_packets=0, history_unknown_packets=0, gap_before_packets=0,
                            ts_estimated_packets=0, device_timestamp_nonincreasing=0,
                            host_clock_backsteps=0)
        self._key: tuple[int | None, int] | None = None
        self._retired: deque[tuple[int | None, int]] = deque(maxlen=SESSION_HISTORY)
        self._epoch = 0
        self._origin = self._tail = self._covered = 0
        self._anchor: tuple[int, bool, bool] | None = None  # time, TS_ESTIMATED, rate eligible
        self._last_rx: int | None = None
        self._rate = {False: [0, 0, 0], True: [0, 0, 0]}  # sample steps, us, pairs

    def observe(self, env: Envelope, raw: RawPacket, host_rx_us: int, boot_id: int | None) -> None:
        c = self._counts
        c["packets"] += 1
        c["samples"] += raw.sample_count
        c["gap_before_packets"] += raw.gap_before
        c["ts_estimated_packets"] += env.ts_estimated
        if self._last_rx is not None and host_rx_us < self._last_rx:
            c["host_clock_backsteps"] += 1
        self._last_rx = host_rx_us

        key = (boot_id, env.session_id)
        if key != self._key:
            if key in self._retired:
                c["old_session_packets"] += 1
                return
            if self._key is not None:
                self._retired.append(self._key)
                c["session_changes"] += key[1] != self._key[1]
                c["boot_context_changes"] += key[0] != self._key[0]
            self._key = key
            self._new_segment(raw)
        elif raw.slot_plan_epoch != self._epoch:
            delta = (raw.slot_plan_epoch - self._epoch) & 0xffff
            if delta >= 0x8000:
                c["ambiguous_epoch_packets" if delta == 0x8000 else "old_epoch_packets"] += 1
                return
            c["epoch_changes"] += 1
            self._new_segment(raw)

        delta = (raw.first_sample_sequence - self._origin - self._tail) & _U32_MASK
        if delta == _U32_HALF:
            c["ambiguous_sequence_packets"] += 1
            return
        if delta > _U32_HALF:
            delta -= 1 << 32
        start = self._tail + delta
        end = start + raw.sample_count
        old_tail = self._tail
        window_start = old_tail - HISTORY_SAMPLES
        known_start, known_end = max(start, window_start), min(end, old_tail)
        covered = self._covered_in(known_start, known_end)

        if delta > 0:
            c["gap_packets"] += 1
            c["forward_gap_samples"] += delta
        elif delta < 0:
            if start < window_start:
                c["history_unknown_packets"] += 1
            elif covered == raw.sample_count:
                c["duplicate_packets"] += 1
            elif covered:
                c["overlap_packets"] += 1
            else:
                c["reorder_packets"] += 1
            recovery_start = max(known_start, 0)
            if known_end > recovery_start:
                c["late_gap_samples_observed"] += (
                    known_end - recovery_start - self._covered_in(recovery_start, known_end))

        if end > old_tail:
            advance = end - old_tail
            self._covered = 0 if advance >= HISTORY_SAMPLES else (
                self._covered << advance) & _HISTORY_MASK
            self._tail = end
            if delta >= 0:
                self._observe_anchor(env, contiguous=delta == 0, previous_count=self._previous_count)
                self._previous_count = raw.sample_count
            else:
                self._anchor = None  # a forward-extending overlap breaks the anchor chain
        mark_start = max(start, self._tail - HISTORY_SAMPLES)
        if end > mark_start:
            self._covered |= ((1 << (end - mark_start)) - 1) << (self._tail - end)

    def _new_segment(self, raw: RawPacket) -> None:
        self._epoch = raw.slot_plan_epoch
        self._origin = raw.first_sample_sequence
        self._tail = self._covered = self._previous_count = 0
        self._anchor = None
        self._counts["segments"] += 1

    def _covered_in(self, start: int, end: int) -> int:
        if end <= start:
            return 0
        return ((self._covered >> (self._tail - end)) & ((1 << (end - start)) - 1)).bit_count()

    def _observe_anchor(self, env: Envelope, *, contiguous: bool, previous_count: int) -> None:
        rate_eligible = True
        if self._anchor is not None:
            previous_us, estimated, previous_eligible = self._anchor
            delta_us = env.timestamp_us - previous_us
            if delta_us <= 0:
                self._counts["device_timestamp_nonincreasing"] += 1
                rate_eligible = False
            elif contiguous and previous_eligible and estimated == env.ts_estimated:
                rate = self._rate[estimated]
                rate[0] += previous_count
                rate[1] += delta_us
                rate[2] += 1
        self._anchor = (env.timestamp_us, env.ts_estimated, rate_eligible)

    def snapshot(self) -> dict:
        result = dict(self._counts)
        result["forward_gap_samples_not_seen_late"] = (
            result["forward_gap_samples"] - result["late_gap_samples_observed"])
        for estimated, (steps, span_us, pairs) in self._rate.items():
            name = "adc_rate_ts_estimated" if estimated else "adc_rate_ts_not_flagged"
            result[name] = dict(hz=steps * 1_000_000 / span_us if span_us else None,
                                sample_steps=steps, span_us=span_us, anchor_pairs=pairs)
        return result
