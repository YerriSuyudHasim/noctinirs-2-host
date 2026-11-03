"""Synthetic demo signal for the S0 spike (pure Python, no Qt, no NumPy).

This is NOT device data and NOT a model of a physiological signal. It only
gives the plot something to draw so that the packaging spike has a live widget.
"""

from __future__ import annotations

import math
import random

SAMPLE_RATE_HZ = 10.0  # demo value only; unrelated to any device setting
WINDOW_SECONDS = 30.0


class SyntheticSource:
    """Deterministic slow sine plus small seeded noise, one sample per call."""

    def __init__(self, seed: int = 1234) -> None:
        self._rng = random.Random(seed)
        self._n = 0

    def next_sample(self) -> tuple[float, float]:
        """Return (time in seconds, value in arbitrary units)."""
        t = self._n / SAMPLE_RATE_HZ
        value = math.sin(2 * math.pi * 0.1 * t) + 0.05 * self._rng.uniform(-1.0, 1.0)
        self._n += 1
        return t, value
