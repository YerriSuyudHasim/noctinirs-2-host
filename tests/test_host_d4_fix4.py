"""Regression tests for the Codex host re-review IR_CODEX_HOST_D4_FIX3_2026-11-10 (H-02b, H-02c, H-01b).

Evidence class: host/unit and in-process simulation (EngineeringFake models written from the FIX2 patch
sources); no device, no firmware was run. Every test here fails on 8ef513f (code 6642428) and passes after
the fix, except the one marked CONTROL (see HOST_D4_FIX4_REPORT.md).
"""

from pathlib import Path

from noctinirs_host.control import profile as PR      # module access: new names fail per test on 6642428
from noctinirs_host.control.profile import EVENT_LIMIT, parse_profile
from noctinirs_host.engineering import RecordingTarget
from noctinirs_host.record.writer import RAW_EVENTS_LIMIT
from test_host_d4_fix2 import all_events, patch_fake, rig_streaming
from test_host_d4_fix3 import FIVE_DUPLICATES, H1, H2, CodexProbeFake, codex_probe_reply, raw_occurrences
from test_profile import close, start_recorded

# the two Codex replies: different raw tuples, identical comma-joined sidecar text on 6642428
COLLIDING = ("v4profile=1 stop_error=x stop_error=0,stop_error:1 stop_error=2",
             "v4profile=1 stop_error=x stop_error=0 stop_error=1,stop_error:2")


def texts(ev):
    return (ev.main,) + tuple(ev.ids)


def tokens(ev, name):
    return [t for line in texts(ev) for t in line.split(" ") if t.partition("=")[0] == name]


# ---- H-02b: reversible raw integrity / issue encoding -----------------------------------------------------------

def test_h02b_the_two_colliding_codex_replies_give_different_sidecar_text_and_read_back_exactly():
    a, b = (parse_profile(t) for t in COLLIDING)
    assert a.raw_integrity != b.raw_integrity and len(a.raw_integrity) == len(b.raw_integrity) == 3
    ea, eb = (PR.profile_event_lines(r, phase="ended") for r in (a, b))
    assert texts(ea) != texts(eb)
    for r, ev in ((a, ea), (b, eb)):
        assert r.stop_integrity_effective == "UNVERIFIED"
        assert len(tokens(ev, "raw_integrity")) == 3                      # one token per occurrence
        assert PR.raw_integrity_from_events(texts(ev)) == r.raw_integrity  # commas in values survive
        assert tokens(ev, "integrity_issues") == ["integrity_issues=duplicate:stop_error"]
        assert all(len(t) <= EVENT_LIMIT and not t.endswith(".") for t in texts(ev))


def test_h02b_values_with_commas_and_colons_survive_the_overflow_follow_ons():
    extra = FIVE_DUPLICATES + " stop_error=1,2:3 stop_slot=a,b,c stop_match=,"
    text = codex_probe_reply(extra)
    r = parse_profile(text)
    assert list(r.raw_integrity) == raw_occurrences(text) and len(r.raw_integrity) == 13
    ev = PR.profile_event_lines(r, phase="after_safe_off", boot_id=0xFFFFFFFFFFFFFFFF, plan_epoch=0xFFFFFFFF,
                                provenance=PR.profile_attribution(0, phase="after_safe_off",
                                                                  candidate_session=0xFFFFFFFF),
                                reply_state="SAFE_OFF")
    assert "raw_integrity=in_profile_ids" in ev.main and "hashes=in_profile_ids" in ev.main
    assert all(len(t) <= EVENT_LIMIT and not t.endswith(".") for t in texts(ev))
    assert PR.raw_integrity_from_events(texts(ev)) == r.raw_integrity
    assert len(tokens(ev, "raw_integrity")) == 13 + 1                      # 13 occurrences + the main marker
    joined = " ".join(ev.ids)
    assert f"hash={H1}" in joined.split(" ") and f"applied_hash={H2}" in joined.split(" ")
    issues = [t.partition("=")[2] for t in tokens(ev, "integrity_issues")]
    assert issues.count("in_profile_ids") == 1
    assert set(issues) - {"in_profile_ids"} == set(r.integrity_issues)


# ---- H-02c: the main line and its follow-ons are stored as one group --------------------------------------------

def run_at_limit(tmp_path, monkeypatch, free_slots):
    patch_fake(monkeypatch, CodexProbeFake)
    rig = start_recorded(tmp_path)
    run = rig.run
    rig.go(lambda: run.profile_view and run.profile_view["phase"] == "running")
    for _ in range(RAW_EVENTS_LIMIT - free_slots - len(run.recorder.metadata()["events"])):
        run.recorder.event("review_filler", "x")
    run.controller._last_hb = run.controller._clock()
    before = run.recorder.metadata()
    assert RAW_EVENTS_LIMIT - len(before["events"]) == free_slots
    assert run._profile_query("running")
    return rig, run, before["raw"]["event_drops"]


def test_h02c_one_free_slot_stores_an_explicit_incomplete_marker_not_an_orphan_main_line(tmp_path, monkeypatch):
    rig, run, drops_before = run_at_limit(tmp_path, monkeypatch, 1)
    group = PR.profile_event_lines(parse_profile(codex_probe_reply()), phase="running")
    needed = 1 + len(group.ids)
    meta = run.recorder.metadata()
    last = meta["events"][-1]
    assert last["kind"] == "profile"
    assert last["text"] == f"phase=running incomplete=1 reason=event_limit needed={needed} available=1"
    assert "in_profile_ids" not in last["text"]
    assert meta["raw"]["event_drops"] - drops_before == needed
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    stored = rig.sidecar()["events"]
    for i, e in enumerate(stored):          # no main line anywhere points at follow-ons that are missing
        if e["kind"] in ("profile", "profile_device") and "in_profile_ids" in e["text"]:
            assert i + 1 < len(stored) and stored[i + 1]["kind"] == e["kind"] + "_ids", e["text"]
    assert any("incomplete=1" in e["text"] for e in stored)
    close(rig)


def test_h02c_no_free_slot_stores_nothing_and_counts_the_group_and_the_marker_as_drops(tmp_path, monkeypatch):
    rig, run, drops_before = run_at_limit(tmp_path, monkeypatch, 0)
    group = PR.profile_event_lines(parse_profile(codex_probe_reply()), phase="running")
    meta = run.recorder.metadata()
    assert meta["events"][-1]["kind"] == "review_filler"
    assert meta["raw"]["event_drops"] - drops_before == 1 + len(group.ids) + 1
    assert any("event limit reached" in line for line in run.log)
    close(rig)


def test_h02c_with_room_the_whole_group_is_stored_contiguously(tmp_path, monkeypatch):   # CONTROL
    rig, run, drops_before = run_at_limit(tmp_path, monkeypatch, 5)
    events = run.recorder.metadata()["events"]
    start = max(i for i, e in enumerate(events) if e["kind"] == "profile")
    tail = events[start:]
    assert len(tail) >= 2 and [e["kind"] for e in tail[1:]] == ["profile_ids"] * (len(tail) - 1)
    assert tail[0]["text"].startswith("phase=running") and "hashes=in_profile_ids" in tail[0]["text"]
    assert f"hash={H1}" in " ".join(e["text"] for e in tail[1:])
    assert run.recorder.metadata()["raw"]["event_drops"] == drops_before
    close(rig)


def test_h02c_the_recorder_group_api_is_all_or_nothing(tmp_path, monkeypatch):
    rig, run, _ = run_at_limit(tmp_path, monkeypatch, 5)
    rec = run.recorder
    free = RAW_EVENTS_LIMIT - len(rec.metadata()["events"])
    assert free >= 2
    assert not rec.event_group([("g", str(i)) for i in range(free + 1)],
                               incomplete=("g", lambda n, a: f"incomplete=1 needed={n} available={a}"))
    events = rec.metadata()["events"]
    assert [e["text"] for e in events if e["kind"] == "g"] == [f"incomplete=1 needed={free + 1} available={free}"]
    assert rec.event_group([("g2", "a")] * (free - 1))
    assert len(rec.metadata()["events"]) == RAW_EVENTS_LIMIT
    close(rig)


# ---- H-01b: a new recording never carries the previous run's RAW epoch ------------------------------------------

def test_h01b_safe_off_on_a_new_unstarted_recording_has_no_plan_epoch_of_the_previous_run(tmp_path, monkeypatch):
    rig, run = rig_streaming(tmp_path, monkeypatch)
    rig.go(lambda: "epoch" in run.latest_raw)
    assert run.latest_raw["epoch"] == 1
    run.stop()
    rig.go(lambda: run.recorder_state == "FINALIZED" and run.recorder is None, seconds=15)
    first = [e["text"] for e in all_events(rig, "record_1.json") if e["kind"] == "profile"]
    assert any("plan_epoch=1" in t for t in first)                  # run 1 did carry its own epoch
    target = RecordingTarget.parse(str(Path(tmp_path)), "record_2", "")
    run.check_destination(target)
    rig.go(lambda: run.checked_target == target and run._job is None)
    run.arm(target)
    assert run.latest_raw == {}
    rig.go(lambda: run.recorder_state == "ARMED" and run._job is None)
    run.safe_off()
    rig.go(lambda: run.recorder_state == "FINALIZED", seconds=15)
    second = [e["text"] for e in all_events(rig, "record_2.json") if e["kind"] == "profile"]
    assert second and all("plan_epoch=" not in t for t in second), second
    after = [t for t in second if t.startswith("phase=after_safe_off")]
    assert len(after) == 1 and "basis=no_session" in after[0] and "candidate_session=" not in after[0]
    close(rig)
