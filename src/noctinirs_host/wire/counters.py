"""Counters for rejected packets and host-invalidated slots.

Every packet the parser rejects is counted under the `WireError.code`. The codes
that the firmware reviewer asked to have as separate counters are listed in
`REJECT_REASONS`; any other rejection (size, layout, CRC, ...) is counted under
its own code too, so nothing is dropped silently.
"""

from __future__ import annotations

from collections import Counter

from . import optical
from .errors import WireError

REJECT_REASONS = (
    "reserved",
    "raw_flags",
    "sample_count",
    "engineering_mismatch",
    "verified_n_lt_2",
    "reason_quality",
)


class RejectCounters:
    def __init__(self) -> None:
        self.rejected: Counter[str] = Counter()
        self.slot_invalid: Counter[str] = Counter()
        self.frames_ok = 0
        self.warnings = 0

    def record_error(self, err: WireError) -> None:
        self.rejected[err.code] += 1

    def record_frame(self, frame: optical.Opt2Frame | optical.RawPacket) -> None:
        self.frames_ok += 1
        if isinstance(frame, optical.Opt2Frame):
            self.warnings += len(frame.warnings)
            for s in frame.slots:
                if s.host_invalid:
                    self.slot_invalid[s.host_invalid] += 1

    def parse(self, payload: bytes) -> optical.Opt2Frame | optical.RawPacket | None:
        """parse_frame + counting. Returns None when the packet was rejected."""
        try:
            frame = optical.parse_frame(payload)
        except WireError as e:
            self.record_error(e)
            return None
        self.record_frame(frame)
        return frame

    def named(self) -> dict[str, int]:
        """The six reviewer-requested counters (zero when never seen)."""
        return {r: self.rejected.get(r, 0) for r in REJECT_REASONS}
