"""Read-only LED calibration profile support (firmware Calib Stage-1 FIX1) and host compatibility.

Evidence class: host/unit and simulation. The "firmware" here is the EngineeringFake / Peer model written from
the patch sources and the FIX1 report; nothing is a device capture and no firmware was changed or run.
Text fixtures are produced by `encode_profile_text`, which mirrors the C builder in nn2_app_comm.c
(patch 0001 + 0003), and the 1404-byte worst case is checked against the figure the firmware report gives.
"""

import json
from pathlib import Path
import struct

import pytest

from noctinirs_host.control import protocol as P
from noctinirs_host.control.engineering_fake import EngineeringFake
from noctinirs_host.control.profile import (
    DOCUMENTED_EVIDENCE, EVENT_LIMIT, IREF_CODE_MAX, MAX_TEXT, PROFILE_ARG_BYTES, args_set_profile,
    encode_profile_text, parse_profile, profile_codes_event_text, profile_event_text, profile_summary,
)
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.engineering import EngineeringRun
from calibration_fixture import seed_calibration
from noctinirs_host.geometry import load_geometry
from noctinirs_host.wire.envelope import MAX_PAYLOAD
from test_engineering_run import GEOMETRY

HASH = bytes(range(16, 48))
CODES = [133] * 20
EVIDENCE_CASES = [
    ("NONE", "UNVERIFIED"), ("VALID_MATCH", "OK"), ("VALID_MISMATCH", "BLOCKED"),
    ("TRANSPORT_ERR", "BLOCKED"), ("TIMEOUT", "UNVERIFIED"), ("SKIPPED_EMERGENCY", "UNVERIFIED"),
    ("NOT_RUN", "UNVERIFIED"), ("UNAVAILABLE", "UNVERIFIED"),          # FIX2 (patch 0004)
]


def applied(**kw):
    base = dict(state="APPLIED", armed=True, profile_id=0xA1B2C3D4, profile_hash=HASH, profile_code=CODES,
                source="PROFILE", start_result="OK", applied_profile_id=0xA1B2C3D4, applied_profile_hash=HASH,
                requested_code=CODES, applied_code=CODES, applied_read=True, applied_valid=True)
    base.update(kw)
    return encode_profile_text(**base)


# ---- the format ------------------------------------------------------------------------------------------

def test_a_fix1_report_with_a_profile_applied_is_parsed_field_by_field():
    r = parse_profile(applied())
    assert r.supported and r.state == "APPLIED" and r.armed is True and r.source == "PROFILE"
    assert r.profile_id == 0xA1B2C3D4 and r.applied_profile_id == 0xA1B2C3D4
    assert r.profile_hash == HASH.hex() and r.applied_profile_hash == HASH.hex()
    assert r.profile_code == tuple(CODES) and r.requested_code == tuple(CODES) and r.applied_code == tuple(CODES)
    assert r.applied_read is True and r.applied_valid is True and r.start_result == "OK"
    assert r.default_code == tuple([133] * 20)
    assert r.requested_ua == tuple([133 * 225] * 20) and r.applied_ua == tuple([133 * 225] * 20)   # nominal 225 uA/code
    assert (r.iref_ua_per_lsb, r.iref_code_default, r.iref_code_max) == (225, 133, 222)
    assert r.warnings == () and r.unknown == () and r.ignored == ()


def test_no_profile_armed_reports_none_and_default_codes():
    r = parse_profile(encode_profile_text(source="DEFAULT", requested_code=CODES, applied_code=CODES,
                                          applied_read=True, applied_valid=True, start_result="OK"))
    assert r.state == "NONE" and r.armed is False and r.profile_hash is None and r.profile_code is None
    assert r.source == "DEFAULT" and r.profile_id == 0


@pytest.mark.parametrize("evidence,integrity", EVIDENCE_CASES)
def test_every_stop_evidence_class_is_parsed_and_the_integrity_follows_the_device(evidence, integrity):
    extra = dict(stop_evidence=evidence, applied_at_stop=CODES if evidence.startswith("VALID") else None,
                 stop_slot=6 if evidence == "VALID_MISMATCH" else -1,
                 stop_error=263 if evidence == "TRANSPORT_ERR" else 0)
    r = parse_profile(applied(state="ARMED", applied_valid=False, **extra))
    assert r.stop_evidence == evidence and r.stop_integrity == integrity
    assert r.stop_integrity_effective == integrity and r.warnings == ()
    assert r.stop_match is (evidence == "VALID_MATCH")
    assert r.stop_slot == extra["stop_slot"] and r.stop_error == extra["stop_error"]
    assert r.integrity_blocked is (integrity == "BLOCKED")
    text = profile_event_text(r, phase="ended", boot_id=0x1122, session_id=3)
    assert f"stop_evidence={evidence}" in text and f"stop_integrity={integrity}" in text
    assert ("DATA_INTEGRITY_BLOCKED" in text) is (integrity == "BLOCKED")
    assert len(text) <= EVENT_LIMIT


def test_a_preempted_name_is_tolerated_but_never_becomes_verified():
    r = parse_profile(applied(stop_evidence="PREEMPTED", stop_integrity="UNVERIFIED"))
    assert r.stop_evidence == "PREEMPTED" and r.warnings == ()          # a known name of the capture result
    assert r.stop_integrity_effective == "UNVERIFIED" and "PREEMPTED" not in DOCUMENTED_EVIDENCE
    r2 = parse_profile(applied(stop_evidence="PREEMPTED", stop_integrity="OK"))
    assert r2.stop_integrity_effective == "UNVERIFIED"                  # a device claim the host will not repeat


def test_the_integrity_is_never_inferred_when_the_device_contradicts_itself_or_says_nothing():
    contradiction = parse_profile(applied(stop_evidence="TIMEOUT", stop_integrity="OK"))
    assert contradiction.stop_integrity == "OK" and contradiction.stop_integrity_effective == "UNVERIFIED"
    assert any("contradicts" in w for w in contradiction.warnings)
    unknown_name = parse_profile(applied(stop_evidence="SOMETHING_NEW", stop_integrity="OK"))
    assert unknown_name.stop_evidence == "SOMETHING_NEW" and unknown_name.stop_integrity_effective == "UNVERIFIED"
    assert any("unknown stop_evidence" in w for w in unknown_name.warnings)
    missing = parse_profile(applied().replace(b" stop_integrity=UNVERIFIED", b""))   # key absent
    assert missing.stop_integrity is None and missing.stop_integrity_effective == "UNVERIFIED"


def worst_case():
    h = bytes([0xAB]) * 32
    return encode_profile_text(
        state="NOT_APPLIED", armed=True, profile_id=0xFFFFFFFF, profile_hash=h, profile_code=[222] * 20,
        last_reject="CODE_ABOVE_MAX", reject_slot=19, source="PROFILE", start_result="PROFILE_NOT_APPLIED",
        mismatch_slot=19, applied_profile_id=0xFFFFFFFF, applied_profile_hash=h, requested_code=[222] * 20,
        applied_code=[222] * 20, applied_read=True, applied_valid=False, applied_at_stop=[222] * 20,
        stop_evidence="SKIPPED_EMERGENCY", stop_integrity="UNVERIFIED", stop_slot=19, stop_error=-2147483648,
        legacy_requested_ma=[50] * 20)


def test_the_worst_case_report_is_1404_bytes_fits_one_reply_and_parses_without_warnings():
    text = worst_case()
    assert len(text) == 1404 <= MAX_TEXT                                  # the figure in the FIX1 report
    assert 8 + 20 + len(text) <= MAX_PAYLOAD                              # reply prefix + text in one datagram
    r = parse_profile(text)
    assert r.warnings == () and r.unknown == ()
    assert r.stop_error == -2147483648 and r.stop_slot == 19 and r.reject_slot == 19
    assert r.last_reject == "CODE_ABOVE_MAX" and r.start_result == "PROFILE_NOT_APPLIED"
    assert r.legacy_requested_ma == tuple([50] * 20) and r.requested_ua == tuple([222 * 225] * 20)
    datagram = P.encode_reply(7, P.OP_GET_PROFILE, 0, state=1, fault=0, simulated=False, boot_id=9, t_rx_us=1,
                              extension=text)
    reply = P.decode_reply(datagram)
    assert reply.ok and reply.profile.state == "NOT_APPLIED" and len(reply.extension) == 1404


# ---- unknown fields and other firmware generations -----------------------------------------------------------

def test_unknown_keys_are_ignored_safely_and_kept_for_the_record():
    r = parse_profile(applied(extra="future_key=abc9 another=1,2,3"))
    assert r.state == "APPLIED" and r.warnings == ()
    assert dict(r.unknown) == {"future_key": "abc9", "another": "1,2,3"}
    text = profile_event_text(r, phase="running")
    assert "unknown_keys=future_key,another" in text and "state=APPLIED" in text


def test_a_longer_unknown_value_a_new_enum_name_and_a_newer_minor_do_not_raise():
    r = parse_profile(applied(extra="vendor_blob=" + "x" * 500, start_result="START_ABORTED"))
    assert r.start_result == "START_ABORTED" and any("unknown start_result" in w for w in r.warnings)
    assert len(dict(r.unknown)["vendor_blob"]) <= 120
    newer = parse_profile(b"v4profile=2 whatever=1")
    assert not newer.supported and newer.reason.startswith("unknown_version") and newer.warnings


def test_the_pre_fix1_report_with_stop_read_is_accepted_and_stop_read_is_ignored():
    old = parse_profile(applied(state="ARMED", legacy_stop_read=True, stop_match=True))
    assert old.supported and "stop_read" in old.ignored and old.stop_evidence is None
    assert old.stop_match is True and old.stop_integrity_effective == "UNVERIFIED"   # no evidence, no claim
    assert old.warnings == ()


@pytest.mark.parametrize("text,reason", [
    (b"v4profile=0 reason=unsupported", "unsupported"), (b"v4profile=0 reason=overflow", "overflow"),
    (b"", "empty_reply"), (b"   ", "empty_reply"), (b"hello world", "unrecognised_reply"),
    (b"\xff\xfe\x00", "unrecognised_reply")])
def test_unsupported_empty_or_garbage_replies_are_reported_not_raised(text, reason):
    r = parse_profile(text)
    assert not r.supported and r.reason == reason
    assert "not available" in profile_summary(r) and len(profile_event_text(r, phase="connect")) <= EVENT_LIMIT


def test_odd_values_of_known_keys_are_flagged_and_dropped_not_guessed():
    text = applied().replace(b"applied_valid=1", b"applied_valid=maybe").replace(b"stop_slot=-1", b"stop_slot=x")
    r = parse_profile(text)
    assert r.applied_valid is None and r.stop_slot is None
    assert {"unusable value for applied_valid", "unusable value for stop_slot"} <= set(r.warnings)
    short = parse_profile(applied().replace(b"applied_code=" + b",".join([b"133"] * 20), b"applied_code=133,133"))
    assert short.applied_code is None and any("applied_code" in w for w in short.warnings)
    dup = parse_profile(applied() + b" profile_state=NONE")
    assert dup.state == "APPLIED" and any("duplicate key" in w for w in dup.warnings)


def test_a_code_outside_the_accepted_domain_is_flagged_not_clamped():
    r = parse_profile(applied(applied_code=[223] + [133] * 19))
    assert r.applied_code[0] == 223 and any("above 222" in w for w in r.warnings)


# ---- requests (protocol layer only) ------------------------------------------------------------------------------

def test_set_profile_arguments_are_56_bytes_and_refuse_what_the_firmware_refuses():
    args = args_set_profile(0xA1B2C3D4, HASH, [1] + [133] * 18 + [222])
    assert len(args) == PROFILE_ARG_BYTES == 56 and args[:4] == struct.pack("<I", 0xA1B2C3D4)
    assert args[4:36] == HASH and args[36] == 1 and args[-1] == 222
    for bad in ((0, HASH, CODES), (1 << 32, HASH, CODES), (1, HASH[:31], CODES), (1, HASH, CODES[:19]),
                (1, HASH, [0] + CODES[1:]), (1, HASH, [IREF_CODE_MAX + 1] + CODES[1:]), (1, HASH, [True] * 20)):
        with pytest.raises(ValueError):
            args_set_profile(*bad)                                         # never clamped
    assert P.OP_SET_PROFILE == 0x0103 and P.OP_CLEAR_PROFILE == 0x0104 and P.OP_GET_PROFILE == 0x0034
    assert {P.OP_SET_PROFILE, P.OP_CLEAR_PROFILE} <= set(P.MUTATING_OPS) and P.OP_GET_PROFILE not in P.MUTATING_OPS
    wire = P.encode_request(5, P.OP_SET_PROFILE, args)
    req = P.decode_request(wire)
    assert req.opcode == 0x0103 and req.args == args


def test_profile_writes_are_confined_to_the_calibration_workflow():
    root = Path(__file__).resolve().parents[1] / "src"
    calls = [path.relative_to(root).as_posix() for path in root.rglob("*.py")
             if ".set_profile(" in path.read_text(encoding="utf-8")]
    assert calls == ["noctinirs_host/engineering.py"]


def controller(**fake):
    sim = EngineeringFake(geometry=load_geometry(GEOMETRY), **fake)
    ctl = SessionController(LoopbackTransport(sim))
    ctl.profile_enabled = True
    assert ctl.connect().ok
    return sim, ctl


def test_get_profile_on_firmware_without_it_is_not_available_and_not_an_error():
    sim, ctl = controller()                       # EngineeringFake without profile_model = pre-profile firmware
    res = ctl.query_profile()
    assert not res.ok and res.status == "UNKNOWN_COMMAND" and ctl.profile.reason == "unsupported"
    assert ctl.profile_counters["unsupported"] == 1 and ctl.counters["timeouts"] == 0
    assert ctl.connected and ctl.counters["malformed"] == 0


def test_get_profile_reads_the_report_and_never_touches_session_or_state():
    sim, ctl = controller(profile_model=True)
    before = (ctl.session_id, ctl.device_state)
    res = ctl.query_profile()
    assert res.ok and ctl.profile.supported and ctl.profile.state == "NONE" and ctl.profile.source == "NONE"
    assert (ctl.session_id, ctl.device_state) == before and sim.profile_requests == 1


def test_set_clear_profile_travel_through_the_same_command_path_only_when_called_by_hand():
    sim, ctl = controller(profile_model=True)
    ok = ctl._run("set_profile", P.OP_SET_PROFILE, args_set_profile(0xA1B2C3D4, HASH, CODES))
    assert ok.ok and sim._profile[0] == 0xA1B2C3D4
    assert ctl.query_profile().ok and ctl.profile.state == "ARMED" and ctl.profile.profile_hash == HASH.hex()
    bad = ctl._run("set_profile", P.OP_SET_PROFILE, b"\x00" * 56)                  # id 0
    assert not bad.ok and bad.status == "INVALID_ARG" and sim._profile[0] == 0xA1B2C3D4
    assert ctl._run("clear_profile", P.OP_CLEAR_PROFILE).ok and sim._profile is None


def test_the_report_id_of_a_foreign_session_is_not_adopted():
    sim, ctl = controller(profile_model=True)
    assert ctl.session_id == 0
    assert ctl.query_profile().ok and ctl.session_id == 0


# ---- compatibility of the old host paths with FIX1 firmware (simulation) -------------------------------------------------

FIX1_POLICY_INFO = (
    "NoctiNIRS2/0.1.0;wire=ADR0004/1;baseline=V4_IDF61_NN2_CTRL;led=REAL_BRINGUP;short_policy=STRICT;"
    "adc=ADS131M04;frames=RAW_ENGINEERING;fs_min=2;fs_max=10;fs_allowed=2,4,5,8,10;current_default_ma=30;"
    "current_max_ma=50;legacy_current=RECORDED_ONLY;iref_code_default=133;iref_code_max=222;"
    "iref_ua_per_lsb=225;diag=health,timing;console=removed;mode=ENGINEERING;wire_minor=1;profile_api=IREF1;"
    "profile_state=APPLIED;profile_source=PROFILE;applied_profile_id=0xA1B2C3D4;applied_valid=1;start_result=OK")


def test_the_fix1_info_text_is_read_by_the_unchanged_info_path():
    reply = P.decode_reply(P.encode_reply(1, P.OP_INFO, 0, state=1, fault=0, simulated=False, boot_id=3,
                                          t_rx_us=1, extension=FIX1_POLICY_INFO.encode("ascii")))
    info = reply.info
    assert info["frames"] == "RAW_ENGINEERING" and info["fs_allowed"] == "2,4,5,8,10"
    assert info["current_max_ma"] == "50" and info["legacy_current"] == "RECORDED_ONLY"
    assert info["wire_minor"] == "1" and info["profile_api"] == "IREF1" and info["applied_valid"] == "1"
    assert len(FIX1_POLICY_INFO) < MAX_PAYLOAD


def test_an_unknown_opcode_reply_with_data_stays_fail_closed_as_before():
    with pytest.raises(P.WireError):
        P.decode_reply(P.encode_reply(1, 0x7777, 0, state=1, fault=0, simulated=False, boot_id=3, t_rx_us=1,
                                      extension=b"abc"))


def test_info_with_profile_tokens_is_accepted_by_connect_and_fs_capability():
    sim, ctl = controller(profile_model=True)
    assert "wire_minor" in ctl.info and ctl.info["profile_api"] == "IREF1" and ctl.info["legacy_current"] == "RECORDED_ONLY"
    assert ctl.settings.fs_allowed == tuple(range(2, 11)) and ctl.settings.current_max_ma == 50


# ---- the engineering workflow ---------------------------------------------------------------------------------------------

class Rig:
    def __init__(self, tmp_path, **fake):
        self.run = EngineeringRun(load_geometry(GEOMETRY), fake_options=dict(profile_model=True, stage2_model=False, **fake))
        self.tmp = tmp_path

    def go(self, pred, seconds=8.0):
        import time
        end = time.monotonic() + seconds
        while not pred():
            assert time.monotonic() < end, "did not converge"
            self.run.tick()
            time.sleep(0.01)

    def sidecar(self):
        files = sorted(self.tmp.glob("record_[0-9].json"))
        assert files
        return json.loads(files[0].read_text(encoding="utf-8"))

    def events(self, kind):
        return [e["text"] for e in self.sidecar()["events"] if e["kind"] == kind]


def start_recorded(tmp_path, **fake):
    rig = Rig(tmp_path, **fake)
    run = rig.run
    run.connect(True, "", "")
    run.configure(5)
    seed_calibration(run)
    run.start_recorded(str(tmp_path), "")
    return rig


def close(rig):
    run = rig.run
    run.disconnect()
    rig.go(lambda: run._closing is None and run._job is None)
    run._disk.shutdown(wait=True)


def test_a_normal_run_records_the_profile_while_running_and_the_stop_evidence_at_the_end(tmp_path):
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.controller.device_state == 3 and run.profile_view and run.profile_view["phase"] == "running")
    report = run.snapshot()["profile"]["report"]
    assert report.state == "APPLIED" and report.source == "PROFILE" and report.applied_valid is True
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    texts = rig.events("profile")
    assert [t.split()[0] for t in texts].count("phase=running") == 1 and any(t.startswith("phase=ended") for t in texts)
    running = next(t for t in texts if t.startswith("phase=running"))
    assert "source=PROFILE" in running and "applied_valid=1" in running and "start_result=OK" in running
    assert "boot=0x" in running and "session=" in running
    ended = next(t for t in texts if t.startswith("phase=ended"))
    assert "stop_evidence=VALID_MATCH" in ended and "stop_integrity=OK" in ended and "applied_valid=0" in ended
    close(rig)
    assert any(t.startswith("codes requested=") for t in rig.events("profile_codes"))
    assert all(len(t) <= 512 for t in texts + rig.events("profile_codes"))


def test_an_armed_profile_is_reported_applied_while_running_and_armed_afterwards(tmp_path):
    rig = Rig(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    run.connect(True, "", "")
    run.configure(5)
    sweep = seed_calibration(run, CODES)
    run.start_recorded(str(tmp_path), "")
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    profile = run._recording_calibration
    assert profile.profile_id != sweep.profile_id
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    texts = rig.events("profile")
    running = next(t for t in texts if t.startswith("phase=running"))
    for part in ("state=APPLIED", "armed=1", f"id=0x{profile.profile_id:08X}", f"hash={profile.profile_hash}", "source=PROFILE",
                 "applied_valid=1", f"applied_hash={profile.profile_hash}"):
        assert part in running, part
    ended = next(t for t in texts if t.startswith("phase=ended"))
    assert "state=ARMED" in ended and "applied_valid=0" in ended and "stop_evidence=VALID_MATCH" in ended
    codes = rig.events("profile_codes")
    assert any("at_stop=" in c for c in codes) and all("requested=133," in c for c in codes)
    close(rig)


def test_a_status_reply_after_a_refused_start_is_not_dropped_as_a_wrong_session():
    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    sim = EngineeringFake(geometry=load_geometry(GEOMETRY), clock=clock, profile_model=True,
                          start_outcome="profile_not_applied")
    ctl = SessionController(LoopbackTransport(sim), clock)
    assert ctl.connect().ok
    assert ctl.start().ok and ctl.state_text == "STARTING" and ctl.session_id
    clock.now += 0.4                                      # the device ends the START after its ACK
    res = ctl.refresh_status()
    assert res.ok and ctl.state_text == "STOPPED" and ctl.session_id == 0
    assert ctl.counters["wrong_session"] == 0 and ctl.counters["timeouts"] == 0
    # a reply with another owner's session while STREAMING is still refused
    assert sim.state == 1 and sim.fault == 0


@pytest.mark.parametrize("evidence,integrity", [("VALID_MISMATCH", "BLOCKED"), ("TRANSPORT_ERR", "BLOCKED"),
                                                ("TIMEOUT", "UNVERIFIED"), ("NOT_RUN", "UNVERIFIED"),
                                                ("UNAVAILABLE", "UNVERIFIED")])
def test_a_blocked_or_unverified_stop_is_flagged_in_the_sidecar(tmp_path, evidence, integrity):
    rig = start_recorded(tmp_path, stop_evidence=evidence)
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    ended = next(t for t in rig.events("profile") if t.startswith("phase=ended"))
    assert f"stop_evidence={evidence}" in ended and f"stop_integrity={integrity}" in ended
    assert ("DATA_INTEGRITY_BLOCKED" in ended) is (integrity == "BLOCKED")
    assert run.snapshot()["profile"]["integrity_blocked"] is (integrity == "BLOCKED")
    close(rig)


@pytest.mark.parametrize("outcome,state,result,evidence", [
    ("profile_not_applied", "STOPPED", "PROFILE_NOT_APPLIED", "NONE"),
    ("start_failed", "FAULTED", "START_FAILED", "SKIPPED_EMERGENCY"),
    ("start_cancelled", "STOPPED", "START_CANCELLED", "SKIPPED_EMERGENCY")])
def test_a_start_that_fails_after_the_ack_is_recorded_and_the_host_recovers(tmp_path, outcome, state, result, evidence):
    rig = start_recorded(tmp_path, start_outcome=outcome)
    run = rig.run
    rig.go(lambda: run.recorder_state in ("FINALIZED", "INCOMPLETE / ERROR"), seconds=15)
    assert run.controller.session_id == 0 and run.controller.state_text == state
    assert "Owned session ended" in run.error                        # the existing end-of-session handling
    ended = [t for t in rig.events("profile") if t.startswith("phase=ended")]
    assert len(ended) == 1
    assert f"start_result={result}" in ended[0] and f"stop_evidence={evidence}" in ended[0]
    assert "applied_valid=0" in ended[0] and "state=APPLIED" not in ended[0]
    assert not any(t.startswith("phase=running") for t in rig.events("profile"))   # it never reached STREAMING
    view = run.snapshot()["profile"]
    assert view["report"].start_result == result and result in view["summary"]
    assert run.snapshot()["connected"]                                # the link survives; no crash, no hang
    close(rig)


def test_firmware_without_get_profile_is_a_quiet_not_available_in_the_workflow(tmp_path):
    run = EngineeringRun(load_geometry(GEOMETRY), fake_options={"profile_model": False})
    run.connect(True, "", "")
    view = run.snapshot()["profile"]
    assert view["report"].reason == "unsupported" and "not available" in view["summary"]
    assert run.error == "" and run.snapshot()["connected"]
    run._disk.shutdown(wait=True)


def test_unknown_profile_fields_in_a_run_are_recorded_by_name(monkeypatch):
    class Odd(EngineeringFake):
        def _execute_profile(self, req, op, args):
            res = super()._execute_profile(req, op, args)
            if op == P.OP_GET_PROFILE and res is not None:
                text = P.decode_reply(res[0]).extension + b" brand_new_key=1"
                return self._reply(req.req_id, op, 0, text), False
            return res

    import noctinirs_host.engineering as engineering
    monkeypatch.setattr(engineering, "EngineeringFake", Odd)
    run = EngineeringRun(load_geometry(GEOMETRY), fake_options=dict(profile_model=True))
    run.connect(True, "", "")
    report = run.snapshot()["profile"]["report"]
    assert dict(report.unknown) == {"brand_new_key": "1"} and run.error == "" and report.state == "NONE"
    run._disk.shutdown(wait=True)
