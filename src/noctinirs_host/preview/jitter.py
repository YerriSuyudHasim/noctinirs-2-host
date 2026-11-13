"""Bounded preview-only reorder queue. Never changes or writes a RAW datagram."""
from dataclasses import dataclass
from typing import Protocol


def signed_delta(value, origin):
    return ((value - origin + 0x80000000) & 0xffffffff) - 0x80000000


@dataclass(frozen=True)
class MissingSamples:
    first_sequence: int
    count: int
    gap_before: bool


class RecoveryObserver(Protocol):
    """Future recovery integration point; no opcode or sender is implemented."""
    def missing(self, gap: MissingSamples) -> None: ...


class PreviewJitterBuffer:
    def __init__(self, delay_s=3.0, observer=None, max_packets=4096):
        if not 2 <= delay_s <= 5:
            raise ValueError("Preview delay must be 2–5 seconds")
        self.delay_us = int(delay_s * 1e6)
        self.observer = observer
        self.max_packets = max_packets
        self.queue = {}
        self.anchor = None
        self.frontier = None
        self.epoch = None
        self.counts = dict(late=0, duplicate=0, overflow=0, gaps=0, missing_samples=0)

    def push(self, packet, host_us, msg_sequence=None):
        out = []
        if self.epoch is not None and packet.slot_plan_epoch != self.epoch:
            if ((packet.slot_plan_epoch - self.epoch) & 0xffff) >= 0x8000:
                self.counts['late'] += 1
                return []
            # A queue belongs to one plan. Never mix plans across a boundary.
            out = self.drain(host_us, flush=True)
            self.anchor = self.frontier = None
        self.epoch = packet.slot_plan_epoch
        if self.anchor is None:
            self.anchor = packet.first_sample_sequence
        key = signed_delta(packet.first_sample_sequence, self.anchor)
        if self.frontier is not None and key < self.frontier:
            self.counts['late'] += 1
            return out
        if key in self.queue:
            self.counts['duplicate'] += 1
            return out
        self.queue[key] = (packet, host_us, msg_sequence)
        if len(self.queue) > self.max_packets:
            self.counts['overflow'] += 1
            out.extend(self._release(min(self.queue)))
        out.extend(self.drain(host_us))
        return out

    def _release(self, key):
        packet, host_us, msg = self.queue.pop(key)
        missing = max(0, key - self.frontier) if self.frontier is not None else 0
        if missing or packet.gap_before:
            self.counts['gaps'] += 1
            self.counts['missing_samples'] += missing
            if self.observer is not None:
                self.observer.missing(MissingSamples((self.anchor + (self.frontier or 0)) & 0xffffffff,
                                                     missing, packet.gap_before))
        self.frontier = key + packet.sample_count
        # Reanchor periodically so u32 wrap remains unambiguous on long sessions.
        if self.frontier > 0x20000000:
            shift = self.frontier
            self.anchor = (self.anchor + shift) & 0xffffffff
            self.queue = {k - shift: v for k, v in self.queue.items()}
            self.frontier = 0
        return [(packet, host_us, msg)]

    def drain(self, now_us, *, flush=False):
        out = []
        while self.queue:
            key = min(self.queue)
            if not flush and now_us - self.queue[key][1] < self.delay_us:
                break
            out.extend(self._release(key))
        return out
