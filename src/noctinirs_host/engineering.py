"""Stage D orchestration. One caller owns the session; disk jobs never own it."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
import time

from .calibration import Assumptions
from .calibration_sweep import Sweep, SweepPlan, canonical, evidence
from .control.engineering_fake import EngineeringFake
from .control.pairing import ADDRESS, PORT, WindowsWifi, candidates, confirm_device
from .control.profile import profile_attribution, profile_codes_event_text, profile_event_lines, profile_summary
from .control.protocol import STATUS_PRECONDITION_FAILED, ST_FAULTED, ST_SAFE_OFF, ST_STARTING, ST_STOPPED, ST_STOPPING, ST_STREAMING
from .control.session import PROFILE_TIMEOUT_S, START_QUIET_S, LoopbackTransport, SessionController
from .control.udp import UDPTransport
from .geometry import Geometry
from .preview.params import PreviewConfig
from .preview.pipeline import PreviewPipeline
from .record.continuity import RawContinuity
from .record.writer import Recorder
from .wire.envelope import parse_envelope
from .wire.optical import parse_raw

UNKNOWN = "Unknown / unavailable"
STOP_WAIT_S = 5.0
# H-05: SAFE_OFF is acknowledged by the comm layer before the firmware control worker has finished its
# cleanup and recorded the STOP evidence (control_off -> control_run_stop). The one diagnostic get-profile
# after SAFE_OFF is therefore deferred. 1.0 s matches the firmware STOP evidence deadline
# (CONTROL_STOP_EVIDENCE_DEADLINE_US); it is an assumption, not a bench measurement. The read is still one
# send with the 0.4 s profile timeout; if it is not allowed before the limit, a "not read" event is written.
# H-05a: both times count from the FIRST SAFE_OFF ACK of the recording; a repeated ACK never moves them.
# The limit is a FINISH bound (fail closed): the read is started only while its whole PROFILE_TIMEOUT_S wait
# still ends by the limit, so no reply is awaited past 3.0 s after the first ACK.
SAFE_OFF_PROFILE_DELAY_S = 1.0
SAFE_OFF_PROFILE_LIMIT_S = 3.0
_FROM_RUN = object()      # H-01a: `_profile_query(candidate=...)` omitted -> the run's id; None = explicitly none
RECORD_STEM = "record_"
_RECORD_FILE = re.compile(r"^record_(\d+)(\.json|\.csv|\.raw\.bin|\.raw\.csv|\.preview\.json|\.iref-v1\.json)$")


def documents_dir() -> Path:
    """The user's Documents folder (honours Windows folder redirection)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            guid = (ctypes.c_byte * 16).from_buffer_copy(
                bytes.fromhex("d0 9a d3 fd 8f 23 af 46 ad b4 6c 85 48 03 69 c7".replace(" ", "")))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                          ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Documents"


def default_recording_folder(documents: Path | None = None, today: date | None = None) -> Path:
    """Documents/noctinirs/<YYYY-MM-DD>. Not created here."""
    return (documents or documents_dir()) / "noctinirs" / (today or date.today()).isoformat()


def next_record_number(folder: Path) -> int:
    """1 for an empty folder, else highest existing record_X plus one. Never reuses a number."""
    highest = 0
    for entry in folder.iterdir():
        m = _RECORD_FILE.match(entry.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


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

    @classmethod
    def prepare(cls, folder: str, firmware_sha: str = "") -> RecordingTarget:
        """Create the folder if needed and choose the next record_X stem (blocking disk work)."""
        path = Path(folder)
        if folder != folder.strip() or not folder or not path.is_absolute():
            raise ValueError("Enter an absolute recording folder with no surrounding whitespace.")
        path.mkdir(parents=True, exist_ok=True)
        target = cls.parse(folder, f"{RECORD_STEM}{next_record_number(path)}", firmware_sha)
        target.check()
        return target

    def check(self) -> None:
        if not self.folder.is_dir():
            raise ValueError("Choose an existing recording folder.")
        for suffix in (".json", ".csv", ".raw.bin", ".raw.csv", ".iref-v1.json"):
            path = self.folder / (self.name + suffix)
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"{path.name} exists. Choose a new stem.")


class EngineeringRun:
    """Runs on the session worker, including all control transactions and callbacks."""

    def __init__(self, geometry: Geometry, host_sha: str | None = None, *,
                 controller_factory=SessionController, recorder_factory=Recorder,
                 fake_options: dict | None = None, calibration_params=None, sweep_plan=None,
                 wifi=None, udp_factory=UDPTransport) -> None:
        self.geometry = geometry
        self.wifi = wifi if wifi is not None else WindowsWifi()
        self._udp_factory = udp_factory
        self.access_points = ()
        self._paired = None
        self._reconnect_at = None
        self._reconnect_tries = 0
        self.fake_options = {"profile_model": True, "stage2_model": True, **(fake_options or {})}
        self.calibration_params = calibration_params or Assumptions()
        self.sweep_plan = sweep_plan or SweepPlan()
        self.sweep = None
        self.calibration = None
        self.calibration_armed = None
        self._recording_calibration = None
        self._recording_apply_verified = False
        self._recording_running_evidence = None
        self._pre_apply_raw = []
        self._pre_apply_overflow = False
        self.preview_config = PreviewConfig()
        self.preview: PreviewPipeline | None = None
        self.preview_active = False
        self.preview_errors = 0
        self.preview_epoch = 0
        self._preview_gap_sent = False
        self._preview_out: list = []
        self._preview_log: list = []
        self._auto: tuple | None = None
        self._auto_wait: tuple | None = None
        self._auto_finalize = False
        self.destination_note = ""
        self.recording_wanted = True
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
        # LED profile diagnostics and per-session calibration evidence.
        self.profile_view: dict | None = None
        self._session_id: int | None = None
        self._await_running = False
        self._running_seen = False
        self._await_end = False
        self._deferred_profile: dict | None = None     # one get-profile scheduled after SAFE_OFF (H-05)

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

    def _require_disconnected(self):
        if self.connected or self._closing is not None or self.recorder is not None:
            raise ValueError("Disconnect and close the recorder before pairing.")
        if self.controller and self.controller.start_uncertain:
            raise ValueError("Resolve the pending START at the original endpoint first.")

    def discover(self):
        self._require_disconnected()
        self.access_points = ()
        self.access_points = tuple(candidates(self.wifi))
        self._note(f"Found {len(self.access_points)} NoctiNIRS Wi-Fi access point(s)." if self.access_points else
                   "No NoctiNIRS found. Check device power, range and Windows Location access.")

    def pair(self, access_point, password=None):
        self._require_disconnected()
        if access_point not in self.access_points:
            raise ValueError("Scan for NoctiNIRS and select an access point.")
        self._paired = self._reconnect_at = None
        self.wifi.associate(access_point, password)
        # DHCP can finish after association. Confirm via INFO, using the same UDP link
        # that the session will own. Never try an endpoint under a different BSSID.
        for attempt in range(3):
            try:
                self.connect(False, ADDRESS, PORT, access_point=access_point)
                return
            except (OSError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(.5)

    def connect(self, fake: bool, address: str, port: str, *, access_point=None) -> None:
        if self.controller and self.controller.start_uncertain:
            raise ValueError("Resolve the pending START at the original endpoint before connecting.")
        if self.connected or self._closing is not None or self.recorder is not None:
            raise ValueError("Finish disconnecting and closing the recorder before connecting.")
        if access_point is not None and not self.wifi.associated(access_point):
            raise ValueError("Windows is no longer joined to the selected device.")
        if access_point is None:
            self._paired = self._reconnect_at = None
        if fake:
            transport = LoopbackTransport(EngineeringFake(geometry=self.geometry, **self.fake_options))
            endpoint = "Local RAW fake (in-process loopback)"
        else:
            if not re.fullmatch(r"[0-9]{1,5}", port):
                raise ValueError("Enter a UDP port from 1 to 65535.")
            transport = self._udp_factory(address, int(port))
            endpoint = f"UDP {transport.endpoint[0]}:{transport.endpoint[1]}"
        self.calibration = self.calibration_armed = None
        self._recording_calibration = None
        self._pre_apply_raw.clear()
        self.endpoint = endpoint
        self.configured = False
        self.raw = RawContinuity()
        self.latest_raw = {}
        self.context = {}
        self.cleanup = ""
        ctl = self.controller = self._controller_factory(transport)
        ctl.listeners.append(lambda event: self._event(event.text))
        ctl.raw_capture_sink = self._capture
        ctl.battery_enabled = True  # read-only query; never blocks or fails the connection
        ctl.profile_enabled = True  # read-only query; "not available" on firmware without get-profile
        self.profile_view = None
        self._await_running = self._await_end = False
        self._session_id = None
        self._deferred_profile = None
        self._result(ctl.connect())
        self.context = ctl.recording_snapshot()
        if access_point is not None:
            info = self.context.get("observed_info") or {}
            if (not confirm_device(info.get("ascii", ""), ctl.info)
                    or not self.wifi.associated(access_point)):
                ctl.disconnect()
                self.context = {}
                raise ValueError("Wi-Fi joined, but the selected endpoint did not confirm NoctiNIRS identity.")
            self._paired = access_point
            self._reconnect_at = None
        self._profile_query("connect")
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
        self.calibration = self.calibration_armed = None
        self.preview_config.filters.validate(fs)
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

    def _calibration_readback(self, *, allow_busy=False):
        result = self.controller.query_profile()
        if allow_busy and result.status == "LOCAL_REJECT":
            return None, None
        if not result.ok:
            raise ValueError(f"Calibration needs get-profile support and a valid reply ({result.status}).")
        report, reply = self.controller.profile, result.reply
        if not report or not report.supported or report.warnings or reply.boot_id != self.controller.boot_id:
            raise ValueError("Calibration profile reply is missing, malformed or from a different boot.")
        return report, reply

    def _stage2(self):
        info = self.controller.info
        minor, api = info.get("wire_minor"), info.get("profile_api")
        if (minor, api) == ("2", "IREF2"):
            return True
        if minor not in (None, "1", "0") or api not in (None, "IREF1"):
            raise ValueError("The device advertises an unsupported calibration wire revision.")
        return False

    def _read_defaults(self, report):
        if self._stage2():
            result = self.controller.query_default_table()
            self._result(result)
            reply = result.reply
            table = reply.default_table
            slots = tuple(slot for slot in range(20) if any(
                ch is not None and ch.kind == "short"
                for ch in (self.geometry.channel_for(slot, i) for i in range(4))))
            if (table is None or table.short_slots != slots or reply.session_id
                    or reply.state != ST_STOPPED or reply.boot_id != self.controller.boot_id):
                raise ValueError("Stage 2 default-table readback is unusable or disagrees with the slot map.")
            return dict(source=table.source, codes=table.codes, short_status=table.short_status,
                        id=table.id, hash=table.hash, short_slots=table.short_slots,
                        contract="S2-1.0")
        codes = report.default_code
        if not codes or any(not 1 <= code <= 133 for code in codes):
            raise ValueError("Firmware returned an invalid fallback table.")
        return dict(source="legacy_get_profile", codes=codes, short_status=None, id=None, hash=None,
                    stage2_readback="unavailable on the FIX6 host path")

    def _arm_calibration_profile(self, profile):
        self._result(self.controller.set_profile(profile.profile_id, bytes.fromhex(profile.profile_hash),
                                                  profile.codes))
        report, reply = self._calibration_readback()
        if (not profile.matches(report) or reply.session_id or reply.state != ST_STOPPED
                or (self._stage2() and report.start_gate != "OPEN")):
            raise ValueError("The device did not read back the armed profile exactly.")
        return evidence(report, reply)

    def calibrate(self):
        self._require_ready()
        if self.recorder is not None or self._closing or self.sweep:
            raise ValueError("Close the recorder before running calibration.")
        self.calibration = self.calibration_armed = None
        context = self._observe(verify_config=True, profile_phase=None)
        self._require_ready()
        report, _ = self._calibration_readback()
        defaults = self._read_defaults(report)
        self.sweep = Sweep(self, context["config"], defaults)
        self._note("Calibration started. Start opens after the sweep and profile readback.")

    def cancel_calibration(self):
        if self.sweep is None:
            return
        self.sweep = None
        self.calibration = self.calibration_armed = None
        ctl = self.controller
        if ctl.start_uncertain:
            self._note("Calibration ended with unresolved START. Use the recovery controls.")
        elif self.connected and ctl.session_id and ctl.device_state in (ST_STARTING, ST_STREAMING):
            self.stop()
        self._note("Calibration cancelled. Run a new sweep before Start.")

    def _require_calibration(self):
        if self.sweep or self.calibration is None:
            raise ValueError("Run a fresh calibration sweep before Start.")

    def _observe(self, *, verify_config: bool = False, profile_phase: str | None = "diagnostics") -> dict:
        ctl = self.controller
        if not self.connected:
            return ctl.recording_snapshot()
        previous_fs = ctl.settings.fs_hz
        info = ctl.refresh_info()
        config = ctl.refresh_config() if ctl.connected and verify_config else None
        status = ctl.refresh_status() if ctl.connected else None
        if profile_phase == "ended" and not self._await_end:
            profile_phase = None                       # this session's end was already read and recorded
        if profile_phase and ctl.connected:
            if self._profile_query(profile_phase) and profile_phase == "ended":
                self._await_end = False
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
        self._require_calibration()
        if self._job is not None or self.recorder is not None or target != self.checked_target:
            raise ValueError("Check an unused recording destination before arming.")
        ctl = self.controller
        self._reset_run_context("new recording armed")      # H-01a: a previous run never labels this recording
        context = self._observe(verify_config=True, profile_phase="armed")
        self._require_ready()
        if context["observed_info"]["source"] != "INFO" or context["firmware_status"] is None:
            raise ValueError("Fresh INFO and STATUS are required to arm RAW recording.")
        profile = self.calibration
        calibration = dict(profile_id=profile.profile_id, profile_hash=profile.profile_hash,
                           profile=profile.document, armed_readback=self.calibration_armed)
        context = {**context, "calibration": calibration}
        self.recorder = self._recorder_factory(
            target.folder, self.geometry, name=target.name, record_raw=True,
            simulated=ctl.simulated, host_sha=self.host_sha, firmware_sha=target.firmware_sha,
            observed_info=context["observed_info"], boot_id=ctl.boot_id,
            settings=lambda: {**context["config"], "device_state": context["device_state"],
                              "preview": self.preview_config.to_metadata()},
        )
        self.recording_path = self.recorder.json_path
        self._last_recording_meta = None
        self._end_context = None
        self._armed_config = context["config"]
        self.recording_used = False
        self.recorder_state = "OPENING"
        self.checked_target = None  # Recorder exclusively creates the files; check again for reuse.
        self._job = self._disk.submit(self._open_calibrated_recorder, self.recorder, context)

    @staticmethod
    def _open_calibrated_recorder(recorder, context):
        calibration = context["calibration"]
        path = recorder.folder / f"{recorder.stem}.iref-v1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical(calibration["profile"])
        with path.open("xb") as file:
            try:
                file.write(payload)
            except OSError:
                file.close()
                path.unlink(missing_ok=True)
                raise
        try:
            recorder.start(raw_context=context)
        except (ValueError, OSError):
            path.unlink(missing_ok=True)
            raise

    def _reset_run_context(self, why: str) -> None:
        """H-01a: forget the previous run's session id, its pending profile reads and any deferred SAFE_OFF
        read before a new recording cycle, so nothing of that run is written into the new sidecar. H-01b:
        also the latest RAW frame, whose epoch is written as `plan_epoch` (START clears it again)."""
        if self._deferred_profile is not None:
            self._event(f"LED profile (after_safe_off): not read ({why} first)")
        self._deferred_profile = None
        self._session_id = None
        self._recording_calibration = None
        self._recording_running_evidence = None
        self._pre_apply_raw = []
        self._pre_apply_overflow = False
        self._await_running = self._await_end = False
        self.latest_raw = {}          # H-01b: the previous run's RAW epoch never labels the new recording

    def start(self) -> None:
        self._require_ready()
        self._require_calibration()
        if self._job is not None or (self.recorder is not None and (
                self.recorder_state != "ARMED" or self.recording_used)):
            raise ValueError("Wait for the recorder or finalize the previous recording.")
        context = self._observe(verify_config=True, profile_phase="pre_start")
        self._require_ready()
        if self.recorder is not None and self._armed_config != context["config"]:
            raise ValueError("Settings changed since arming. Finalize and arm a new recording.")
        profile = self.calibration
        self.calibration = self.calibration_armed = None  # consume before attempting START
        report, reply = self._calibration_readback()
        if (profile.document["boot_id"] != reply.boot_id or profile.document["configuration"] != context["config"]
                or not profile.matches(report) or self._read_defaults(report) != profile.document["default_table"]
                or reply.session_id or reply.state != ST_STOPPED):
            raise ValueError("Calibration settings or profile changed. Run a new sweep.")
        self._recording_calibration = profile
        self._clip_reported = False
        self._recording_apply_verified = False
        self._recording_running_evidence = None
        self._pre_apply_raw = []
        self._pre_apply_overflow = False
        self.raw = RawContinuity()
        self.latest_raw = {}
        self._end_preview("new START")
        self.preview_epoch += 1
        self._preview_gap_sent = False
        self.preview = PreviewPipeline(self.geometry, self.preview_config,
                                       self.controller.settings.fs_hz or 10,
                                       calibration_headroom=self.calibration_params.headroom)
        self._session_id = None                 # a previous run's session never labels this one
        self._await_running = self._await_end = False
        if self._stage2():
            self._arm_calibration_profile(profile)  # S2-1.0 requires SET_PROFILE immediately before each START.
        result = self.controller.start()
        if result.reply and result.reply.status == STATUS_PRECONDITION_FAILED and self._stage2():
            report, _ = self._calibration_readback()
            if report.start_gate in ("NO_PROFILE", "PROFILE_CONSUMED"):
                raise ValueError("Firmware rejected Start because the profile was missing or consumed. Run a new sweep.")
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
                # H-05: no session was ever acknowledged, so no end read can be attributed to this recording
                self.recorder.event("profile", "phase=end_missing attributed=0 basis=start_unresolved "
                                    "end_evidence=MISSING reason=START ownership was never established "
                                    "(no START ACK); no end get-profile read belongs to this recording")
                self._finalize(context)
            return
        if result.ok:
            self._session_id = self.controller.session_id or None
            self._await_running = self._await_end = True      # read the profile report when it is known
            self._running_seen = False
        if result.ok and self.recorder is not None:
            self.recording_used = True
            self.recorder_state = "RECORDING"
        self._check_mutation(result)

    def start_recorded(self, folder: str, firmware_sha: str = "") -> None:
        """START with automatic RAW recording: folder + next record_X, arm, then START.

        Every step after the destination work happens from `_poll_disk` on this same worker."""
        self._require_ready()
        self._require_calibration()
        if self.recorder is not None:
            raise ValueError("Wait for the recorder or finalize the previous recording.")
        folder = folder.strip()
        RecordingTarget.parse(folder, "record_1", firmware_sha)  # validate input on the caller
        self._auto_wait = (folder, firmware_sha)  # a housekeeping disk job may still be running
        self._launch_auto()

    def _launch_auto(self) -> None:
        if self._auto_wait is None or self._job is not None:
            return
        folder, firmware_sha = self._auto_wait
        self._auto_wait = None
        try:
            self._require_ready()
        except ValueError as exc:
            self.fail(str(exc))
            return
        self._auto = (folder, firmware_sha)
        self.checked_target = None
        self._job = self._disk.submit(RecordingTarget.prepare, folder, firmware_sha)

    def preview_destination(self, folder: str, firmware_sha: str = "") -> None:
        """Tell the operator which record_X the next START would use (no files created)."""
        if self._job is not None or self.recorder is not None:
            return
        folder = folder.strip()
        RecordingTarget.parse(folder, "record_1", firmware_sha)
        self._job = self._disk.submit(self._peek, folder)

    @staticmethod
    def _peek(folder: str) -> str:
        path = Path(folder)
        number = next_record_number(path) if path.is_dir() else 1
        return f"{path / (RECORD_STEM + str(number))}"

    def check_start_outcome(self) -> None:
        if self.controller is None:
            raise ValueError("No pending START.")
        result = self.controller.check_start_outcome()
        self._result(result)
        self._note(f"START outcome resolved by device report: {result.reply.state_text}. "
                   "Physical LED state has not been measured.")

    def safe_off(self) -> None:
        self.sweep = self.calibration = self.calibration_armed = None
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
        reply = result.reply
        recording = self.recorder is not None and self.recorder_state in ("ARMED", "RECORDING")
        if recording:
            # H-05: the ACK is its own event; the STOP evidence is written by the firmware after cleanup
            pending = self._deferred_profile is not None
            self.recorder.event("safe_off", (
                f"SAFE_OFF ACK reply_session={reply.session_id} reply_state={reply.state_text} "
                f"boot=0x{reply.boot_id:X}; STOP evidence is recorded by the firmware after its cleanup; "
                + ("repeated ACK: the first ACK's deferred get-profile schedule is kept" if pending else
                   f"one get-profile read deferred {SAFE_OFF_PROFILE_DELAY_S:g} s")
                + (" (link lost: no read possible)" if not self.connected else ""))[:500])
        if self._deferred_profile is not None and self.connected:
            # H-05a: a repeated ACK neither shifts the due time or the finish bound nor adds a read
            self._await_end = self._await_running = False
            return
        candidate = self._session_id if self._await_end else None
        self._await_end = self._await_running = False
        if not self.connected:
            self._deferred_profile = None
            if recording:
                self.recorder.event("profile", "phase=after_safe_off not read: no live link")
                self._finalize(ctl.recording_snapshot())
            return
        now = time.monotonic()
        self._deferred_profile = {"due": now + SAFE_OFF_PROFILE_DELAY_S, "limit": now + SAFE_OFF_PROFILE_LIMIT_S,
                                  "candidate": candidate}

    def clear_fault(self):
        if not self.connected or self.controller.device_state not in (ST_FAULTED, ST_SAFE_OFF):
            raise ValueError("Recovery requires a connected device reporting FAULTED or SAFE_OFF.")
        if self.recorder is not None or self._deferred_profile or self._closing is not None:
            raise ValueError("Wait for recording cleanup before clearing the fault.")
        self.sweep = self.calibration = self.calibration_armed = None
        self.configured = False
        self._check_mutation(self.controller.clear_fault())
        self._note("Fault cleared. Apply configuration and run a fresh calibration.")

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
        self._finalize(self._observe(profile_phase="ended"))

    # ---- LED calibration profile report (read-only) ------------------------------------------------
    def _profile_query(self, phase: str, *, candidate=_FROM_RUN) -> bool:
        """Read get-profile once and remember it for the status area; when a recorder is open, write what the
        device reported (or that it could not be read) as sidecar events. False = not allowed right now
        (nothing was sent; the caller may try again later). A missing, old or odd reply never fails the
        session.

        H-01: the report is device-wide. It is attributed to this run only when the request carried the live
        owned session id and the reply reports that same id (`basis=owner`). A reply naming another session
        is a device diagnostic (`basis=foreign`, sidecar kind `profile_device`, no `session=`). Session 0
        after this run ended is `basis=detached`: unattributed, with the run's id only as a candidate.

        H-01a: `candidate` omitted means "this run's id" (`_session_id`); an explicit None means that no
        run of this recording can be named and is never replaced by `_session_id`."""
        ctl = self.controller
        if ctl is None or not ctl.profile_enabled or not self.connected:
            return False
        sent_session = ctl.session_id if ctl.device_state in (ST_STARTING, ST_STREAMING, ST_STOPPING) else 0
        result = ctl.query_profile()
        if result.status == "LOCAL_REJECT":
            return False
        report = ctl.profile if (result.ok or result.status == "UNKNOWN_COMMAND") else None
        failure = "" if report is not None else f"get-profile {result.status}: {result.message}"
        reply = result.reply
        provenance = None
        reply_state = None
        if reply is not None:
            provenance = profile_attribution(reply.session_id, sent_session=sent_session, phase=phase,
                                             candidate_session=self._session_id if candidate is _FROM_RUN
                                             else candidate)
            reply_state = reply.state_text
        self.profile_view = {"phase": phase, "report": report, "failure": failure,
                             "boot_id": ctl.boot_id, "provenance": provenance, "reply_state": reply_state}
        self._event(f"LED profile ({phase}): " + (profile_summary(report).splitlines()[0] if report else failure)
                    + (f" [{provenance['basis']}]" if provenance else ""))
        if self.recorder is not None and self.recorder_state in ("ARMED", "RECORDING"):
            foreign = bool(provenance and provenance["basis"] == "foreign")
            kind = "profile_device" if foreign else "profile"
            if report is None:
                self.recorder.event(kind, f"phase={phase} not read: {failure}"[:500])
            else:
                epoch = self.latest_raw.get("epoch")
                boot = reply.boot_id if reply is not None else ctl.boot_id
                lines = profile_event_lines(
                    report, phase=phase, boot_id=boot, plan_epoch=epoch,
                    provenance=provenance or {"attributed": False, "basis": "no_session", "reply_session": 0},
                    reply_state=reply_state)
                # H-02a: the follow-ons come from the same structured result, never from scanning the text.
                # H-02c: main line, every follow-on and the code vectors are stored as one group or not at
                # all; a group that does not fit leaves one explicit `incomplete=1` event (no orphan
                # `in_profile_ids` marker), or, without room even for that, an event drop.
                group = [(kind, lines.main)] + [(kind + "_ids", ids) for ids in lines.ids]
                codes = profile_codes_event_text(report)
                if codes and not foreign:
                    group.append(("profile_codes", codes))
                stored = self.recorder.event_group(group, incomplete=(kind, lambda needed, available: (
                    f"phase={phase} incomplete=1 reason=event_limit needed={needed} available={available}")))
                if not stored:
                    self._event(f"LED profile ({phase}): sidecar event limit reached; report group not stored")
        return True

    def _deferred_follow(self) -> None:
        """H-05: the one get-profile after SAFE_OFF, once the firmware has had time to record its STOP
        evidence. Then the recording (if any) is finalized. Bounded: one send at most, and a "not read"
        event when the read is not allowed before the limit or the link is gone. H-05a: the limit is a finish
        bound; a read whose PROFILE_TIMEOUT_S wait could end after it is not started (fail closed)."""
        d = self._deferred_profile
        if d is None:
            return
        now = time.monotonic()
        if self.connected and now < d["due"]:
            return
        done = False
        if self.connected and now + PROFILE_TIMEOUT_S <= d["limit"]:
            done = self._profile_query("after_safe_off", candidate=d["candidate"])
            if not done:
                return                          # not allowed right now; nothing was sent
        self._deferred_profile = None
        recording = self.recorder is not None and self.recorder_state in ("ARMED", "RECORDING")
        if not done:
            reason = "no live link" if not self.connected else \
                f"could not finish within {SAFE_OFF_PROFILE_LIMIT_S:g} s of the first SAFE_OFF ACK"
            self._event(f"LED profile (after_safe_off): not read ({reason})")
            if recording:
                self.recorder.event("profile", f"phase=after_safe_off not read: {reason}")
        if recording:
            self._finalize(self.controller.recording_snapshot() if self.controller else None)

    def _profile_follow(self) -> None:
        """Read the report when the owned session reaches STREAMING and again when it has ended (normal STOP,
        refused or failed START, fault, lease loss), before any recorder finalization."""
        ctl = self.controller
        if self._await_running and ctl.session_id and ctl.device_state == ST_STREAMING:
            if self._running_seen:
                if self._profile_query("running"):
                    self._await_running = False
                    profile = self._recording_calibration
                    if profile is not None:
                        view = self.profile_view
                        self._recording_apply_verified = bool((view.get("provenance") or {}).get("attributed")
                            and view["reply_state"] == "STREAMING"
                            and profile.matches(view["report"], applied=True)
                            and profile.table_matches(view["report"], profile.document["default_table"]))
                        if self._recording_apply_verified and not self._pre_apply_overflow:
                            self._recording_running_evidence = dict(boot_id=view["boot_id"],
                                session_id=self._session_id, generation=None, report=view["report"].text)
                            self.preview_active = True
                            frames, self._pre_apply_raw = self._pre_apply_raw, []
                            for frame in frames:
                                self._capture(*frame)
                        else:
                            self._recording_apply_verified = False
                            self._pre_apply_raw.clear()
                            self.fail("Applied profile readback failed. Stopping acquisition. Run a new sweep.")
                            try:
                                self.stop()
                            except ValueError as exc:
                                self.fail(str(exc))
            else:
                self._running_seen = True       # the first owner heartbeat goes out before this read
        if (self._await_end and not ctl.session_id
                and ctl.device_state not in (ST_STARTING, ST_STREAMING, ST_STOPPING, None)):
            if self._profile_query("ended"):
                self._await_end = self._await_running = False

    def _finalize(self, context: dict | None) -> None:
        if self.recorder is None or self._job is not None:
            if self.recorder_state == "OPENING":
                self._end_context = context
            return
        ctl = self.controller
        ctl.listeners.remove(self.recorder.on_controller_event)
        if self._recording_calibration is not None and self.recording_used:
            view = self.profile_view or {}
            report = view.get("report") if view.get("phase") in ("ended", "after_safe_off") else None
            stop = dict(report=report.text, boot_id=view.get("boot_id"),
                        provenance=view.get("provenance"), generation=None,
                        reported_integrity=report.stop_integrity_effective) if report else None
            context = {**(context or {}), "calibration_end": {
                "running": self._recording_running_evidence, "stop_observation": stop,
                "generation_verified": False,
                "headroom_exceeded": self._clip_reported, "pre_apply_overflow": self._pre_apply_overflow}}
        self.recorder_state = "FINALIZING"
        self._end_preview("recording finalized")
        provenance = self.preview.provenance() if self.preview is not None else None
        self._job = self._disk.submit(self._stop_recorder, self.recorder, context, provenance)

    @staticmethod
    def _stop_recorder(recorder: Recorder, context: dict | None, provenance: dict | None) -> bool:
        ok = recorder.stop(raw_context=context)
        if provenance is not None:
            # Derived-parameter provenance goes to its own file; the RAW archive is never touched.
            path = recorder.folder / f"{recorder.stem}.preview.json"
            try:
                with open(path, "x", encoding="utf-8") as fh:
                    json.dump(provenance, fh, indent=2, default=str)
            except OSError:
                return False
        return ok

    # ---- PREVIEW (never affects RAW recording) ----------------------------
    def set_preview_config(self, config: PreviewConfig) -> None:
        config.validate()
        fs = self.controller.settings.fs_hz if self.connected else None
        config.filters.validate(fs or 10)
        old_filters = self.preview_config.filters
        if self.preview is not None:
            self.preview.configure(config)
        self.preview_config = config
        if self.recorder is not None and self.recorder_state in ("ARMED", "RECORDING"):
            if config.filters != old_filters:
                self.recorder.event("preview_filter", canonical(config.filters.metadata()).decode("ascii"))
            self.recorder.event("preview_config", f"PREVIEW settings changed: ssr={config.ssr_mode} "
                                f"dpf={config.dpf.mode} baseline_s={config.baseline_s} "
                                "(full record in .preview.json)")
        self._event("PREVIEW settings applied.")

    def reset_preview_baseline(self) -> None:
        if self.preview is None:
            raise ValueError("No PREVIEW is running.")
        self.preview.reset_baseline()
        if self.recorder is not None and self.recorder_state in ("ARMED", "RECORDING"):
            self.recorder.event("preview_baseline_reset", "PREVIEW baseline reset by operator")
        self._event("PREVIEW baseline reset.")

    def _preview_feed(self, raw, host_us: int, msg_sequence: int | None = None) -> None:
        if self.preview is None:
            return
        try:
            self._preview_out.extend(self.preview.feed(raw, host_us, msg_sequence))
            if self._recording_calibration is not None and self.preview.headroom_exceeded and not self._clip_reported:
                self._clip_reported = True
                warning = "Calibration headroom exceeded. Check room light and probe fit, then Stop and run a new sweep."
                self._note(warning)
                if self.recorder is not None and self.recorder_state == "RECORDING":
                    self.recorder.event("calibration_integrity", "CLIP_DETECTED: affected occurrence invalid. " + warning)
        except Exception:  # preview faults must not disturb capture or recording
            self.preview_errors += 1

    def _preview_gap(self, reason: str) -> None:
        if self.preview is None or self._preview_gap_sent:
            return
        self._preview_gap_sent = True  # one gap marker per run of the preview, clean or not
        try:
            marker = self.preview.note_gap(reason)
            self._preview_out.extend(self.preview.pending)
            if marker is not None:
                self._preview_out.append(marker)
        except Exception:
            self.preview_errors += 1

    def _end_preview(self, reason: str) -> None:
        if self.preview is not None and self.preview_active:
            try:
                self._preview_out.extend(self.preview.flush())
            except Exception:
                self.preview_errors += 1
        self.preview_active = False

    def _capture(self, wire: bytes, host_us: int, boot_id: int | None) -> None:
        env = parse_envelope(wire)
        raw = parse_raw(env.payload)
        if self.sweep is not None:
            self.sweep.feed(raw, wire, host_us)
            return
        if self._recording_calibration is not None and not self._recording_apply_verified:
            if len(self._pre_apply_raw) < 512:
                self._pre_apply_raw.append((wire, host_us, boot_id))
            else:
                self._pre_apply_overflow = True
            return
        if self.recorder_state in ("ARMED", "RECORDING"):
            self.recorder.feed_raw(wire, host_us, boot_id)
        self.raw.observe(env, raw, host_us, boot_id)
        self.latest_raw = {"sample": raw.samples[-1], "first_device_us": env.timestamp_us,
                           "host_us": host_us, "estimated": env.ts_estimated,
                           "simulated": env.simulated, "epoch": raw.slot_plan_epoch}
        self._preview_feed(raw, host_us, env.msg_sequence)

    def disconnect(self) -> None:
        self._paired = self._reconnect_at = None
        if self._closing is not None:
            return
        self.sweep = self.calibration = self.calibration_armed = None
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
        self.sweep = self.calibration = self.calibration_armed = None
        ctl = self.controller
        self._event(message)
        self._preview_gap("disconnected")
        if self._deferred_profile is not None:
            self._deferred_profile = None
            if ctl and self.recorder_state in ("ARMED", "RECORDING"):
                self.recorder.event("profile", "phase=after_safe_off not read: link closed first")
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
        self._preview_gap("link lost")
        self._end_preview("link lost")
        self.fail(message)
        self._close_link(message, ctl.recording_snapshot() if ctl else None)
        if self._paired and not self._closing and not (ctl and ctl.start_uncertain):
            self._reconnect_at = time.monotonic() + START_QUIET_S + .5
            self._reconnect_tries = 0

    def _reconnect(self):
        if (self._reconnect_at is None or time.monotonic() < self._reconnect_at
                or self.recorder is not None or self._job is not None or self._closing is not None):
            return
        self._reconnect_at = None
        try:
            self.wifi.associate(self._paired)
            self.connect(False, ADDRESS, PORT, access_point=self._paired)
        except (ValueError, OSError) as exc:
            self._reconnect_tries += 1
            if self._reconnect_tries < 2:
                self._reconnect_at = time.monotonic() + 3
            else:
                self.fail(f"Could not reconnect to the selected device. Scan and connect again. {exc}")
        else:
            self.error = ""
            self._note("Device reconnected. Apply configuration and run a new calibration before Start.")

    def _poll_disk(self) -> None:
        if self._job is None or not self._job.done():
            return
        job, self._job = self._job, None
        try:
            result = job.result()
        except Exception as exc:
            self._auto = None
            self.fail(f"Recording destination or writer error: {type(exc).__name__}: {exc}")
            if self.recorder is not None:
                self.recorder_state = "INCOMPLETE / ERROR"
                self.recorder = None
            return
        if isinstance(result, str):
            self.destination_note = result
        elif isinstance(result, RecordingTarget):
            self.checked_target = result
            self._note("Destination checked for existing files. Arming reserves files exclusively.")
            if self._auto is not None:
                try:
                    self.arm(result)
                except (ValueError, OSError) as exc:
                    self._auto = None
                    self.fail(str(exc))
        elif self.recorder_state == "OPENING":
            self.controller.listeners.append(self.recorder.on_controller_event)
            self.recorder_state = "ARMED"
            self._note(f"RAW recording armed: {self.recording_path}")
            if not self.connected or not self.stopped or self._closing is not None:
                self.recorder.event("cleanup", self.cleanup or "Device no longer STOPPED while arming.")
                self._auto = None
                self._finalize(self._end_context)
            elif self._auto is not None:
                self._auto = None
                try:
                    self.start()
                    self._auto_finalize = self.recording_used
                except (ValueError, OSError) as exc:
                    self.fail(str(exc))
                    if self.recorder is not None and self.recorder_state == "ARMED"                             and not self.recording_used and self._job is None:
                        self._finalize(self.controller.recording_snapshot())
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
            if ctl.connected and ctl.counters["timeouts"] == timeouts:
                self._profile_follow()
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
        if self.sweep is not None:
            try:
                result = self.sweep.tick()
                if result is not None:
                    self.calibration, self.calibration_armed = result
                    self.sweep = None
                    suffix = "" if self._stage2() else " FIX6 has no Stage 2 table readback."
                    self._note("Calibration verified. Ready for one Start." + suffix)
            except (ValueError, OSError) as exc:
                try:
                    self.cancel_calibration()
                except (ValueError, OSError) as stop_error:
                    self.fail(str(stop_error))
                self.fail(str(exc))
        if ctl:
            ctl.events.clear()  # Only this workflow's bounded log and recorder retain events.
            for line in ctl.battery_log:  # session log only; never the recording sidecar
                self._event(line)
            ctl.battery_log.clear()
        if self.preview_active and self.stopped:
            self._end_preview("session ended")
        self._deferred_follow()
        self._poll_disk()
        self._launch_auto()
        self._reconnect()
        if (self._auto_finalize and self.recorder_state in ("ARMED", "RECORDING") and self.stopped
                and self.recording_used and self._job is None and self._closing is None):
            self._auto_finalize = False
            try:
                self.finalize()
            except (ValueError, OSError) as exc:
                self.fail(str(exc))
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

    def _battery_view(self) -> dict:
        ctl = self.controller
        if ctl is None or not ctl.connected:
            return {"status": "DISCONNECTED", "connected": False}
        return {**ctl.battery.snapshot(ctl._clock()), "connected": True,
                "streaming": ctl.device_state in (ST_STARTING, ST_STREAMING, ST_STOPPING)}

    def _profile_state_view(self) -> dict:
        view = self.profile_view
        if view is None:
            return {"summary": profile_summary(None), "report": None, "phase": None}
        summary = profile_summary(view["report"], failure=view["failure"])
        prov = view.get("provenance")
        if prov is not None and view["report"] is not None and view["report"].supported:
            summary += "\n" + {
                "owner": f"Report bound to this host's session {prov.get('session')}.",
                "foreign": f"Report while another owner holds session {prov.get('reply_session')}: device "
                           "diagnostic, not evidence for this host's run.",
                "detached": f"Device-wide report read after session {prov.get('candidate_session')} ended "
                            "(session 0): not bound to that run.",
                "no_session": "Device-wide report; no session of this host.",
            }.get(prov.get("basis"), "")
        return {"summary": summary, "report": view["report"], "phase": view["phase"], "provenance": prov,
                "integrity_blocked": bool(view["report"] and view["report"].integrity_blocked)}

    def _drain_preview(self) -> list:
        out, self._preview_out = self._preview_out, []
        return out

    def snapshot(self) -> dict:
        ctl = self.controller
        connected = self.connected
        meta = self.recorder.metadata() if self.recorder else None
        return {
            "connected": connected, "endpoint": self.endpoint,
            "access_points": self.access_points,
            "reconnecting": self._reconnect_at is not None,
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
            "calibration_ready": self.calibration is not None and self.stopped and self.configured,
            "calibrating": self.sweep is not None,
            "calibration_status": self.sweep.progress if self.sweep else (
                "Profile verified. Ready for one Start." if self.calibration else "A fresh sweep is required."),
            "calibration_flags": sorted({flag for slot in self.calibration.document["slots"]
                                         for flag in slot["selection"]["flags"]}) if self.calibration else [],
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
            "battery": self._battery_view(),
            "profile": self._profile_state_view(),
            "preview_new": self._drain_preview(), "preview_errors": self.preview_errors,
            "preview_loss": self.preview.loss_stats() if self.preview is not None else {},
            "preview_active": self.preview_active, "preview_epoch": self.preview_epoch,
            "preview_config": self.preview_config,
            "preview_limits": self.preview.provenance()["limits"] if self.preview else (),
            "destination_note": self.destination_note,
            "geometry_draft": self.geometry.is_draft, "geometry_status": self.geometry.status,
        }
