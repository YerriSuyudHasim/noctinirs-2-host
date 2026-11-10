"""Regression tests for the Codex host review IR_CODEX_HOST_D4_FIX_2026-11-10 (H-01, H-02, H-04, H-05).

Evidence class: host/unit and in-process simulation (EngineeringFake models written from the FIX2 patch
sources, plus the localhost UDP Peer); no device, no firmware was run. Each finding has its own section.
Tests marked CONTROL also pass on 1565124: they pin behaviour that was already right (see the report).
"""

import json
import re
import struct
import time

import pytest

from calibration_fixture import seed_calibration
from noctinirs_host.control import protocol as P
from noctinirs_host.control.engineering_fake import EngineeringFake
from noctinirs_host.control import profile as PR      # module access: new names fail per test on 1565124
from noctinirs_host.control.profile import EVENT_LIMIT, encode_profile_text, parse_profile, profile_event_text, profile_summary
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.engineering import EngineeringRun
from noctinirs_host.geometry import load_geometry
from test_engineering_run import GEOMETRY, arm, poll, setup  # noqa: F401 (setup is a fixture)
from test_profile import CODES, HASH, Rig, applied, close, start_recorded

BARE_SESSION = re.compile(r"(?<![A-Za-z_])session=(\d+)")


def bare_session(text):
    m = BARE_SESSION.search(text)
    return int(m.group(1)) if m else None


def patch_fake(monkeypatch, cls):
    import noctinirs_host.engineering as engineering
    monkeypatch.setattr(engineering, "EngineeringFake", cls)


def all_events(rig, name):
    meta = json.loads((rig.tmp / name).read_text(encoding="utf-8"))
    return meta["events"]


# ---- H-01: attribution of the device-wide report ----------------------------------------------------------

class NewOwnerAtEndRead(EngineeringFake):
    """Another endpoint takes the device between this host's terminal STATUS and its GET_PROFILE: the
    zero-session end read is answered while session 202 is STREAMING (firmware: reply session = c.session)."""

    def _execute_profile(self, req, op, args):
        a = self._applied
        if (op == P.OP_GET_PROFILE and req.session_id == 0 and a and not a.get("valid")
                and a.get("stop_evidence") not in (None, "NONE")):
            saved = (self.state, self.session_id)
            self.state, self.session_id = P.ST_STREAMING, 202
            try:
                return super()._execute_profile(req, op, args)
            finally:
                self.state, self.session_id = saved
        return super()._execute_profile(req, op, args)


def test_h01_a_new_owner_between_terminal_status_and_get_profile_is_not_attributed_to_the_run(tmp_path, monkeypatch):
    patch_fake(monkeypatch, NewOwnerAtEndRead)
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    ours = rig.events("profile")
    running = next(t for t in ours if t.startswith("phase=running"))
    assert "attributed=1" in running and "basis=owner" in running and bare_session(running) == 1
    # the end read saw session 202: it is a device diagnostic, never this run's evidence
    assert not any(t.startswith("phase=ended") for t in ours), ours
    device = [t for t in rig.events("profile_device") if t.startswith("phase=ended")]
    assert len(device) == 1
    assert "attributed=0" in device[0] and "basis=foreign" in device[0] and "reply_session=202" in device[0]
    assert "reply_state=STREAMING" in device[0] and bare_session(device[0]) is None
    view = run.snapshot()["profile"]
    assert view["provenance"]["basis"] == "foreign" and "another owner" in view["summary"]
    close(rig)


def test_h01_a_detached_end_read_is_unattributed_and_names_the_run_only_as_a_candidate(tmp_path):
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    ended = next(t for t in rig.events("profile") if t.startswith("phase=ended"))
    for part in ("attributed=0", "basis=detached", "reply_session=0", "reply_state=STOPPED", "candidate_session=1",
                 "stop_evidence=VALID_MATCH", "stop_integrity=OK", "boot=0x"):
        assert part in ended, (part, ended)
    assert bare_session(ended) is None and len(ended) <= EVENT_LIMIT
    close(rig)


def test_h01_the_next_runs_pre_start_read_never_carries_the_previous_session_id(tmp_path):
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED" and run.recorder is None, seconds=15)
    seed_calibration(run)
    run.start_recorded(str(tmp_path), "")
    rig.go(lambda: run.recorder_state == "RECORDING" and run.profile_view["phase"] == "running", seconds=15)
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    second = [e["text"] for e in all_events(rig, "record_2.json") if e["kind"] == "profile"]
    pre = next(t for t in second if t.startswith("phase=pre_start"))
    assert bare_session(pre) is None and "basis=no_session" in pre and "attributed=0" in pre
    running = next(t for t in second if t.startswith("phase=running"))
    assert bare_session(running) == 2 and "basis=owner" in running
    close(rig)


@pytest.mark.parametrize("reply_session,sent,phase,cand,basis,attributed", [
    (7, 7, "running", None, "owner", True),
    (202, 7, "running", None, "foreign", False),
    (202, 0, "ended", 7, "foreign", False),
    (0, 0, "ended", 7, "detached", False),
    (0, 0, "after_safe_off", 7, "detached", False),
    (0, 0, "pre_start", 7, "no_session", False),
    (0, 0, "ended", None, "no_session", False),
    (0, 7, "running", None, "no_session", False)])
def test_h01_attribution_rules(reply_session, sent, phase, cand, basis, attributed):
    prov = PR.profile_attribution(reply_session, sent_session=sent, phase=phase, candidate_session=cand)
    assert prov["basis"] == basis and prov["attributed"] is attributed
    text = profile_event_text(parse_profile(applied()), phase=phase, boot_id=0x55, provenance=prov,
                              reply_state="STOPPED")
    assert f"basis={basis}" in text and f"attributed={int(attributed)}" in text
    assert (bare_session(text) == reply_session) if attributed else bare_session(text) is None
    assert text.index("hash=") > text.index("stop_error=")        # identity and integrity before the hashes


def test_h01_the_worst_case_event_keeps_identity_integrity_and_both_hashes_inside_the_limit():
    h = bytes([0xAB]) * 32
    r = parse_profile(encode_profile_text(
        state="NOT_APPLIED", armed=True, profile_id=0xFFFFFFFF, profile_hash=h, profile_code=[222] * 20,
        source="PROFILE", start_result="PROFILE_NOT_APPLIED", mismatch_slot=19, applied_profile_id=0xFFFFFFFF,
        applied_profile_hash=h, applied_valid=False, stop_evidence="SKIPPED_EMERGENCY", stop_slot=19,
        stop_error=-2147483648))
    prov = PR.profile_attribution(0, phase="after_safe_off", candidate_session=0xFFFFFFFF)
    text = profile_event_text(r, phase="after_safe_off", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                              provenance=prov, reply_state="SAFE_OFF")
    assert len(text) <= EVENT_LIMIT and not text.endswith(".")        # nothing is cut off
    assert "candidate_session=4294967295" in text and "stop_evidence=SKIPPED_EMERGENCY" in text
    assert "stop_error=-2147483648" in text and "hashes=in_profile_ids" in text
    assert f"hash={h.hex()} applied_hash={h.hex()}" in PR.profile_ids_event_text(r, phase="after_safe_off")
    # a typical identity (small session, 32-bit boot) keeps the hash on the line; an equal applied hash is "same"
    typical = profile_event_text(r, phase="ended", boot_id=0x5151_0001, plan_epoch=3,
                                 provenance=PR.profile_attribution(0, phase="ended", candidate_session=12),
                                 reply_state="STOPPED")
    assert len(typical) <= EVENT_LIMIT and f"hash={h.hex()}" in typical and "applied_hash=same" in typical
    # two different full hashes do not fit next to the identity: both move to a profile_ids line, uncut
    h2 = bytes([0xCD]) * 32
    r2 = parse_profile(encode_profile_text(
        state="NOT_APPLIED", armed=True, profile_id=0xFFFFFFFF, profile_hash=h, profile_code=[222] * 20,
        source="PROFILE", start_result="PROFILE_NOT_APPLIED", mismatch_slot=19, applied_profile_id=0xFFFFFFFE,
        applied_profile_hash=h2, applied_valid=False, stop_evidence="SKIPPED_EMERGENCY", stop_slot=19,
        stop_error=-2147483648))
    text2 = profile_event_text(r2, phase="after_safe_off", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                               provenance=prov, reply_state="SAFE_OFF")
    assert len(text2) <= EVENT_LIMIT and not text2.endswith(".") and "hashes=in_profile_ids" in text2
    ids = PR.profile_ids_event_text(r2, phase="after_safe_off")
    assert f"hash={h.hex()}" in ids and f"applied_hash={h2.hex()}" in ids


# ---- H-02: duplicated or contradictory integrity fields -------------------------------------------------------

DUP_VALUES = {"stop_evidence": ("VALID_MATCH", "VALID_MISMATCH"), "stop_integrity": ("OK", "BLOCKED"),
              "stop_match": ("1", "0"), "stop_error": ("0", "-1"), "stop_slot": ("-1", "6")}


def with_duplicate(key, first, second):
    text = applied(stop_evidence="VALID_MATCH", applied_at_stop=CODES)
    m = re.search(rb" " + key.encode() + rb"=\S+", text)
    assert m, key
    return text[:m.start()] + f" {key}={first}".encode() + text[m.end():] + f" {key}={second}".encode()


@pytest.mark.parametrize("key", ("stop_evidence", "stop_integrity", "stop_match", "stop_error", "stop_slot"))
@pytest.mark.parametrize("order", ["positive_first", "negative_first", "identical"])
def test_h02_a_duplicated_integrity_key_is_unverified_in_either_order(key, order):
    good, bad = DUP_VALUES[key]
    first, second = {"positive_first": (good, bad), "negative_first": (bad, good), "identical": (good, good)}[order]
    r = parse_profile(with_duplicate(key, first, second))
    assert r.stop_integrity_effective == "UNVERIFIED" and not r.integrity_blocked
    assert f"duplicate:{key}" in r.integrity_issues
    assert any("duplicate integrity key" in w for w in r.warnings)
    assert [v for k, v in r.raw_integrity if k == key] == [first, second]      # the device's raw values are kept
    ev = PR.profile_event_lines(r, phase="ended", provenance=PR.profile_attribution(0))
    text = ev.main
    assert "stop_integrity=UNVERIFIED" in text and f"duplicate:{key}" in text
    # FIX4 (H-02b): one `raw_integrity=<key>:<value>` token per occurrence; the six tokens no longer fit the
    # main line next to the identity, so they are read back from main + follow-ons, complete and in order
    assert PR.raw_integrity_from_events((text,) + ev.ids) == r.raw_integrity
    assert f"raw_integrity={key}:{first}" in " ".join((text,) + ev.ids)
    assert f"raw_integrity={key}:{second}" in " ".join((text,) + ev.ids)
    assert all(len(t) <= EVENT_LIMIT for t in (text,) + ev.ids)
    assert "inconsistent" in profile_summary(r)


@pytest.mark.parametrize("key", ("stop_evidence", "stop_integrity", "stop_match", "stop_error", "stop_slot"))
def test_h02_a_duplicate_at_full_identity_is_never_cut_and_keeps_hashes_and_raw_values(key):
    good, bad = DUP_VALUES[key]
    r = parse_profile(with_duplicate(key, good, bad))
    prov = PR.profile_attribution(0, phase="ended", candidate_session=0xFFFFFFFF)
    text = profile_event_text(r, phase="ended", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                              provenance=prov, reply_state="STOPPED")
    assert len(text) <= EVENT_LIMIT and not text.endswith(".")        # nothing is cut off
    assert "stop_integrity=UNVERIFIED" in text and f"duplicate:{key}" in text
    both = text + " " + PR.profile_ids_event_text(r, phase="ended")
    assert f"hash={HASH.hex()}" in both and (f"applied_hash={HASH.hex()}" in both or "applied_hash=same" in both)
    assert f"{key}:{good}" in both and f"{key}:{bad}" in both


@pytest.mark.parametrize("match,error,why", [
    ("0", "0", "valid_match_without_stop_match_1"), ("1", "-1", "valid_match_with_stop_error"),
    ("0", "-1", "valid_match_without_stop_match_1"), ("1", "263", "valid_match_with_stop_error"),
    ("1", "x", "valid_match_with_stop_error"), ("maybe", "0", "valid_match_without_stop_match_1"),
    (None, "0", "valid_match_without_stop_match_1"), ("1", None, "valid_match_with_stop_error")])
def test_h02_valid_match_needs_ok_match_1_and_error_0_as_fix2_emits(match, error, why):
    text = applied(stop_evidence="VALID_MATCH", applied_at_stop=CODES)
    for key, value in (("stop_match", match), ("stop_error", error)):
        text = re.sub(rb" " + key.encode() + rb"=\S+", b"" if value is None else f" {key}={value}".encode(), text)
    r = parse_profile(text)
    assert r.stop_evidence == "VALID_MATCH" and r.stop_integrity == "OK"           # what the device said
    assert r.stop_integrity_effective == "UNVERIFIED" and why in r.integrity_issues
    assert "stop_integrity=UNVERIFIED" in profile_event_text(r, phase="ended")


@pytest.mark.parametrize("evidence,integrity", [("VALID_MATCH", "BLOCKED"), ("VALID_MATCH", "UNVERIFIED"),
                                                ("VALID_MISMATCH", "OK"), ("TRANSPORT_ERR", "OK"), ("NONE", "OK")])
def test_h02_contradictory_evidence_and_integrity_never_give_ok(evidence, integrity):   # CONTROL
    r = parse_profile(applied(stop_evidence=evidence, stop_integrity=integrity))
    assert r.stop_integrity_effective == "UNVERIFIED"


def test_h02_the_fix2_valid_match_reply_is_still_ok_and_a_mismatch_still_blocked():   # CONTROL
    ok = parse_profile(applied(stop_evidence="VALID_MATCH", applied_at_stop=CODES))
    assert ok.stop_integrity_effective == "OK" and ok.integrity_issues == () and ok.warnings == ()
    blocked = parse_profile(applied(stop_evidence="VALID_MISMATCH", applied_at_stop=CODES, stop_slot=6))
    assert blocked.stop_integrity_effective == "BLOCKED" and blocked.integrity_blocked


# ---- H-04: B-01 refusal and wrong-session cases, firmware-faithful -------------------------------------------

class Overrides(EngineeringFake):
    """`override[op] = (status, state, reply_session)` answers that opcode with exactly that header (the
    remaining fields are well formed). `fix2_stale` makes STATUS / INFO / get_config answer a request that
    still carries an ended session id with INVALID_STATE and the current session (0), as FIX2 does
    (nn2_app_comm.c: req.session_id && req.session_id != c.session -> NN2_REPLY_STATE)."""

    def __init__(self, *a, **k):
        self.override = {}
        self.fix2_stale = False
        self.silent = set()
        super().__init__(*a, **k)

    def handle(self, datagram):
        try:
            if P.decode_request(datagram).opcode in self.silent:
                return []
        except P.WireError:
            pass
        return super().handle(datagram)

    def _execute(self, req, op, args):
        o = self.override.get(op)
        if o is not None:
            status, state, session = o
            ext = b""
            if status == P.STATUS_OK and op in P.TIMED_OPS:
                t = self._now_us()
                ext = args + struct.pack("<QQ", t, t)
                if op == P.OP_STATUS:
                    ext += struct.pack("<5IB", 0, 0, 0, 0, 0, 0)
            elif status == P.STATUS_OK and op == P.OP_GET_CONFIG:
                ext = struct.pack("<BB20H", self.fs_hz, 20, *self.current_ma)
            saved = self.state
            self.state = state
            try:
                return self._reply(req.req_id, op, status, ext, session_id=session), False
            finally:
                self.state = saved
        if (self.fix2_stale and op in (P.OP_STATUS, P.OP_INFO, P.OP_GET_CONFIG) and req.session_id
                and req.session_id != self.session_id):
            return self._reply(req.req_id, op, P.STATUS_INVALID_STATE), False
        return super()._execute(req, op, args)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def owned_controller(**fake):
    clock = Clock()
    sim = Overrides(geometry=load_geometry(GEOMETRY), clock=clock, profile_model=True, **fake)
    ctl = SessionController(LoopbackTransport(sim), clock, retries=0, timeout_s=0.05)
    assert ctl.connect().ok
    return clock, sim, ctl


def test_h04_fix2_stale_session_status_after_a_refused_start_is_invalid_state_with_session_0():   # CONTROL
    clock, sim, ctl = owned_controller(start_outcome="profile_not_applied")
    sim.fix2_stale = True
    assert ctl.start().ok and ctl.state_text == "STARTING" and ctl.session_id
    clock.now += 0.4                                       # the device ends the START after its ACK
    res = ctl.refresh_status()
    assert res.status == "INVALID_STATE" and res.reply.session_id == 0
    assert ctl.session_id == 0 and ctl.state_text == "STOPPED" and ctl.connected
    assert ctl.counters["wrong_session"] == 0 and ctl.counters["timeouts"] == 0


def test_h04_fix2_refusal_in_the_engineering_workflow_records_one_end_read_and_keeps_the_link(tmp_path, monkeypatch):
    class Faithful(Overrides):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.fix2_stale = True
    patch_fake(monkeypatch, Faithful)            # CONTROL for the link; the end read format is H-01
    rig = start_recorded(tmp_path, start_outcome="profile_not_applied")
    run = rig.run
    rig.go(lambda: run.recorder_state in ("FINALIZED", "INCOMPLETE / ERROR"), seconds=15)
    assert run.snapshot()["connected"] and run.controller.session_id == 0 and run.controller.state_text == "STOPPED"
    ended = [t for t in rig.events("profile") if t.startswith("phase=ended")]
    assert len(ended) == 1 and "start_result=PROFILE_NOT_APPLIED" in ended[0] and "basis=detached" in ended[0]
    close(rig)


@pytest.mark.parametrize("op,state,session", [
    (P.OP_STATUS, P.ST_STOPPED, 202),            # CONTROL: a foreign nonzero id with OK
    (P.OP_STATUS, P.ST_STREAMING, 0),            # CONTROL: id 0 in an owned state
    (P.OP_STATUS, P.ST_STOPPING, 0),             # CONTROL
    (P.OP_STATUS, P.ST_RECOVERING, 0),           # id 0 in a state that is not an explicit terminal one
    (P.OP_STATUS, P.ST_BOOT, 0),
    (P.OP_HEARTBEAT, P.ST_STOPPED, 0),           # id 0 on a non-observation command
    (P.OP_GET_TIME, P.ST_STOPPED, 0)])
def test_h04_ok_replies_that_cannot_belong_to_the_held_session_are_refused(op, state, session):
    clock, sim, ctl = owned_controller()
    assert ctl.start().ok and ctl.state_text == "STREAMING"
    held = ctl.session_id
    sim.override[op] = (P.STATUS_OK, state, session)
    args = P.args_host_time(int(clock() * 1e6)) if op in P.TIMED_OPS else b""
    res = ctl._run(P.OPCODE_NAMES.get(op, "op"), op, args)
    assert res.status == "TIMEOUT" and ctl.counters["wrong_session"] >= 1
    assert ctl.session_id == held and ctl.state_text == "STREAMING"     # nothing observed, nothing dropped


@pytest.mark.parametrize("op", [P.OP_STATUS, P.OP_INFO, P.OP_GET_CONFIG])
@pytest.mark.parametrize("state", [P.ST_STOPPED, P.ST_FAULTED, P.ST_SAFE_OFF])
def test_h04_the_compatibility_terminal_ok_path_stays_for_observation_commands(op, state):   # CONTROL
    clock, sim, ctl = owned_controller()
    assert ctl.start().ok
    sim.override[op] = (P.STATUS_OK, state, 0)
    if op == P.OP_STATUS:
        res = ctl.refresh_status()
    elif op == P.OP_INFO:
        res = ctl.refresh_info()
    else:
        res = ctl.refresh_config()
    assert res.reply is not None and res.reply.session_id == 0 and ctl.counters["wrong_session"] == 0


def test_h04_no_reply_is_a_timeout_and_keeps_the_held_session():   # CONTROL
    clock, sim, ctl = owned_controller()
    assert ctl.start().ok
    held = ctl.session_id
    sim.silent.add(P.OP_STATUS)
    res = ctl.refresh_status()
    assert res.status == "TIMEOUT" and ctl.session_id == held and ctl.counters["timeouts"] == 1


# ---- H-05: SAFE_OFF and the end evidence -------------------------------------------------------------------------

class LateSafeOffEvidence(EngineeringFake):
    """Models FIX2 SAFE_OFF: the comm layer ACKs at once; the control worker records the STOP evidence
    (SKIPPED_EMERGENCY for an emergency cause) only after its cleanup, here 0.5 s later."""
    evidence_delay_s = 0.5

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.safe_off_at = None
        self.profile_after_safe_off = 0
        self.silent_profile = False

    def handle(self, datagram):
        try:
            op = P.decode_request(datagram).opcode
        except P.WireError:
            op = None
        if op == P.OP_SAFE_OFF and self.safe_off_at is None:
            self.safe_off_at = self._clock()
        if op == P.OP_GET_PROFILE and self.safe_off_at is not None:
            self.profile_after_safe_off += 1
            if self.silent_profile:
                return []
        return super().handle(datagram)

    def _profile_fields(self):
        a = self._applied
        if a and self.safe_off_at is not None and self._clock() - self.safe_off_at >= self.evidence_delay_s:
            a.update(valid=False, stop_evidence="SKIPPED_EMERGENCY")
        return super()._profile_fields()


def rig_streaming(tmp_path, monkeypatch, cls=LateSafeOffEvidence):
    patch_fake(monkeypatch, cls)
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    return rig, run


def test_h05_safe_off_ack_is_recorded_and_the_one_end_read_is_deferred_until_after_cleanup(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    t0 = time.monotonic()
    run.safe_off()
    assert run.recorder_state == "RECORDING" and sim.profile_after_safe_off == 0   # nothing read at the ACK
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    assert time.monotonic() - t0 >= 1.0 and sim.profile_after_safe_off == 1        # exactly one send
    ack = rig.events("safe_off")
    assert len(ack) == 1 and "SAFE_OFF ACK reply_session=0 reply_state=SAFE_OFF" in ack[0]
    texts = rig.events("profile")
    after = [t for t in texts if t.startswith("phase=after_safe_off")]
    assert len(after) == 1 and not any(t.startswith("phase=ended") for t in texts)
    for part in ("stop_evidence=SKIPPED_EMERGENCY", "attributed=0", "basis=detached", "candidate_session=1",
                 "reply_state=SAFE_OFF"):
        assert part in after[0], (part, after[0])
    close(rig)


def test_h05_a_silent_deferred_read_is_one_bounded_send_then_the_recording_finalizes(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    sim.silent_profile = True
    run.safe_off()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    assert sim.profile_after_safe_off == 1 and run.snapshot()["connected"]
    after = [t for t in rig.events("profile") if t.startswith("phase=after_safe_off")]
    assert after == ["phase=after_safe_off not read: get-profile TIMEOUT: no usable reply"]
    assert run.controller.counters["timeouts"] == 0                  # a profile miss is not a control timeout
    close(rig)


def test_h05_disconnect_before_the_deferred_read_records_that_it_was_not_read(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    run.safe_off()
    run.disconnect()
    rig.go(lambda: run._closing is None and run._job is None and run.recorder is None, seconds=15)
    texts = rig.events("profile")
    assert "phase=after_safe_off not read: link closed first" in texts and sim.profile_after_safe_off == 0
    run._disk.shutdown(wait=True)


def test_h05_a_lost_start_ack_records_an_explicit_missing_end_event(setup, tmp_path):  # noqa: F811
    peer, clock, make = setup
    run = make()
    run.configure(5)
    arm(run, tmp_path)
    poll(run, lambda: run.recorder_state == "ARMED")
    peer.filter = lambda _p, req, _wire, ack, _src: [] if req.opcode == P.OP_START else [ack]
    run.start()
    clock.now += 2
    poll(run, lambda: run.recorder_state == "FINALIZED")
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    missing = [e["text"] for e in meta["events"] if e["kind"] == "profile" and e["text"].startswith("phase=end_missing")]
    assert len(missing) == 1 and "basis=start_unresolved" in missing[0] and "end_evidence=MISSING" in missing[0]
    assert "attributed=0" in missing[0]
    run.disconnect()
    run.tick()
    run.safe_off()                                   # recovery: the recording is already closed
    assert not run.connected and "reported SAFE_OFF" in run.notice
