"""Assemble RAW samples into per-slot ON/OFF measurements.

Phase classes come from the RAW contract (OPTICAL_DATA_CONTRACT_DRAFT v0.4.3, section 5):
4 = ON_STABLE, 7 = OFF_STABLE. These are the phases the firmware *scheduled*; they are not
a device-verified LED state. Only STABLE phases enter a mean. A slot occurrence that began
after a stream start or a discontinuity is flagged ``partial`` and is never used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..wire.optical import PH_OUTSIDE, RawPacket

PH_ON_STABLE = 4
PH_OFF_STABLE = 7
ADC_MAX = 8388607   # int24 rails
ADC_MIN = -8388608


@dataclass
class SlotMeasurement:
    slot: int
    epoch: int
    first_seq: int
    host_us: int
    on_sum: list = field(default_factory=lambda: [0, 0, 0, 0])
    off_sum: list = field(default_factory=lambda: [0, 0, 0, 0])
    on_sq: list = field(default_factory=lambda: [0, 0, 0, 0])    # sums of squares, for sigma
    off_sq: list = field(default_factory=lambda: [0, 0, 0, 0])
    on_n: int = 0
    off_n: int = 0
    saturated: list = field(default_factory=lambda: [False] * 4)
    partial: bool = False   # started at stream start or after a gap, or ended by a gap
    gap: bool = False       # a sample discontinuity happened inside this occurrence

    def on_mean(self, ain: int) -> float | None:
        return self.on_sum[ain] / self.on_n if self.on_n else None

    def off_mean(self, ain: int) -> float | None:
        return self.off_sum[ain] / self.off_n if self.off_n else None

    @staticmethod
    def _var(total: int, sq: int, n: int) -> float:
        if n < 2:
            return 0.0
        return max(0.0, (sq - total * total / n) / (n - 1))

    def on_var(self, ain: int) -> float:
        return self._var(self.on_sum[ain], self.on_sq[ain], self.on_n)

    def off_var(self, ain: int) -> float:
        return self._var(self.off_sum[ain], self.off_sq[ain], self.off_n)

    def within_sigma(self, ain: int) -> float:
        """White-noise sigma of mean(ON) - mean(OFF) from the samples inside this slot."""
        if self.on_n < 2 or self.off_n < 2:
            return 0.0
        return (self.on_var(ain) / self.on_n + self.off_var(ain) / self.off_n) ** 0.5

    def flat_on(self, ain: int) -> bool:
        """True when every ON_STABLE sample was identical (a flat top)."""
        return self.on_n >= 3 and self.on_sq[ain] * self.on_n == self.on_sum[ain] ** 2


class SlotAssembler:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._cur: SlotMeasurement | None = None
        self._expected: int | None = None
        self._epoch: int | None = None
        self._fresh = True  # next occurrence follows a start or a gap

    def _close(self, out: list, *, gap: bool = False) -> None:
        if self._cur is not None:
            if gap:
                self._cur.gap = self._cur.partial = True
            out.append(self._cur)
            self._cur = None

    def lose_continuity(self) -> list:
        """Call on a disconnect / reconfiguration: the open slot cannot be trusted."""
        out: list = []
        self._close(out, gap=True)
        self._expected = None
        self._fresh = True
        return out

    def flush(self) -> list:
        out: list = []
        self._close(out, gap=True)
        return out

    def feed(self, packet: RawPacket, host_us: int) -> list:
        out: list = []
        epoch = packet.slot_plan_epoch
        for i, s in enumerate(packet.samples):
            broken = (i == 0 and packet.gap_before) or (
                self._expected is not None and s.sample_sequence != self._expected)
            if broken or epoch != self._epoch:
                self._close(out, gap=True)
                self._fresh = True
            self._expected = (s.sample_sequence + 1) & 0xFFFFFFFF
            self._epoch = epoch
            if s.phase_class == PH_OUTSIDE:
                self._close(out)
                self._fresh = False  # a clean boundary: the next slot starts whole
                continue
            if self._cur is not None and self._cur.slot != s.slot_index:
                self._close(out)
            if self._cur is None:
                self._cur = SlotMeasurement(s.slot_index, epoch, s.sample_sequence, host_us,
                                            partial=self._fresh)
                self._fresh = False
            cur = self._cur
            if s.phase_class == PH_ON_STABLE:
                cur.on_n += 1
                target, squares = cur.on_sum, cur.on_sq
            elif s.phase_class == PH_OFF_STABLE:
                cur.off_n += 1
                target, squares = cur.off_sum, cur.off_sq
            else:
                continue
            for k, v in enumerate(s.ain):
                target[k] += v
                squares[k] += v * v
                if v >= ADC_MAX or v <= ADC_MIN:
                    cur.saturated[k] = True
        return out
