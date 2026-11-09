"""Median baseline over a time window, with reset."""

from __future__ import annotations

import math
import statistics


class MedianBaseline:
    def __init__(self, duration_s: float, min_samples: int) -> None:
        self.duration_s, self.min_samples = duration_s, min_samples
        self.reset()

    def reset(self) -> None:
        self._t0: float | None = None
        self._vals: list[float] = []
        self.value: float | None = None

    @property
    def ready(self) -> bool:
        return self.value is not None

    @property
    def collected(self) -> int:
        return len(self._vals)

    def push(self, t: float, v: float) -> None:
        """Collect valid positive finite samples only; freeze after duration and minimum count."""
        if self.ready or not (math.isfinite(v) and v > 0):
            return
        if self._t0 is None:
            self._t0 = t
        self._vals.append(v)
        if t - self._t0 >= self.duration_s and len(self._vals) >= self.min_samples:
            self.value = statistics.median(self._vals)
