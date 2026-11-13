"""Online quality estimates for the preview: noise sigma, ambient level and saturation.

Every constant below is host-chosen. Source for the shape of the rules and the first values:
the bench report of 9 Nov 2026 (BENCH_PREVIEW_GAP_ANALYSIS_2026-11-09, record_1): step noise of
50-100 counts per AIN and two ambient events where OFF rose from about 0.2 M to 1.3-2.3 M counts.
They are tuning values from one bright-room recording, not validated thresholds, and are saved with
the session provenance. The former cross-side "leak" rule was removed on 2026-11-13: a step seen by
the detector on the opposite side of the source is a legitimate tissue path, not a fault.
"""

from __future__ import annotations

import math
import statistics
from collections import deque

ADC_RAIL = 8388607  # int24 positive rail

# --- noise sigma (per slot and AIN), estimated online --------------------------------------
SIGMA_WINDOW = 64            # successive ON-OFF steps kept per (slot, AIN)
SIGMA_MIN_SAMPLES = 8        # below this the between-occurrence sigma is "unknown"
LOW_SIGNAL_SIGMA = 3.0       # I < 3 sigma: warning LOW_SIGNAL (still shown)

# --- ambient (OFF level) ---------------------------------------------------------------------------
AMBIENT_WINDOW = 31          # OFF means kept for the running median
AMBIENT_MIN_HISTORY = 8      # no ambient verdict before this many OFF means
AMBIENT_MAD_K = 20.0         # jump larger than 20 x robust sigma of OFF ...
AMBIENT_REL = 0.10           # ... and larger than 10 % of the running median: AMBIENT_HIGH
AMBIENT_ACCEPT_AFTER = 30    # a level that stays shifted this many occurrences becomes the new normal

# --- saturation -----------------------------------------------------------------------------------------
NEAR_RAIL_FRACTION = 0.97    # mean at or above 97 % of the rail counts as saturated
FLAT_TOP_MIN_FRACTION = 0.25  # zero variance at a high level (flat top)

CONSTANTS = {
    "SIGMA_WINDOW": SIGMA_WINDOW, "SIGMA_MIN_SAMPLES": SIGMA_MIN_SAMPLES,
    "LOW_SIGNAL_SIGMA": LOW_SIGNAL_SIGMA,
    "AMBIENT_WINDOW": AMBIENT_WINDOW, "AMBIENT_MIN_HISTORY": AMBIENT_MIN_HISTORY,
    "AMBIENT_MAD_K": AMBIENT_MAD_K, "AMBIENT_REL": AMBIENT_REL,
    "AMBIENT_ACCEPT_AFTER": AMBIENT_ACCEPT_AFTER, "NEAR_RAIL_FRACTION": NEAR_RAIL_FRACTION,
    "FLAT_TOP_MIN_FRACTION": FLAT_TOP_MIN_FRACTION, "ADC_RAIL": ADC_RAIL,
    "status": "host_chosen_from_bench_9_nov_2026; sigma estimated_online",
}


def robust_sigma(values) -> float:
    """1.4826 x MAD, or 0.0 for fewer than two values."""
    v = list(values)
    if len(v) < 2:
        return 0.0
    med = statistics.median(v)
    return 1.4826 * statistics.median(abs(x - med) for x in v)


class SlotAinStats:
    """History for one (slot, AIN): successive steps (noise) and OFF means (ambient)."""

    def __init__(self) -> None:
        self.steps: deque = deque(maxlen=SIGMA_WINDOW)
        self.off: deque = deque(maxlen=AMBIENT_WINDOW)
        self.shifted = 0
        self.last_sigma: float | None = None

    def step_sigma(self) -> tuple[float, bool]:
        """(sigma, known). Known once enough steps exist: robust sigma of successive differences
        divided by sqrt(2), which removes slow drift."""
        if len(self.steps) < SIGMA_MIN_SAMPLES:
            return 0.0, False
        d = [b - a for a, b in zip(self.steps, list(self.steps)[1:])]
        return robust_sigma(d) / math.sqrt(2.0), True

    def ambient_high(self, off_mean: float) -> bool:
        """True when OFF jumped far from the running median. A level that stays shifted is
        accepted after AMBIENT_ACCEPT_AFTER occurrences."""
        if len(self.off) < AMBIENT_MIN_HISTORY:
            return False
        med = statistics.median(self.off)
        limit = max(AMBIENT_MAD_K * robust_sigma(self.off), AMBIENT_REL * abs(med))
        return abs(off_mean - med) > limit

    def update(self, step: float | None, off_mean: float | None, ambient_flag: bool) -> None:
        if ambient_flag:
            self.shifted += 1
            if self.shifted >= AMBIENT_ACCEPT_AFTER:
                self.off.clear()
                self.steps.clear()
                self.shifted = 0
            return  # event values never enter the history
        self.shifted = 0
        if off_mean is not None and math.isfinite(off_mean):
            self.off.append(off_mean)
        if step is not None and math.isfinite(step):
            self.steps.append(step)
        sigma, known = self.step_sigma()
        self.last_sigma = sigma if known else None
