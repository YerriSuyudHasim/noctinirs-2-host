"""Battery status and polling schedule. Pure logic: no I/O, no Qt, injectable time.

Contract: noctinirs-2 ADR-0004 section 14 (firmware 519a96a). The firmware refreshes the
sample only when idle: about every 30 s, and about 5 s after a STOP cleanup. It does no
battery transfer while scanning, so the reported sample age keeps growing during acquisition.
The query never takes or refreshes a lease. Nothing here infers charging.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import BatteryReading

# Display status
UNKNOWN = "UNKNOWN"                  # nothing asked yet
NOT_AVAILABLE = "NOT_AVAILABLE"      # valid=0: no sample (never shown as 0 %)
OK = "OK"
STALE_REFRESH_FAILED = "STALE_REFRESH_FAILED"  # valid=1 but the last refresh failed
UNSUPPORTED = "UNSUPPORTED"          # UNKNOWN_COMMAND: firmware without the query
ERROR = "ERROR"                      # another non-OK status
NO_REPLY = "NO_REPLY"                # timeout or unusable reply

ERR_INVALID_STATE = 259   # esp_err_t: no sample yet since boot
ERR_NOT_SUPPORTED = 262   # esp_err_t: the binding does not report

IDLE_PERIOD_S = 30.0      # ADR-0004 section 14: firmware idle refresh interval
STREAM_PERIOD_S = 60.0    # host-chosen: the sample is not refreshed while scanning
POST_STOP_FIRST_S = 7.0   # firmware schedules ~5 s after cleanup, plus 2 s margin
POST_STOP_RETRY_S = 5.0
POST_STOP_RETRIES = 2
QUERY_TIMEOUT_S = 0.25    # host-chosen: must stay below the owner heartbeat period (0.5 s)
LOW_PERCENT = 20.0        # host-chosen display warning threshold, not a device value


@dataclass(frozen=True)
class BatterySnapshot:
    status: str
    percent: float | None
    volts: float | None
    uv: int | None
    mperm: int | None
    age_ms: int | None          # estimated age of the sample now (device age at reply + host time since)
    error: int | None           # firmware refresh error code of the newest reply
    detail: str
    received_host_time: float | None
    simulated: bool | None
    supported: bool
    waiting_post_stop: bool
    refresh_failed: bool


class BatteryMonitor:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.status = UNKNOWN
        self.detail = ""
        self._good: BatteryReading | None = None
        self._good_at = 0.0
        self._latest: BatteryReading | None = None
        self._latest_at: float | None = None
        self._simulated: bool | None = None
        self._unsupported = False
        self._last_attempt: float | None = None
        self._post_stop = False
        self._stop_seen = 0.0
        self._post_stop_due = 0.0
        self._post_stop_tries = 0

    @property
    def unsupported(self) -> bool:
        return self._unsupported

    # ---- schedule ---------------------------------------------------------
    def due(self, now: float, streaming: bool) -> bool:
        if self._unsupported:
            return False
        if self._post_stop:
            return now >= self._post_stop_due
        if self._last_attempt is None:
            return True
        return now - self._last_attempt >= (STREAM_PERIOD_S if streaming else IDLE_PERIOD_S)

    def attempted(self, now: float) -> None:
        self._last_attempt = now

    def note_stopped(self, now: float) -> None:
        """The host saw the device leave an owned session and report STOPPED."""
        if self._unsupported:
            return
        self._post_stop, self._stop_seen = True, now
        self._post_stop_due, self._post_stop_tries = now + POST_STOP_FIRST_S, 0

    def _after_attempt(self, now: float, fresh: bool) -> None:
        if not self._post_stop:
            return
        if fresh or self._unsupported:
            self._post_stop = False
        elif self._post_stop_tries < POST_STOP_RETRIES:
            self._post_stop_tries += 1
            self._post_stop_due = now + POST_STOP_RETRY_S
        else:
            self._post_stop = False  # give up; the normal schedule resumes

    # ---- outcomes ----------------------------------------------------------
    def on_reading(self, now: float, reading: BatteryReading, simulated: bool | None = None) -> None:
        self._latest, self._latest_at, self._simulated = reading, now, simulated
        self.detail = ""
        if reading.valid:
            self._good, self._good_at = reading, now
            self.status = OK if reading.error == 0 else STALE_REFRESH_FAILED
            fresh = reading.error == 0 and reading.age_ms / 1000.0 < now - self._stop_seen
        else:
            self._good = None
            self.status = NOT_AVAILABLE
            self.detail = {ERR_INVALID_STATE: "no sample since boot",
                           ERR_NOT_SUPPORTED: "not reported by this firmware"}.get(
                reading.error, f"error {reading.error}")
            fresh = False
        self._after_attempt(now, fresh)

    def on_unsupported(self, now: float) -> None:
        self._unsupported = True
        self.status, self.detail = UNSUPPORTED, "UNKNOWN_COMMAND"
        self._after_attempt(now, False)

    def on_status_error(self, now: float, status_text: str) -> None:
        self.status, self.detail = ERROR, status_text
        self._after_attempt(now, False)

    def on_no_reply(self, now: float) -> None:
        self.status, self.detail = NO_REPLY, ""
        self._after_attempt(now, False)

    # ---- view ----------------------------------------------------------------
    def snapshot(self, now: float) -> dict:
        shown = self._good if self.status in (OK, STALE_REFRESH_FAILED, NO_REPLY, ERROR) else None
        if self.status in (NO_REPLY, ERROR) and self._good is None:
            shown = None
        age = None
        if shown is not None:
            age = int(shown.age_ms + max(0.0, now - self._good_at) * 1000)
        refresh_failed = bool(self.status == STALE_REFRESH_FAILED)
        return vars(BatterySnapshot(
            status=self.status,
            percent=None if shown is None else shown.percent,
            volts=None if shown is None else shown.volts,
            uv=None if shown is None else shown.uv,
            mperm=None if shown is None else shown.mperm,
            age_ms=age,
            error=None if self._latest is None else self._latest.error,
            detail=self.detail,
            received_host_time=self._good_at if shown is not None else self._latest_at,
            simulated=self._simulated,
            supported=not self._unsupported,
            waiting_post_stop=self._post_stop,
            refresh_failed=refresh_failed)).copy()
