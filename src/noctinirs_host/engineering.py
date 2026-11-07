"""Stage D orchestration. One caller owns the session; disk jobs never own it."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import re
import time

from .control.engineering_fake import EngineeringFake
from .control.protocol import ST_SAFE_OFF, ST_STARTING, ST_STOPPED, ST_STREAMING
from .control.session import LoopbackTransport, SessionController
from .control.udp import UDPTransport
from .geometry import Geometry
from .record.continuity import RawContinuity
from .record.writer import Recorder
from .wire.envelope import parse_envelope
from .wire.optical import parse_raw

UNKNOWN = "Unknown / unavailable"
STOP_WAIT_S = 5.0


@dataclass(frozen=True)
class RecordingTarget:
    folder: Path
    name: str
    firmware_sha: str | None

    @classmethod
    def parse(cls, folder: str, name: str, firmware_sha: str) -> RecordingTarget:
        path = Path(folder)
        if folder != folder.strip() or not path.is_absolute():
            raise ValueError("Enter an absolute recording folder with no surrounding whitespace.")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name)
                or name.endswith(".") or re.fullmatch(
                    r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", name.split(".")[0], re.I)):
            raise ValueError("Enter a portable recording stem (1–80 letters, digits, ., _ or -; "
                             "start with a letter or digit; avoid reserved names).")
        if firmware_sha and not re.fullmatch(r"[0-9a-fA-F]{40}", firmware_sha):
            raise ValueError("Operator firmware SHA must be empty or 40 hexadecimal characters.")
        return cls(path, name, firmware_sha or None)

    def check(self) -> None:
        if not self.folder.is_dir():
            raise ValueError("Choose an existing recording folder.")
        for suffix in (".json", ".csv", ".raw.bin", ".raw.csv"):
            path = self.folder / (self.name + suffix)
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"{path.name} exists. Choose a new stem.")


class EngineeringRun:
    """Runs on the session worker, including all control transactions and callbacks."""

    def __init__(self, geometry: Geometry, host_sha: str | None = None, *,
                 controller_factory=SessionController, recorder_factory=Recorder) -> None:
        self.geometry = geometry
        self.host_sha = host_sha
        self._controller_factory = controller_factory
        self._recorder_factory = recorder_factory
        self._disk = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nn2-disk-lifecycle")
        self._job: Future | None = None
        self.controller: SessionController | None = None
        self.endpoint = ""
        self.configured = False
        self.checked_target: RecordingTarget | None = None
        self.recorder: Recorder | None = None
        self.recorder_state = "IDLE"
        self.recording_used = False
        self.recording_path: Path | None = None
        self._last_recording_meta: dict | None = None
        self.raw = RawContinuity()
        self.latest_raw: dict = {}
        self.context: dict = {}
        self.log: deque[str] = deque(maxlen=200)
        self.notice = ""
        self.error = ""
        self.cleanup = ""
        self._closing: float | None = None
        self._stop_deadline: float | None = None
        self._end_context: dict | None = None
        self._exit_requested = False
        self.finished = False

    @property
    def connected(self) -> bool:
        return bool(self.controller and self.controller.connected)

    @property
    def stopped(self) -> bool:
        return bool(self.connected and self.controller.device_state == ST_STOPPED
                    and not self.controller.session_id and not self.controller.start_uncertain
                    and self._stop_deadline is None)

    def _note(self, text: str) -> None:
        self.notice = text
        self._event(text)

    def _event(self, text: str) -> None:
        self.log.append(f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}  {text}")

    def fail(self, message: str) -> None:
        self.error = message
        self._event(message)

    def _result(self, result) -> None:
        if not result.ok:
            raise ValueError(f"{result.command}: {result.status} ({result.message})")

    def connect(self, fake: bool, address: str, port: str) -> None:
        if self.controller and self.controller.start_uncertain:
            raise ValueError("Resolve the pending START at the original endpoint before connecting.")
        if self.connected or self._closing is not None or self.recorder is not None:
            raise ValueError("Finish disconnecting and closing the recorder before connecting.")
        if fake:
            transport = LoopbackTransport(EngineeringFake(geometry=self.geometry))
            endpoint = "Local RAW fake (in-process loopback)"
        else:
            if not re.fullmatch(r"[0-9]{1,5}", port):
                raise ValueError("Enter a UDP port from 1 to 65535.")
            transport = UDPTransport(address, int(port))
            endpoint = f"UDP {transport.endpoint[0]}:{transport.endpoint[1]}"
        self.endpoint = endpoint
        self.configured = False
        self.raw = RawContinuity()
        self.latest_raw = {}
        self.context = {}
        self.cleanup = ""
        ctl = self.controller = self._controller_factory(transport)
        ctl.listeners.append(lambda event: self._event(event.text))
        ctl.raw_capture_sink = self._capture
        self._result(ctl.connect())
        self.context = ctl.recording_snapshot()
        self._note(f"Connected to {endpoint}. Configure ENGINEERING before starting.")

    def configure(self, fs: int | None) -> None:
        ctl = self.controller
        if not self.stopped or self.recorder is not None:
            raise ValueError("Configuration requires STOPPED and a closed recorder.")
        if ctl.info.get("frames") != "RAW_ENGINEERING":
            raise ValueError("The peer did not advertise RAW_ENGINEERING in INFO.")
        if fs not in ctl.settings.fs_allowed:
            raise ValueError("Choose a rate advertised by the peer in INFO fs_allowed.")
        self.configured = False
        self._result(ctl.set_mode("ENGINEERING"))
        self._result(ctl.set_fs(fs))
        info = ctl.refresh_info()
        self._result(info)
        self.context = ctl.recording_snapshot(info=info)
        if info.reply.info.get("mode") != "ENGINEERING" or not self.stopped:
            raise ValueError("The peer did not confirm ENGINEERING in STOPPED.")
        self.configured = True
        self._note(f"ENGINEERING applied. Nominal optical Fs {fs} Hz.")

    def check_destination(self, target: RecordingTarget) -> None:
        if self._job is not None or self.recorder is not None:
            raise ValueError("The recorder is busy.")
        self.checked_target = None
        self._job = self._disk.submit(self._check, target)

    @staticmethod
    def _check(target: RecordingTarget) -> RecordingTarget:
        target.check()
        return target

    def _require_ready(self) -> None:
        if not self.stopped or not self.configured or self.controller.settings.mode != "ENGINEERING":
            raise ValueError("Connect and apply ENGINEERING while STOPPED.")

    def _observe(self, *, verify_config: bool = False) -> dict:
        ctl = self.controller
        if not self.connected:
            return ctl.recording_snapshot()
        previous_fs = ctl.settings.fs_hz
        info = ctl.refresh_info()
        config = ctl.refresh_config() if ctl.connected and verify_config else None
        status = ctl.refresh_status() if ctl.connected else None
        self.context = ctl.recording_snapshot(info=info, status=status)
        if info.ok and (info.reply.info.get("mode") != "ENGINEERING"
                        or info.reply.info.get("frames") != "RAW_ENGINEERING"):
            self.configured = False
        for result in (info, config, status):
            if result is not None and not result.ok:
                self.fail(f"{result.command}: {result.status} ({result.message})")
        if not ctl.connected:
            self._lose_link("Device link lost or rebooted while reading diagnostics. Device STOP is unconfirmed.")
        if verify_config:
            self._result(info)
            self._result(config)
            self._result(status)
            if ctl.settings.fs_hz != previous_fs:
                self.configured = False
                raise ValueError("Nominal optical Fs changed. Apply configuration again.")
        return self.context

    def diagnostics(self) -> None:
        if not self.stopped:
            raise ValueError("Refresh firmware diagnostics while STOPPED.")
        self._observe()

    def arm(self, target: RecordingTarget) -> None:
        self._require_ready()
        if self._job is not None or self.recorder is not None or target != self.checked_target:
            raise ValueError("Check an unused recording destination before arming.")
        ctl = self.controller
        context = self._observe(verify_config=True)
        self._require_ready()
        if context["observed_info"]["source"] != "INFO" or context["firmware_status"] is None:
            raise ValueError("Fresh INFO and STATUS are required to arm RAW recording.")
        self.recorder = self._recorder_factory(
            target.folder, self.geometry, name=target.name, record_raw=True,
            simulated=ctl.simulated, host_sha=self.host_sha, firmware_sha=target.firmware_sha,
            observed_info=context["observed_info"], boot_id=ctl.boot_id,
            settings=lambda: {**context["config"], "device_state": context["device_state"]},
        )
        self.recording_path = self.recorder.json_path
        self._last_recording_meta = None
        self._end_context = None
        self._armed_config = context["config"]
        self.recording_used = False
        self.recorder_state = "OPENING"
        self.checked_target = None  # Recorder exclusively creates the files; check again for reuse.
        self._job = self._disk.submit(self.recorder.start, raw_context=context)

    def start(self) -> None:
        self._require_ready()
        if self._job is not None or (self.recorder is not None and (
                self.recorder_state != "ARMED" or self.recording_used)):
            raise ValueError("Wait for the recorder or finalize the previous recording.")
        context = self._observe(verify_config=True)
        self._require_ready()
        if self.recorder is not None and self._armed_config != context["config"]:
            raise ValueError("Settings changed since arming. Finalize and arm a new recording.")
        self.raw = RawContinuity()
        self.latest_raw = {}
        result = self.controller.start()
        if self.controller.start_uncertain:
            self.configured = False
            message = f"START outcome unresolved ({result.status}). Lease-refresh traffic suspended."
            self.fail(message)
            context = self.controller.recording_snapshot()
            context["last_reported_device_state"] = context["device_state"]
            context["device_state"] = None
            context["start_outcome"] = "unresolved"
            if self.recorder is not None:
                self.recorder.event("cleanup", message)
                self._finalize(context)
            return
        if result.ok and self.recorder is not None:
            self.recording_used = True
            self.recorder_state = "RECORDING"
        self._check_mutation(result)

    def check_start_outcome(self) -> None:
        if self.controller is None:
            raise ValueError("No pending START.")
        result = self.controller.check_start_outcome()
        self._result(result)
        self._note(f"START outcome resolved by device report: {result.reply.state_text}. "
                   "Physical LED state has not been measured.")

    def safe_off(self) -> None:
        ctl = self.controller
        if ctl is None or not (self.connected or ctl.start_uncertain):
            raise ValueError("SAFE_OFF requires an endpoint with a live link or unresolved START.")
        result = ctl.safe_off()
        self.configured = False
        if not result.ok:
            if not ctl.connected and not ctl.start_uncertain:
                self._lose_link("SAFE_OFF response unavailable. Device state is unconfirmed.")
            self._result(result)
        if result.reply.state != ST_SAFE_OFF:
            raise ValueError(f"SAFE_OFF not confirmed. Device reported {result.reply.state_text}.")
        self._stop_deadline = None
        self._note("Device reported SAFE_OFF. Physical LED state has not been measured.")
        self._finalize(ctl.recording_snapshot())

    def stop(self) -> None:
        ctl = self.controller
        if not self.connected or not ctl.session_id or ctl.device_state not in (ST_STARTING, ST_STREAMING):
            raise ValueError("STOP requires an owned STARTING or STREAMING session.")
        result = ctl.stop()
        if result.ok:
            if ctl.device_state == ST_STOPPED:
                self._note("Device reported STOPPED after STOP.")
            else:
                self._stop_deadline = time.monotonic() + STOP_WAIT_S
                self._note("STOP accepted. Waiting for the device to report STOPPED.")
        self._check_mutation(result)

    def _check_mutation(self, result) -> None:
        if not result.ok and result.status in ("TIMEOUT", "IO_ERROR"):
            self._lose_link(f"{result.command}: {result.status}. Device STOP is unconfirmed.")
        self._result(result)

    def finalize(self) -> None:
        if not self.stopped or self.recorder_state not in ("ARMED", "RECORDING"):
            raise ValueError("Wait for STOPPED before finalizing the recording.")
        self._finalize(self._observe())

    def _finalize(self, context: dict | None) -> None:
        if self.recorder is None or self._job is not None:
            if self.recorder_state == "OPENING":
                self._end_context = context
            return
        ctl = self.controller
        ctl.listeners.remove(self.recorder.on_controller_event)
        self.recorder_state = "FINALIZING"
        self._job = self._disk.submit(self.recorder.stop, raw_context=context)

    def _capture(self, wire: bytes, host_us: int, boot_id: int | None) -> None:
        if self.recorder_state in ("ARMED", "RECORDING"):
            self.recorder.feed_raw(wire, host_us, boot_id)
        env = parse_envelope(wire)
        raw = parse_raw(env.payload)
        self.raw.observe(env, raw, host_us, boot_id)
        self.latest_raw = {"sample": raw.samples[-1], "first_device_us": env.timestamp_us,
                           "host_us": host_us, "estimated": env.ts_estimated,
                           "simulated": env.simulated, "epoch": raw.slot_plan_epoch}

    def disconnect(self) -> None:
        if self._closing is not None:
            return
        self._closing = time.monotonic() + STOP_WAIT_S
        self.cleanup = "Closing session. Waiting for STOP evidence and recorder finalization."
        ctl = self.controller
        if self.connected and ctl.session_id and ctl.device_state in (ST_STARTING, ST_STREAMING):
            try:
                self.stop()
            except ValueError as exc:
                self.fail(str(exc))

    def shutdown(self) -> None:
        self._exit_requested = True
        self.disconnect()

    def _close_link(self, message: str, context: dict | None) -> None:
        ctl = self.controller
        self._event(message)
        if ctl and self.recorder_state in ("ARMED", "RECORDING"):
            self.recorder.event("cleanup", message)
        if ctl:
            ctl.disconnect()
        self.configured = False
        self._stop_deadline = None
        self.cleanup = message
        self._end_context = context
        self._finalize(context)

    def _lose_link(self, message: str) -> None:
        ctl = self.controller
        self.fail(message)
        self._close_link(message, ctl.recording_snapshot() if ctl else None)

    def _poll_disk(self) -> None:
        if self._job is None or not self._job.done():
            return
        job, self._job = self._job, None
        try:
            result = job.result()
        except Exception as exc:
            self.fail(f"Recording destination or writer error: {type(exc).__name__}: {exc}")
            if self.recorder is not None:
                self.recorder_state = "INCOMPLETE / ERROR"
                self.recorder = None
            return
        if isinstance(result, RecordingTarget):
            self.checked_target = result
            self._note("Destination checked for existing files. Arming reserves files exclusively.")
        elif self.recorder_state == "OPENING":
            self.controller.listeners.append(self.recorder.on_controller_event)
            self.recorder_state = "ARMED"
            self._note(f"RAW recording armed: {self.recording_path}")
            if not self.connected or not self.stopped or self._closing is not None:
                self.recorder.event("cleanup", self.cleanup or "Device no longer STOPPED while arming.")
                self._finalize(self._end_context)
        else:
            meta = self.recorder.metadata()
            self._last_recording_meta = meta["raw"]
            self.recorder_state = "FINALIZED" if result else "INCOMPLETE / ERROR"
            if not result:
                detail = meta["writer_error"] or (
                    "finalization timed out; files may still be draining" if meta["close_timed_out"]
                    else "writer did not confirm a clean close")
                self.fail(f"Recording incomplete: {detail}. Keep files for inspection.")
            self._note(f"Recorder {self.recorder_state}: {self.recording_path}")
            self.recorder = None

    def tick(self) -> None:
        ctl = self.controller
        if self.connected:
            timeouts, session = ctl.counters["timeouts"], ctl.session_id
            ctl.tick()
            if not ctl.connected or ctl.counters["timeouts"] != timeouts:
                reason = "Device link lost or rebooted" if not ctl.connected else "Control poll timed out"
                self._lose_link(f"{reason}. Device STOP is unconfirmed. Reconnect explicitly.")
            elif self._stop_deadline is not None and ctl.device_state == ST_STOPPED:
                self._stop_deadline = None
                self._note("Device reported STOPPED after STOP.")
            elif self._stop_deadline is not None and time.monotonic() >= self._stop_deadline:
                self._lose_link("STOP confirmation timed out. Device STOP is unconfirmed.")
            elif session and not ctl.session_id and self.recorder_state == "RECORDING":
                self.fail(f"Owned session ended. Last device report: {ctl.state_text}.")
                self.recorder.event("cleanup", self.error)
                self._finalize(ctl.recording_snapshot())
        if ctl:
            ctl.events.clear()  # Only this workflow's bounded log and recorder retain events.
        self._poll_disk()
        if self.recorder_state in ("ARMED", "RECORDING"):
            error = self.recorder.metadata()["writer_error"]
            if error:
                self.fail(f"RAW writer error: {error}. Acquisition is controlled separately.")
                self._finalize(ctl.recording_snapshot())
        if self._closing is not None:
            if self.stopped:
                context = self._observe() if self.recorder is not None else ctl.recording_snapshot()
                message = ("Disconnected. Device reported STOPPED before disconnect." if self.stopped
                           else "Disconnected. Device STOP is unconfirmed.")
                self._close_link(message, context)
            elif self.connected and not ctl.session_id and self._stop_deadline is None:
                message = ("Disconnected. START outcome unresolved. Device STOP is unconfirmed."
                           if ctl.start_uncertain else
                           "Disconnected without an owned session. Device STOP is unconfirmed.")
                self._close_link(message,
                                 ctl.recording_snapshot())
            elif self.connected and time.monotonic() >= self._closing:
                self._lose_link("Disconnected after cleanup deadline. Device STOP is unconfirmed.")
            if not self.connected and self._job is None:
                if not self.cleanup or self.cleanup.startswith("Closing session"):
                    self.cleanup = ("Disconnected. START outcome unresolved. Device STOP is unconfirmed."
                                    if ctl and ctl.start_uncertain else
                                    "Disconnected. No active link available to confirm device STOP.")
                self._closing = None
                if self._exit_requested:
                    self._disk.shutdown()
                    self.finished = True

    def snapshot(self) -> dict:
        ctl = self.controller
        connected = self.connected
        meta = self.recorder.metadata() if self.recorder else None
        return {
            "connected": connected, "endpoint": self.endpoint,
            "device_state": ctl.state_text if connected else "DISCONNECTED",
            "boot": ctl.boot_id if connected else None,
            "session": (ctl.session_id or None) if connected else None,
            "fault": ctl.fault_code if connected else None,
            "simulated": ctl.simulated if connected else None,
            "start_uncertain": bool(ctl and ctl.start_uncertain),
            "start_wait": ctl.start_recheck_wait_s if ctl else 0.0,
            "capable": bool(connected and ctl.info.get("frames") == "RAW_ENGINEERING"),
            "allowed": ctl.settings.fs_allowed if connected else (),
            "mode": ctl.settings.mode if connected else None,
            "fs": ctl.settings.fs_hz if connected else None,
            "stopped": self.stopped, "configured": self.configured,
            "can_stop": bool(connected and ctl.session_id
                             and ctl.device_state in (ST_STARTING, ST_STREAMING)),
            "recording": self.recorder_state, "recording_used": self.recording_used,
            "recording_open": self.recorder is not None, "recording_path": self.recording_path,
            "recording_meta": self._last_recording_meta if meta is None else meta["raw"],
            "checked_target": self.checked_target,
            "disk_busy": self._job is not None, "closing": self._closing is not None,
            "raw": self.raw.snapshot(), "latest": dict(self.latest_raw),
            "context": self.context, "host_counters": dict(ctl.counters) if ctl else {},
            "notice": self.notice, "error": self.error, "cleanup": self.cleanup,
            "log": tuple(self.log),
        }
