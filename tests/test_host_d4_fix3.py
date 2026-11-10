"""Regression tests for the Codex host re-review IR_CODEX_HOST_D4_FIX2_2026-11-10 (H-02a, H-01a, H-05a).

Evidence class: host/unit and in-process simulation (EngineeringFake models written from the FIX2 patch
sources); no device, no firmware was run. H-08a is documentation only (no test). Every test here fails on
7800319 and passes after the fix, except the one marked CONTROL, which pins that a read that can still finish
by the limit is sent (see HOST_D4_FIX3_REPORT.md).
"""

import re
import time

import pytest

from noctinirs_host.control import profile as PR      # module access: new names fail per test on 7800319
from noctinirs_host.control import protocol as P
from noctinirs_host.control.engineering_fake import EngineeringFake
from noctinirs_host.control.profile import EVENT_LIMIT, INTEGRITY_KEYS, encode_profile_text, parse_profile
from noctinirs_host.engineering import RecordingTarget
from test_host_d4_fix2 import LateSafeOffEvidence, all_events, bare_session, patch_fake, rig_streaming
from test_profile import close, start_recorded

H1 = "ab" * 32
H2 = "cd" * 32
FIVE_DUPLICATES = "stop_evidence=VALID_MATCH stop_integrity=OK stop_match=1 stop_slot=19 stop_error=-2147483648"


def codex_probe_reply(extra=FIVE_DUPLICATES) -> bytes:
    """The Codex probe: full-width identity fields, two different 64-hex hashes, VALID_MATCH and all five
    integrity keys sent twice."""
    return encode_profile_text(
        state="NOT_APPLIED", armed=True, profile_id=0xFFFFFFFF, profile_hash=bytes.fromhex(H1),
        profile_code=[222] * 20, source="PROFILE", start_result="PROFILE_NOT_APPLIED", mismatch_slot=19,
        applied_profile_id=0xFFFFFFFE, applied_profile_hash=bytes.fromhex(H2), applied_valid=False,
        stop_evidence="VALID_MATCH", stop_integrity="OK", stop_match=True, stop_slot=19,
        stop_error=-2147483648, extra=extra)


def raw_occurrences(text: bytes):
    return [tuple(t.split("=", 1)) for t in text.decode("ascii").split(" ")
            if t.partition("=")[0] in INTEGRITY_KEYS]


def ids_values(ids, key):
    """Every value of `key` over the `ids` events, in order (a comma list may continue on the next event)."""
    out = []
    for line in ids:
        for token in line.split(" "):
            k, _, v = token.partition("=")
            if k == key:
                out.extend(v.split(",") if key in ("raw_integrity", "integrity_issues") else [v])
    return out


def check_ids_complete(ids, phase):
    n = len(ids)
    assert n >= 1
    for i, line in enumerate(ids, 1):
        assert line.startswith(f"phase={phase} ids={i}/{n} "), line
        assert len(line) <= EVENT_LIMIT and not line.endswith(".")


# ---- H-02a: overflow never drops the hashes or the raw integrity values ---------------------------------------

def test_h02a_the_codex_probe_keeps_markers_both_hashes_and_all_ten_raw_values():
    text = codex_probe_reply()
    assert len(text) == 1173                                              # the probe's reply size
    r = parse_profile(text)
    assert r.stop_integrity_effective == "UNVERIFIED"
    assert list(r.raw_integrity) == raw_occurrences(text) and len(r.raw_integrity) == 10
    prov = PR.profile_attribution(0, phase="after_safe_off", candidate_session=0xFFFFFFFF)
    ev = PR.profile_event_lines(r, phase="after_safe_off", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                                provenance=prov, reply_state="SAFE_OFF")
    assert len(ev.main) <= EVENT_LIMIT and not ev.main.endswith(".")      # never sliced
    assert "stop_integrity=UNVERIFIED" in ev.main and "candidate_session=4294967295" in ev.main
    assert "hashes=in_profile_ids" in ev.main and "raw_integrity=in_profile_ids" in ev.main
    check_ids_complete(ev.ids, "after_safe_off")
    assert ids_values(ev.ids, "hash") == [H1] and ids_values(ev.ids, "applied_hash") == [H2]
    assert ids_values(ev.ids, "raw_integrity") == [f"{k}:{v}" for k, v in raw_occurrences(text)]
    issues = set(re.search(r"integrity_issues=(\S+)", ev.main).group(1).split(",")) \
        if "integrity_issues=in_profile_ids" not in ev.main else set(ids_values(ev.ids, "integrity_issues"))
    assert {f"duplicate:{k}" for k in INTEGRITY_KEYS} <= issues


class CodexProbeFake(EngineeringFake):
    """Every get-profile is answered with the Codex probe reply (1173 B, five duplicated keys, two hashes)."""

    def _execute_profile(self, req, op, args):
        if op == P.OP_GET_PROFILE and not args:
            self.profile_requests += 1
            return self._reply(req.req_id, op, P.STATUS_OK, codex_probe_reply()), False
        return super()._execute_profile(req, op, args)


def test_h02a_the_recorder_path_always_writes_the_follow_on_events_with_both_hashes_and_all_raw_values(
        tmp_path, monkeypatch):
    patch_fake(monkeypatch, CodexProbeFake)
    rig = start_recorded(tmp_path)
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    events = rig.sidecar()["events"]
    expected_raw = [f"{k}:{v}" for k, v in raw_occurrences(codex_probe_reply())]
    mains = [(i, e["text"]) for i, e in enumerate(events) if e["kind"] == "profile"]
    phases = [t.split(" ")[0] for _, t in mains]
    assert "phase=running" in phases and "phase=ended" in phases
    for i, main in mains:
        phase = main.split(" ")[0].partition("=")[2]
        assert len(main) <= EVENT_LIMIT and not main.endswith(".") and "stop_integrity=UNVERIFIED" in main
        ids = []
        for e in events[i + 1:]:                       # the follow-ons are written right after their main line
            if e["kind"] != "profile_ids":
                break
            ids.append(e["text"])
        check_ids_complete(ids, phase)
        assert ids_values(ids, "hash") == [H1] and ids_values(ids, "applied_hash") == [H2]
        assert ids_values(ids, "raw_integrity") == expected_raw
    close(rig)


def test_h02a_a_long_ids_payload_is_split_into_bounded_events_and_never_cuts_a_hash_or_value():
    long_error = "-" + "9" * 120                         # kept whole (old parser: first 24 characters)
    extra = " ".join([FIVE_DUPLICATES] * 2 + [f"stop_error={long_error}"])
    text = codex_probe_reply(extra)
    assert len(text) <= PR.MAX_TEXT                      # still a reply the firmware could send
    r = parse_profile(text)
    assert list(r.raw_integrity) == raw_occurrences(text) and len(r.raw_integrity) == 16
    assert r.stop_integrity_effective == "UNVERIFIED"
    ev = PR.profile_event_lines(r, phase="ended", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                                provenance=PR.profile_attribution(0, phase="ended", candidate_session=0xFFFFFFFF),
                                reply_state="STOPPED")
    assert len(ev.main) <= EVENT_LIMIT and not ev.main.endswith(".")
    assert len(ev.ids) >= 2                              # one 500-character event cannot hold it all
    check_ids_complete(ev.ids, "ended")
    assert ids_values(ev.ids, "hash") == [H1] and ids_values(ev.ids, "applied_hash") == [H2]
    assert ids_values(ev.ids, "raw_integrity") == [f"{k}:{v}" for k, v in raw_occurrences(text)]
    # the standalone ids text (used for display) holds the same complete data, hashes uncut
    joined = PR.profile_ids_event_text(r, phase="ended")
    assert f"hash={H1}" in joined and f"applied_hash={H2}" in joined and f"stop_error:{long_error}" in joined


def test_h02a_a_raw_value_above_the_limit_is_shortened_with_an_explicit_marker_not_dropped():
    huge = "7" * (PR.RAW_VALUE_LIMIT + 50)
    r = parse_profile(f"v4profile=1 stop_evidence=NONE stop_error={huge} stop_error=0")
    assert r.raw_integrity[1] == ("stop_error", f"{huge[:PR.RAW_VALUE_LIMIT]}~len={len(huge)}")
    assert r.raw_integrity[2] == ("stop_error", "0") and "duplicate:stop_error" in r.integrity_issues
    ev = PR.profile_event_lines(r, phase="ended", provenance=PR.profile_attribution(0))
    assert len(ev.main) <= EVENT_LIMIT and all(len(t) <= EVENT_LIMIT for t in ev.ids)
    assert ids_values(ev.ids, "raw_integrity") == [f"{k}:{v}" for k, v in r.raw_integrity]


def test_h02a_raw_integrity_values_longer_than_24_characters_are_kept_whole():
    value = "-21474836480000000000000000000000"           # 33 characters
    r = parse_profile(codex_probe_reply(f"stop_error={value}"))
    assert ("stop_error", value) in r.raw_integrity
    assert f"stop_error:{value}" in PR.profile_ids_event_text(r, phase="ended")


@pytest.mark.parametrize("basis", ["owner", "foreign", "detached", "no_session"])
@pytest.mark.parametrize("evidence", ["VALID_MATCH", "VALID_MISMATCH"])
def test_h02a_the_widest_identity_keeps_the_stop_fields_and_markers_on_an_unsliced_main_line(basis, evidence):
    unknown = " ".join(f"k{i:02d}{'x' * 40}=1" for i in range(4))
    r = parse_profile(codex_probe_reply(FIVE_DUPLICATES + " " + unknown).replace(
        b"stop_evidence=VALID_MATCH", f"stop_evidence={evidence}".encode(), 1))
    prov = {"attributed": basis == "owner", "basis": basis, "reply_session": 0xFFFFFFFF,
            "candidate_session": 0xFFFFFFFF, "session": 0xFFFFFFFF}
    ev = PR.profile_event_lines(r, phase="after_safe_off", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                                provenance=prov, reply_state="RECOVERING")
    assert len(ev.main) <= EVENT_LIMIT and not ev.main.endswith(".")
    for key in ("stop_evidence=", "stop_integrity=UNVERIFIED", "stop_match=", "stop_slot=", "stop_error=",
                "boot=0xFFFFFFFFFFFFFFFF", f"basis={basis}", "hashes=in_profile_ids"):
        assert key in ev.main, (key, ev.main)
    check_ids_complete(ev.ids, "after_safe_off")
    assert ids_values(ev.ids, "hash") == [H1] and ids_values(ev.ids, "applied_hash") == [H2]
    assert ids_values(ev.ids, "raw_integrity") == [f"{k}:{v}" for k, v in r.raw_integrity]
    # every field that left the main line is in the follow-ons, verbatim
    full = PR.profile_event_lines(r, phase="after_safe_off").main
    for token in full.split(" "):
        k = token.partition("=")[0]
        if k in ("state", "armed", "id", "source", "start_result", "mismatch_slot", "applied_id", "applied_valid"):
            assert token in ev.main.split(" ") or any(token in line.split(" ") for line in ev.ids), token


# ---- H-01a: no stale candidate_session after SAFE_OFF before START on a new recording --------------------------

def test_h01a_safe_off_on_a_new_unstarted_recording_never_names_the_previous_run(tmp_path, monkeypatch):
    patch_fake(monkeypatch, LateSafeOffEvidence)
    rig = start_recorded(tmp_path, stop_evidence="VALID_MATCH")
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED" and run.recorder is None, seconds=15)
    target = RecordingTarget.prepare(str(tmp_path))
    assert target.name == "record_2"
    run.check_destination(target)
    rig.go(lambda: run.checked_target == target and run._job is None)
    run.arm(target)
    assert run._session_id is None                    # the run context is reset when a new recording is armed
    rig.go(lambda: run.recorder_state == "ARMED" and run._job is None)
    run.safe_off()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    second = [e["text"] for e in all_events(rig, "record_2.json") if e["kind"] == "profile"]
    after = [t for t in second if t.startswith("phase=after_safe_off")]
    assert len(after) == 1
    assert "candidate_session=" not in after[0] and bare_session(after[0]) is None
    assert "basis=no_session" in after[0] and "attributed=0" in after[0]
    close(rig)


# ---- H-05a: a repeated SAFE_OFF ACK keeps the first schedule; the limit is a finish bound ----------------------

def test_h05a_a_second_safe_off_ack_keeps_the_first_due_time_limit_and_candidate(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    run.safe_off()
    first = dict(run._deferred_profile)
    time.sleep(0.10)
    run.safe_off()
    assert run._deferred_profile == first             # neither due nor limit moved; the candidate is kept
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    assert sim.profile_after_safe_off == 1            # still one read
    acks = rig.events("safe_off")
    assert len(acks) == 2 and "repeated ACK" in acks[1] and "repeated ACK" not in acks[0]
    after = [t for t in rig.events("profile") if t.startswith("phase=after_safe_off")]
    assert len(after) == 1 and "candidate_session=1" in after[0] and "basis=detached" in after[0]
    close(rig)


def test_h05a_a_read_that_could_not_finish_by_the_limit_is_not_started(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    run.safe_off()
    now = time.monotonic()
    from noctinirs_host.control.session import PROFILE_TIMEOUT_S
    # due long ago, still before the limit, but the 0.4 s wait could not end by it
    run._deferred_profile.update(due=now - 0.5, limit=now + PROFILE_TIMEOUT_S / 2)
    run._deferred_follow()
    assert sim.profile_after_safe_off == 0 and run._deferred_profile is None
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    texts = [t for t in rig.events("profile") if t.startswith("phase=after_safe_off")]
    assert texts == ["phase=after_safe_off not read: could not finish within 3 s of the first SAFE_OFF ACK"]
    close(rig)


def test_h05a_a_read_that_can_finish_by_the_limit_is_still_sent_once(tmp_path, monkeypatch):   # CONTROL
    rig, run = rig_streaming(tmp_path, monkeypatch)
    sim = run.controller._t.simulator
    sim.silent_profile = True                         # worst case: the whole timeout is spent waiting
    run.safe_off()
    from noctinirs_host.control.session import PROFILE_TIMEOUT_S
    now = time.monotonic()
    limit = now + PROFILE_TIMEOUT_S + 0.2
    run._deferred_profile.update(due=now - 0.5, limit=limit)
    run._deferred_follow()
    assert sim.profile_after_safe_off == 1 and time.monotonic() <= limit       # the wait ended by the limit
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    close(rig)
