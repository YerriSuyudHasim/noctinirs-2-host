"""Control layer tests: codec (firmware golden vectors), simulator model, controller.

Evidence class: host/unit + simulation. The simulator is a model of the pinned
firmware documents, not the firmware.
"""

import json
import pathlib
import struct

import pytest

from noctinirs_host.control import protocol as P
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.control.simulator import GRACE_S, SILENCE_S, DeviceSimulator
from noctinirs_host.wire.errors import WireError

VEC = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "fw_golden_vectors_cbec2da.json").read_text())


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(retries=2):
    clk = Clock()
    sim = DeviceSimulator(clk)
    tr = LoopbackTransport(sim)
    ctl = SessionController(tr, clk, retries=retries)
    return clk, sim, tr, ctl


def connected(**kw):
    clk, sim, tr, ctl = make(**kw)
    assert ctl.connect().ok
    return clk, sim, tr, ctl


# --- codec against firmware golden vectors ---------------------------------
@pytest.mark.parametrize("v", VEC, ids=[v["name"] for v in VEC])
def test_request_encoding_matches_firmware_golden_vectors(v):
    wire = P.encode_request(v["id"], v["opcode"], bytes.fromhex(v["args_hex"]),
                            session_id=v["session"], sequence=v["sequence"])
    assert wire.hex() == v["wire_hex"]


@pytest.mark.parametrize("v", VEC, ids=[v["name"] for v in VEC])
def test_golden_request_decodes(v):
    r = P.decode_request(bytes.fromhex(v["wire_hex"]))
    assert (r.req_id, r.opcode, r.args, r.session_id) == (
        v["id"], v["opcode"], bytes.fromhex(v["args_hex"]), v["session"])


def test_decode_request_fail_closed():
    good = bytes.fromhex(VEC[0]["wire_hex"])
    for bad in (good[:-1], good[:-1] + bytes([good[-1] ^ 1]), b"\x00" * 40, good[:10]):
        with pytest.raises(WireError):
            P.decode_request(bad)


def test_reply_roundtrip_and_error_has_no_extension():
    r = P.decode_reply(P.encode_reply(7, P.OP_START, 0, state=3, fault=0, simulated=True,
                                      boot_id=9, t_rx_us=5, session_id=4))
    assert r.ok and r.state_text == "STREAMING" and r.simulated and r.session_id == 4
    e = P.decode_reply(P.encode_reply(7, P.OP_SET_FS, 2, state=1, fault=0, simulated=True,
                                      boot_id=9, t_rx_us=5, extension=b"junk"))
    assert e.status_text == "INVALID_ARG" and e.extension == b""


def test_reply_provenance_mismatch_rejected():
    raw = bytearray(P.encode_reply(1, P.OP_STOP, 0, state=1, fault=0, simulated=True,
                                   boot_id=1, t_rx_us=0))
    raw[28 + 8 + 3] = 0  # body simulated byte -> 0 while header flag stays 1
    import zlib
    raw[-4:] = struct.pack("<I", zlib.crc32(bytes(raw[:-4])))
    with pytest.raises(WireError):
        P.decode_reply(bytes(raw))


# --- every command round-trips ----------------------------------------------------
def test_each_command_roundtrips_against_simulator():
    clk, sim, tr, ctl = connected()
    assert ctl.simulated is True
    assert ctl.settings.fs_min == 2 and ctl.settings.fs_max == 10
    assert ctl.settings.fs_hz == 5 and ctl.settings.current_ma == [30] * 20
    assert ctl.refresh_status().ok
    assert ctl.set_fs(8).ok and ctl.settings.fs_hz == 8 and sim.fs_hz == 8
    assert ctl.set_led_current(3, 12).ok and sim.current_ma[3] == 12
    assert ctl.start().ok and ctl.state_text == "STREAMING" and ctl.session_id == 1
    sent = ctl.counters["sent"]
    clk.t += 0.6
    ctl.tick()
    assert ctl.counters["sent"] == sent + 1  # heartbeat went out (and stays out of the log)
    assert not any(e.text.startswith("heartbeat") for e in ctl.events)
    assert ctl.get_time().ok
    assert ctl.stop().ok and ctl.state_text == "STOPPED" and ctl.session_id == 0
    assert ctl.safe_off().ok and ctl.state_text == "SAFE_OFF"
    assert ctl.clear_fault().ok and ctl.state_text == "STOPPED"


# --- local validation sends nothing ------------------------------------------------
@pytest.mark.parametrize("call", [
    lambda c: c.set_fs(1), lambda c: c.set_fs(11), lambda c: c.set_fs(5.0),
    lambda c: c.set_fs(True), lambda c: c.set_led_current(20, 10),
    lambda c: c.set_led_current(-1, 10), lambda c: c.set_led_current(0, 51),
    lambda c: c.set_led_current(0, -1), lambda c: c.set_led_current(0, 1.5),
])
def test_invalid_values_rejected_locally_without_a_packet(call):
    _, _, _, ctl = connected()
    sent = ctl.counters["sent"]
    res = call(ctl)
    assert not res.ok and not res.sent and res.status == "LOCAL_REJECT"
    assert ctl.counters["sent"] == sent
    assert "REJECTED locally" in ctl.events[-1].text


def test_settings_rejected_locally_while_streaming():
    _, _, _, ctl = connected()
    assert ctl.start().ok
    sent = ctl.counters["sent"]
    assert ctl.set_fs(4).status == "LOCAL_REJECT"
    assert ctl.set_led_current(0, 5).status == "LOCAL_REJECT"
    assert ctl.start().status == "LOCAL_REJECT"
    assert ctl.counters["sent"] == sent


def test_limits_unknown_means_no_fs_change():
    _, sim, _, ctl = make()
    ctl.connected = True
    ctl.device_state = P.ST_STOPPED
    assert ctl.set_fs(5).status == "LOCAL_REJECT"


def test_simulator_rejects_what_the_controller_would_never_send():
    clk, sim, tr, ctl = connected()

    ids = iter(range(900, 1000))

    def raw(op, args):
        return P.decode_reply(sim.handle(P.encode_request(next(ids), op, args))[0])
    assert raw(P.OP_SET_FS, b"\x01").status_text == "INVALID_ARG"
    assert raw(P.OP_SET_FS, b"\x0b").status_text == "INVALID_ARG"
    assert raw(P.OP_SET_LED_CURRENT, struct.pack("<BH", 20, 1)).status_text == "INVALID_ARG"
    assert raw(P.OP_SET_LED_CURRENT, struct.pack("<BH", 0, 51)).status_text == "INVALID_ARG"
    assert raw(0x7777, b"").status_text == "UNKNOWN_COMMAND"
    assert ctl.start().ok
    assert raw(P.OP_SET_FS, b"\x05").status_text == "INVALID_STATE"


# --- link loss, safe_off, duplicates, loss ----------------------------------------
def test_heartbeat_loss_ends_stream_as_link_loss_and_controller_reports_it():
    clk, sim, tr, ctl = connected()
    assert ctl.start().ok
    clk.t += SILENCE_S + 0.1
    sim.poll()
    assert sim.grace_active
    clk.t += GRACE_S + 0.1
    sim.poll()
    assert sim.last_end_reason == "LINK_LOSS" and sim.counters["grace_expirations"] == 1
    clk.t += 1.0
    ctl.tick()  # heartbeat -> INVALID_STATE -> controller notices
    assert ctl.state_text == "STOPPED" and ctl.last_end.startswith("LINK_LOSS")
    assert any(e.kind == "link" and "no longer streaming" in e.text for e in ctl.events)
    assert ctl.session_id == 0


def test_heartbeats_keep_the_stream_alive():
    clk, sim, tr, ctl = connected()
    assert ctl.start().ok
    for _ in range(40):
        clk.t += 0.5
        ctl.tick()
    assert sim.state == P.ST_STREAMING and not sim.grace_active


def test_valid_request_cancels_grace():
    clk, sim, tr, ctl = connected()
    ctl.start()
    clk.t += SILENCE_S + 0.1
    sim.poll()
    assert sim.grace_active
    ctl.tick()
    assert not sim.grace_active and sim.state == P.ST_STREAMING


@pytest.mark.parametrize("setup", ["stopped", "streaming", "safe_off"])
def test_safe_off_accepted_in_every_state(setup):
    _, sim, _, ctl = connected()
    if setup == "streaming":
        ctl.start()
    if setup == "safe_off":
        ctl.safe_off()
    assert ctl.safe_off().ok and sim.state == P.ST_SAFE_OFF and ctl.session_id == 0


def test_safe_off_is_not_blocked_locally_even_when_disconnected_state_unknown():
    clk, sim, tr, ctl = make()
    assert ctl.safe_off().ok  # not connected yet: still sent


def test_safe_off_retries_harder_than_other_commands():
    _, _, tr, ctl = connected(retries=0)
    tr.drop_next_replies = 3
    assert ctl.safe_off().ok
    tr.drop_next_replies = 1
    assert ctl.refresh_status().status == "TIMEOUT"


def test_lost_reply_retry_returns_cached_ack_and_does_not_reexecute():
    _, sim, tr, ctl = connected()
    tr.drop_next_replies = 1
    assert ctl.start().ok
    assert sim.counters["duplicates"] == 1 and sim._next_session == 2  # one session only
    assert ctl.counters["retries"] == 1


def test_duplicate_and_unrelated_replies_are_counted_not_fatal():
    _, sim, tr, ctl = connected()
    tr.duplicate_next_reply = True
    assert ctl.set_fs(4).ok
    assert ctl.refresh_status().ok  # duplicate of the previous reply arrives here
    assert ctl.counters["duplicate_replies"] >= 1


def test_garbage_replies_counted():
    _, sim, tr, ctl = connected()
    tr._queue.append(b"\x00" * 40)
    assert ctl.refresh_status().ok
    assert ctl.counters["malformed"] == 1


def test_no_reply_gives_timeout_not_exception():
    _, _, tr, ctl = connected(retries=1)
    tr.drop_next_replies = 5
    res = ctl.set_fs(4)
    assert res.status == "TIMEOUT" and not res.ok
    assert ctl.settings.fs_hz == 5  # unchanged: never assume success


def test_mutation_cache_same_id_different_args_is_invalid_arg():
    _, sim, _, _ = connected()
    a = P.decode_reply(sim.handle(P.encode_request(50, P.OP_SET_FS, b"\x04"))[0])
    b = P.decode_reply(sim.handle(P.encode_request(50, P.OP_SET_FS, b"\x06"))[0])
    assert a.ok and b.status_text == "INVALID_ARG" and sim.fs_hz == 4


def test_reserved_flags_rejected_before_any_effect():
    _, sim, _, _ = connected()
    r = P.decode_reply(sim.handle(P.encode_request(60, P.OP_SAFE_OFF, flags=0x0002))[0])
    assert r.status_text == "RESERVED_FLAGS" and sim.state == P.ST_STOPPED
    assert sim.counters["reserved_flags_rejects"] == 1


def test_malformed_datagram_is_dropped_and_counted():
    _, sim, _, _ = connected()
    assert sim.handle(b"garbage") == []
    assert sim.counters["malformed"] == 1


def test_reboot_detected_by_boot_id():
    clk, sim, tr, ctl = connected()
    sim.boot_id += 1
    res = ctl.refresh_status()
    assert res.status == "TIMEOUT" and not ctl.connected
    assert any("rebooted" in e.text for e in ctl.events)


def test_clear_fault_blocked_while_fault_active():
    _, sim, _, ctl = connected()
    ctl.safe_off()
    sim.fault_active = True
    assert ctl.clear_fault().status == "PRECONDITION_FAILED"
    sim.fault_active = False
    assert ctl.clear_fault().ok


def test_every_change_is_logged_with_old_and_new_value():
    _, _, _, ctl = connected()
    ctl.set_fs(9)
    ctl.set_led_current(2, 40)
    texts = [e.text for e in ctl.events]
    assert any("set_fs 5 -> 9 Hz" in t for t in texts)
    assert any("set_led_current ch2 30 -> 40 mA" in t for t in texts)
    assert all(e.simulated for e in ctl.events if e.kind != "info")


def test_listener_receives_events():
    _, _, _, ctl = make()
    seen = []
    ctl.listeners.append(seen.append)
    ctl.connect()
    assert seen and seen[-1].kind == "link"


# --- review round 1 (PR #25 at cb94b7f) ---------------------------------------------
def test_late_first_poll_does_not_extend_grace():
    """Grace is measured from the silence deadline, not from when a poll notices it."""
    clk, sim, tr, ctl = connected()
    ctl.start()
    clk.t += 5.0  # 5 s of silence: past silence (1.5 s) + grace (3 s) = 4.5 s
    sim.poll()
    assert sim.state == P.ST_STOPPED and sim.last_end_reason == "LINK_LOSS"
    ctl.tick()  # a heartbeat now must not revive the stream
    assert ctl.state_text == "STOPPED" and ctl.last_end.startswith("LINK_LOSS")


def test_heartbeat_arriving_after_deadline_is_refused_without_prior_poll():
    clk, sim, tr, ctl = connected()
    ctl.start()
    clk.t += 5.0  # no poll in between: handle() must apply the deadline itself
    res = P.decode_reply(sim.handle(P.encode_request(77, P.OP_HEARTBEAT, struct.pack("<Q", 1),
                                                     session_id=1))[0])
    assert res.status_text == "INVALID_STATE" and sim.state == P.ST_STOPPED


def test_grace_started_exactly_at_silence_deadline_even_if_polled_late():
    clk, sim, tr, ctl = connected()
    ctl.start()
    clk.t += SILENCE_S + 1.0  # inside grace window (1.5 s .. 4.5 s)
    sim.poll()
    assert sim.grace_active and sim.state == P.ST_STREAMING
    clk.t += GRACE_S - 1.0 + 0.01  # now 4.51 s after the last owner request
    sim.poll()
    assert sim.state == P.ST_STOPPED


@pytest.mark.parametrize("op", [P.OP_INFO, P.OP_GET_CONFIG, P.OP_START, P.OP_STOP, P.OP_CLEAR_FAULT])
def test_trailing_bytes_on_zero_argument_commands_are_invalid_arg(op):
    clk, sim, tr, ctl = connected()
    r = P.decode_reply(sim.handle(P.encode_request(300 + op, op, b"\x00", session_id=0))[0])
    assert r.status_text == "INVALID_ARG" and sim.state == P.ST_STOPPED


def test_safe_off_ignores_trailing_bytes_model_choice():
    clk, sim, tr, ctl = connected()
    r = P.decode_reply(sim.handle(P.encode_request(400, P.OP_SAFE_OFF, b"\x00\x01"))[0])
    assert r.ok and sim.state == P.ST_SAFE_OFF


def test_protocol_version_mismatch_gets_bad_frame_reply():
    clk, sim, tr, ctl = connected()
    import zlib
    raw = bytearray(P.encode_request(500, P.OP_INFO))
    struct.pack_into("<H", raw, 6, 2)  # protocol_version = 2
    raw[-4:] = struct.pack("<I", zlib.crc32(bytes(raw[:-4])))
    reply = sim.handle(bytes(raw))
    assert len(reply) == 1 and P.decode_reply(reply[0]).status_text == "BAD_FRAME"
    assert sim.state == P.ST_STOPPED


@pytest.mark.parametrize("call", [lambda c: c.stop(), lambda c: c.clear_fault()])
def test_wrong_state_stop_and_clear_fault_send_no_packet(call):
    _, _, _, ctl = connected()
    sent = ctl.counters["sent"]
    res = call(ctl)
    assert res.status == "LOCAL_REJECT" and not res.sent and ctl.counters["sent"] == sent


def test_stop_is_still_sent_after_a_lost_stop_reply():
    _, sim, tr, ctl = connected(retries=0)
    ctl.start()
    tr.drop_next_replies = 1
    assert ctl.stop().status == "TIMEOUT"
    assert ctl.state_text == "STREAMING"  # host never assumes success
    res = ctl.stop()  # the first STOP did execute; a new request id gets INVALID_STATE ...
    assert res.status == "INVALID_STATE" and res.sent
    assert ctl.state_text == "STOPPED" and ctl.session_id == 0  # ... and the state converges


def test_stale_state_is_refreshed_while_not_streaming():
    clk, sim, tr, ctl = connected()
    sim.state = P.ST_SAFE_OFF  # e.g. changed through the USB console
    clk.t += 2.1
    ctl.tick()
    assert ctl.state_text == "SAFE_OFF"
    assert not any(e.text.startswith("status") for e in ctl.events)  # quiet when OK


def test_unrelated_stale_and_wrong_opcode_replies_do_not_break_a_transaction():
    _, sim, tr, ctl = connected()
    stale = P.encode_reply(999999, P.OP_STATUS, 0, state=1, fault=0, simulated=True,
                           boot_id=sim.boot_id, t_rx_us=0, extension=b"\x00" * 45)
    wrong_op = P.encode_reply(ctl._next_id, P.OP_INFO, 0, state=1, fault=0, simulated=True,
                              boot_id=sim.boot_id, t_rx_us=0, extension=b"x=1")
    tr._queue[:0] = [stale, wrong_op]
    assert ctl.set_fs(7).ok
    assert ctl.counters["unmatched"] >= 2 and sim.fs_hz == 7
