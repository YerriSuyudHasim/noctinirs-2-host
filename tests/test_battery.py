"""Battery indicator (opcode 0x0033, ADR-0004 section 14, firmware 519a96a).

Host/unit and simulation evidence only. The golden bytes come from the reference client at the
pinned firmware commit; no device was involved.
"""

import json
import re
from pathlib import Path

import pytest

from helpers import build_envelope, build_raw
from noctinirs_host.control import battery as B
from noctinirs_host.control import protocol as P
from noctinirs_host.control.protocol import BatteryReading, decode_reply, encode_battery_ext
from noctinirs_host.control.session import (BATTERY_TIMEOUT_S, HEARTBEAT_PERIOD_S, LoopbackTransport,
                                            SessionController)
from noctinirs_host.control.simulator import DeviceSimulator
from noctinirs_host.control.udp import UDPTransport
from noctinirs_host.wire.errors import WireError
from noctinirs_host_ui.battery_text import format_battery
from test_udp_session import Clock, Peer, rig  # noqa: F401  (rig is a fixture)

VECTORS = json.loads((Path(__file__).parent / "fixtures/battery_vectors_519a96a.json").read_text())
BOOT, REQ = 0x1122334455667788, 0x01020304


def reply_bytes(ext, *, status=0, state=1, session=0, req=REQ, opcode=0x33, seq=9, boot=BOOT):
    return P.encode_reply(req, opcode, status, state=state, fault=0, simulated=False, boot_id=boot,
                          t_rx_us=0x10, extension=ext, session_id=session, sequence=seq,
                          timestamp_us=0x2000)


# ---- codec ---------------------------------------------------------------------------------

def test_pins_are_separate_and_the_rest_of_the_pin_is_unchanged():
    assert P.BATTERY_FIRMWARE_PIN == "519a96a26ede6517595d4f8a87cd64ab6b7a6fa5"
    assert P.FIRMWARE_PIN == "bd880f5890ea1a72d54e8ab9cc2ed55f002d1382"
    assert "section 14" in P.BATTERY_CONTRACT and "ADR0004/1" in P.BATTERY_CONTRACT
    assert P.OP_GET_BATTERY == 0x0033 and P.OPCODE_NAMES[0x33] == "get_battery"
    assert 0x33 not in P.MUTATING_OPS and 0x33 not in P.TIMED_OPS


def test_request_matches_reference_client_bytes():
    r = VECTORS["request"]
    assert P.encode_request(REQ, 0x33, session_id=r["session_id"], sequence=r["sequence"]).hex() == r["hex"]
    req = P.decode_request(bytes.fromhex(r["hex"]))
    assert (req.req_id, req.opcode, req.args, req.session_id) == (REQ, 0x33, b"", 0)


@pytest.mark.parametrize("name", ["valid", "initial", "stale", "unknown_command"])
def test_replies_match_reference_client_bytes_both_directions(name):
    v = VECTORS["replies"][name]
    wire = bytes.fromhex(v["hex"])
    assert reply_bytes(v["text"].encode(), status=v["status"], state=v["state"],
                       session=v["session_id"]).hex() == v["hex"]
    r = decode_reply(wire)
    assert (r.req_id, r.opcode, r.status, r.state, r.session_id, r.boot_id) == (
        REQ, 0x33, v["status"], v["state"], v["session_id"], BOOT)
    if v["status"] == 0:
        b = r.battery
        assert b == BatteryReading(*[
            bool(int(x)) if i == 0 else int(x) for i, x in enumerate(
                re.findall(r"=(-?\d+)", v["text"]))])
    else:
        assert r.battery is None and r.status_text == "UNKNOWN_COMMAND"


def test_reading_units_and_no_clamp():
    r = decode_reply(bytes.fromhex(VECTORS["replies"]["valid"]["hex"])).battery
    assert r.percent == 50.5 and r.volts == 4.0
    over = decode_reply(reply_bytes(encode_battery_ext(valid=True, uv=4300000, mperm=105250,
                                                       age_ms=0, error=0))).battery
    assert over.percent == 105.25


@pytest.mark.parametrize("text", [
    b"battery_valid=1 battery_uv=1 battery_mperm=1 battery_age_ms=1",                      # missing field
    b"battery_valid=1 battery_uv=1 battery_mperm=1 battery_age_ms=1 battery_error=0 x=1",  # extra field
    b"battery_uv=1 battery_valid=1 battery_mperm=1 battery_age_ms=1 battery_error=0",      # order
    b"battery_valid=1  battery_uv=1 battery_mperm=1 battery_age_ms=1 battery_error=0",     # spacing
    b"battery_valid=1 battery_uv=1 battery_mperm=1 battery_age_ms=1 battery_error=0\n",    # newline
    b"battery_valid=2 battery_uv=1 battery_mperm=1 battery_age_ms=1 battery_error=0",      # bad flag
    b"battery_valid=1 battery_uv=-1 battery_mperm=1 battery_age_ms=1 battery_error=0",     # negative uv
    "battery_valid=1 battery_uv=1 battery_mperm=1 battery_age_ms=1 battery_error=é".encode(),  # non-ASCII
    b"battery_valid=1 battery_uv=1 battery_mperm=1 battery_age_ms=-1 battery_error=0",     # valid, age < 0
    b"battery_valid=0 battery_uv=1 battery_mperm=0 battery_age_ms=-1 battery_error=259",   # invalid with uv
    b"battery_valid=0 battery_uv=0 battery_mperm=5 battery_age_ms=-1 battery_error=259",   # invalid with mperm
    b"battery_valid=0 battery_uv=0 battery_mperm=0 battery_age_ms=0 battery_error=259",    # invalid, age != -1
    b"battery_valid=0 battery_uv=0 battery_mperm=0 battery_age_ms=-1 battery_error=0",     # invalid, error 0
    b"battery_valid=1 battery_uv=" + b"9" * 21 + b" battery_mperm=1 battery_age_ms=1 battery_error=0",
    b"", b"\x01\x02" * 22,
])
def test_malformed_battery_extension_is_rejected(text):
    with pytest.raises(WireError):
        decode_reply(reply_bytes(text))


def test_error_status_must_not_carry_an_extension():
    ok_text = VECTORS["replies"]["valid"]["text"].encode()
    wire = P._envelope(1, P.struct.pack("<IHH", REQ, 0x33, 3) + P._PREFIX.pack(1, 0, 0, BOOT, 0x10)
                       + ok_text, 0, 0, 9, 0x2000)
    with pytest.raises(WireError):
        decode_reply(wire)


# ---- monitor (pure logic) ---------------------------------------------------------------------

def reading(valid=True, uv=3987000, mperm=50500, age=1000, error=0):
    return BatteryReading(valid, uv if valid else 0, mperm if valid else 0, age if valid else -1,
                          error if (error or valid) else 259)


def test_display_status_mapping():
    m = B.BatteryMonitor()
    assert m.snapshot(0)["status"] == B.UNKNOWN
    m.on_reading(1, reading(valid=False, error=259), False)
    s = m.snapshot(1)
    assert s["status"] == B.NOT_AVAILABLE and s["percent"] is None and "no sample" in s["detail"]
    m.on_reading(2, reading(valid=False, error=262), False)
    assert "not reported" in m.snapshot(2)["detail"]
    m.on_reading(3, reading(), False)
    s = m.snapshot(13)
    assert s["status"] == B.OK and s["percent"] == 50.5 and s["volts"] == pytest.approx(3.987)
    assert s["age_ms"] == 11000  # reported age plus the host time since
    m.on_reading(20, reading(mperm=48300, age=95000, error=263), False)
    s = m.snapshot(20)
    assert s["status"] == B.STALE_REFRESH_FAILED and s["percent"] == 48.3 and s["error"] == 263
    m.on_no_reply(30)
    s = m.snapshot(30)
    assert s["status"] == B.NO_REPLY and s["percent"] == 48.3  # last value is kept
    m.on_status_error(40, "BAD_FRAME")
    assert m.snapshot(40)["status"] == B.ERROR and m.snapshot(40)["detail"] == "BAD_FRAME"
    assert not m.unsupported and m.due(1000, False)
    m.on_unsupported(50)
    assert m.snapshot(50)["status"] == B.UNSUPPORTED and not m.snapshot(50)["supported"]
    assert not m.due(10_000, False)  # stops for this connection
    m.reset()
    assert m.due(0, False) and m.snapshot(0)["status"] == B.UNKNOWN


def test_percent_above_100_is_not_clamped():
    m = B.BatteryMonitor()
    m.on_reading(0, reading(mperm=105250), None)
    assert m.snapshot(0)["percent"] == 105.25
    assert "105,2" in format_battery(m.snapshot(0) | {"connected": True}).text


def test_schedule_idle_stream_and_post_stop():
    m = B.BatteryMonitor()
    assert m.due(0, False)
    m.attempted(0)
    assert not m.due(29.9, False) and m.due(30.0, False)
    assert not m.due(59.9, True) and m.due(60.0, True)
    # post-stop: first at +7 s, fresh data by device age ends the wait
    m.attempted(100)
    m.note_stopped(100)
    assert m.snapshot(100)["waiting_post_stop"] and not m.due(106.9, False) and m.due(107.0, False)
    m.on_reading(107, reading(age=2000), False)           # 2 s old < 7 s since STOP: fresh
    assert not m.snapshot(107)["waiting_post_stop"]
    # stale data: two repeats 5 s apart, then back to the 30 s schedule
    m.note_stopped(200)
    m.on_reading(207, reading(age=100_000), False)
    assert m.snapshot(207)["waiting_post_stop"] and not m.due(211.9, False) and m.due(212.0, False)
    m.on_reading(212, reading(age=100_000), False)
    assert m.due(217.0, False)
    m.attempted(217)
    m.on_reading(217, reading(age=100_000), False)
    assert not m.snapshot(217)["waiting_post_stop"]
    assert not m.due(246.9, False) and m.due(247.0, False)


# ---- controller with a simulator and a fake clock ---------------------------------------------

class Tap(LoopbackTransport):
    def __init__(self, sim):
        super().__init__(sim)
        self.opcodes = []

    def send(self, datagram):
        self.opcodes.append(P.decode_request(datagram).opcode)
        super().send(datagram)


def sim_rig(mode="ok"):
    clock = Clock()
    sim = DeviceSimulator(clock=clock)
    sim.battery_mode = mode
    tap = Tap(sim)
    ctl = SessionController(tap, clock)
    ctl.battery_enabled = True
    return clock, sim, tap, ctl


def advance(clock, ctl, seconds, step=0.25):
    end = clock.now + seconds
    while clock.now < end:
        clock.now += step
        ctl.tick()


def test_first_query_after_connect_and_failure_does_not_fail_connect():
    clock, sim, tap, ctl = sim_rig()
    assert ctl.connect().ok
    assert tap.opcodes[:3] == [P.OP_INFO, P.OP_GET_CONFIG, P.OP_GET_BATTERY]
    assert ctl.battery.snapshot(clock())["status"] == B.OK
    clock2, _, tap2, ctl2 = sim_rig("old")
    assert ctl2.connect().ok and ctl2.connected
    assert ctl2.battery.snapshot(0)["status"] == B.UNSUPPORTED
    clock3, _, _, ctl3 = sim_rig("silent")
    assert ctl3.connect().ok and ctl3.battery.snapshot(0)["status"] == B.NO_REPLY


def test_idle_every_30s_streaming_every_60s_and_heartbeats_are_not_delayed():
    clock, sim, tap, ctl = sim_rig()
    ctl.connect()
    q = lambda: ctl.battery_counters["queries"]  # noqa: E731
    advance(clock, ctl, 29)
    assert q() == 1
    advance(clock, ctl, 2)
    assert q() == 2
    assert ctl.start().ok and ctl.session_id
    base, hb0 = q(), tap.opcodes.count(P.OP_HEARTBEAT)
    advance(clock, ctl, 57)                              # last query at t=30, so the next is due at t=90
    assert q() == base
    advance(clock, ctl, 4)
    assert q() == base + 1
    hb = tap.opcodes.count(P.OP_HEARTBEAT) - hb0
    assert hb >= int(61 / HEARTBEAT_PERIOD_S) - 1       # the owner heartbeat kept its cadence
    assert ctl.session_id and ctl.counters["timeouts"] == 0 and sim.state == P.ST_STREAMING


def test_no_query_while_starting_stopping_or_start_uncertain():
    clock, sim, tap, ctl = sim_rig()
    ctl.connect()
    n = tap.opcodes.count(P.OP_GET_BATTERY)
    for state in (P.ST_STARTING, P.ST_STOPPING):
        ctl.device_state = state
        r = ctl.query_battery()
        assert not r.sent and r.status == "LOCAL_REJECT"
    ctl.device_state = P.ST_STOPPED
    ctl._start_probe_at = clock() + 10
    assert not ctl.query_battery().sent
    ctl._start_probe_at = None
    ctl.battery.attempted(-1000)                          # long overdue, but the state forbids it
    for state in (P.ST_STARTING, P.ST_STOPPING):
        ctl.device_state = state
        ctl._battery_tick(clock())
    ctl.device_state, ctl._start_probe_at = P.ST_STOPPED, clock() + 10
    ctl._battery_tick(clock())
    assert tap.opcodes.count(P.OP_GET_BATTERY) == n


def test_after_stop_query_near_7s_and_freshness_from_device_age():
    clock, sim, tap, ctl = sim_rig()
    ctl.connect()
    assert ctl.start().ok
    advance(clock, ctl, 3)
    assert ctl.stop().ok and ctl.device_state == P.ST_STOPPED
    stopped_at = clock.now
    assert ctl.battery.snapshot(clock())["waiting_post_stop"]
    n = ctl.battery_counters["queries"]
    advance(clock, ctl, 6.5)
    assert ctl.battery_counters["queries"] == n          # not before +7 s
    advance(clock, ctl, 1.0)
    assert ctl.battery_counters["queries"] == n + 1
    s = ctl.battery.snapshot(clock())
    assert not s["waiting_post_stop"]                    # simulator refreshed 5 s after the stream ended
    assert s["age_ms"] < (clock.now - stopped_at) * 1000


def test_stale_after_stop_repeats_at_most_twice_then_returns_to_30s():
    clock, sim, tap, ctl = sim_rig()
    sim.battery_age_override_ms = 3_600_000             # the device keeps reporting an old sample
    ctl.connect()
    assert ctl.start().ok
    advance(clock, ctl, 2)
    assert ctl.stop().ok
    n = ctl.battery_counters["queries"]
    advance(clock, ctl, 8)
    assert ctl.battery_counters["queries"] == n + 1 and ctl.battery.snapshot(clock())["waiting_post_stop"]
    advance(clock, ctl, 5)
    advance(clock, ctl, 5)
    assert ctl.battery_counters["queries"] == n + 3
    assert not ctl.battery.snapshot(clock())["waiting_post_stop"]
    advance(clock, ctl, 20)
    assert ctl.battery_counters["queries"] == n + 3
    advance(clock, ctl, 11)
    assert ctl.battery_counters["queries"] == n + 4


def test_unsupported_stops_polling_and_does_not_disturb_the_link():
    clock, sim, tap, ctl = sim_rig("old")
    ctl.connect()
    assert ctl.battery_counters["unsupported"] == 1
    advance(clock, ctl, 130)
    assert tap.opcodes.count(P.OP_GET_BATTERY) == 1
    assert ctl.connected and ctl.counters["timeouts"] == 0
    ctl.disconnect()
    assert ctl.battery.snapshot(0)["status"] == B.UNKNOWN  # new connection asks again


def test_query_never_refreshes_the_simulator_lease_or_adopts_state():
    clock, sim, tap, ctl = sim_rig()
    ctl.connect()
    assert ctl.start().ok
    active = sim._owner_active_at
    state, fault, session = ctl.device_state, ctl.fault_code, ctl.session_id
    clock.now += 0.4
    assert ctl.query_battery().ok
    assert sim._owner_active_at == active                   # not a heartbeat
    assert (ctl.device_state, ctl.fault_code, ctl.session_id) == (state, fault, session)
    wire = P.encode_request(77, P.OP_GET_BATTERY, b"x", session_id=0)
    assert decode_reply(sim.handle(wire)[0]).status == P.STATUS_INVALID_ARG


# ---- UDP peer: routing, correlation, ownership ------------------------------------------------

GOOD = encode_battery_ext(valid=True, uv=3987000, mperm=50500, age_ms=1500, error=0)


def test_raw_around_a_battery_reply_is_routed_unchanged(rig):  # noqa: F811
    peer, _, _, ctl = rig
    peer.battery = GOOD
    ctl.battery_enabled = True
    assert ctl.set_mode("ENGINEERING").ok and ctl.start().ok
    peer.complete_start()
    assert ctl.refresh_status().ok and ctl.device_state == P.ST_STREAMING
    seen = []
    ctl.raw_sink = seen.append
    good = build_envelope(build_raw(), session_id=peer.session)
    bad = build_envelope(build_raw(count=0), session_id=peer.session)
    foreign = build_envelope(build_raw(), session_id=peer.session + 1)
    peer.filter = lambda p, req, raw, reply, source: [good, foreign, bad, reply]
    assert ctl.query_battery().ok
    assert seen == [good]
    assert ctl.counters["raw_routed"] == 1 and ctl.counters["wrong_session"] == 1
    assert ctl.counters["malformed"] == 1 and ctl.battery.snapshot(ctl._clock())["status"] == B.OK


def test_uncorrelated_replies_are_not_battery_results(rig):  # noqa: F811
    peer, _, _, ctl = rig
    peer.battery = GOOD
    ctl.battery_enabled = True
    state, session = ctl.device_state, ctl.session_id

    def wrong(p, req, raw, reply, source):
        return [reply_bytes(GOOD, req=req.req_id + 1, state=P.ST_FAULTED, boot=peer.boot),
                reply_bytes(b"", opcode=P.OP_SET_MODE, req=req.req_id, state=P.ST_FAULTED, boot=peer.boot)]

    peer.filter = wrong
    r = ctl.query_battery()
    assert r.status == "TIMEOUT" and ctl.battery.snapshot(ctl._clock())["status"] == B.NO_REPLY
    assert ctl.counters["unmatched"] >= 2 and ctl.battery_counters["timeouts"] == 1
    assert (ctl.device_state, ctl.session_id) == (state, session)
    old = reply_bytes(GOOD, req=peer.requests[-1][1].req_id, boot=peer.boot)
    peer.filter = lambda *_a: [old, old]               # a late reply to an earlier request
    assert ctl.query_battery().status == "TIMEOUT"
    peer.filter = lambda p, req, raw, reply, source: [reply]
    assert ctl.query_battery().ok and ctl.battery.snapshot(ctl._clock())["status"] == B.OK


def test_battery_miss_is_not_a_link_loss_and_keeps_heartbeats(rig):  # noqa: F811
    peer, _, clock, ctl = rig
    ctl.battery_enabled = True
    peer.filter = lambda p, req, raw, reply, source: [] if req.opcode == P.OP_GET_BATTERY else [reply]
    assert ctl.start().ok
    peer.complete_start()
    timeouts, session = ctl.counters["timeouts"], ctl.session_id
    for _ in range(30):                                  # 15 s of ticks
        clock.now += 0.5
        ctl.tick()
    assert ctl.battery_counters["timeouts"] >= 1 and ctl.counters["timeouts"] == timeouts
    assert ctl.session_id == session and ctl.connected
    heartbeats = [r for _, r, _ in peer.requests if r.opcode == P.OP_HEARTBEAT]
    assert len(heartbeats) >= 28
    assert ctl.battery.snapshot(clock())["status"] == B.NO_REPLY


def test_unknown_command_from_old_firmware_via_udp(rig):  # noqa: F811
    peer, _, _, ctl = rig
    ctl.battery_enabled = True
    r = ctl.query_battery()
    assert r.status == "UNKNOWN_COMMAND" and ctl.battery.unsupported
    assert ctl.counters["timeouts"] == 0 and ctl.battery_counters["unsupported"] == 1
    assert not ctl.battery.due(10_000, False)


def test_other_device_session_id_is_neither_adopted_nor_rejected(rig):  # noqa: F811
    peer, _, _, ctl = rig
    peer.battery = GOOD
    ctl.battery_enabled = True
    assert ctl.start().ok
    peer.complete_start()
    assert ctl.refresh_status().ok
    mine = ctl.session_id
    peer.filter = lambda p, req, raw, reply, source: [reply_bytes(GOOD, session=mine + 500, req=req.req_id,
                                                                  state=P.ST_STREAMING, boot=peer.boot)]
    assert ctl.query_battery().ok
    assert ctl.session_id == mine and ctl.counters["wrong_session"] == 0


def test_observer_endpoint_gets_battery_without_adopting_or_heartbeating(rig):  # noqa: F811
    peer, _, _, owner = rig
    peer.battery = GOOD
    assert owner.start().ok
    peer.complete_start()
    clock = Clock()
    observer = SessionController(UDPTransport(*peer.endpoint), clock, timeout_s=.025, retries=0)
    observer.battery_enabled = True
    try:
        assert observer.connect().ok
        r = observer.query_battery()
        assert r.ok and observer.session_id == 0 and observer.state_text == "STREAMING"
        assert r.reply.session_id == peer.session          # device session, data only
        assert observer.battery.snapshot(clock())["status"] == B.OK
        source = observer._t.local_endpoint[1]
        mine = [r_ for _, r_, s in peer.requests if s[1] == source]
        assert P.OP_HEARTBEAT not in [r_.opcode for r_ in mine]
        assert owner.session_id == peer.session and peer.owner[1] != source and peer.state == P.ST_STREAMING
        assert owner.counters["wrong_session"] == 0
    finally:
        observer.disconnect()


def test_battery_enabled_defaults_off_for_the_plain_controller():
    ctl = SessionController(LoopbackTransport(DeviceSimulator()))
    assert not ctl.battery_enabled and ctl.connect().ok
    assert ctl.battery_counters["queries"] == 0 and BATTERY_TIMEOUT_S < HEARTBEAT_PERIOD_S


# ---- display text -----------------------------------------------------------------------------

def view(**kw):
    base = dict(status="OK", connected=True, percent=50.5, volts=3.987, uv=3987000, mperm=50500,
                age_ms=12000, error=0, detail="", simulated=True, supported=True,
                waiting_post_stop=False, streaming=False)
    return format_battery({**base, **kw})


def test_text_variants_and_no_charging_claim():
    assert view().text == "Baterai 50,5 % · 3,99 V · 12 s lalu"
    assert "3,987 V" not in view().text and "3987 mV" in view().tooltip.replace(".", "")
    assert view(status="NOT_AVAILABLE", percent=None, volts=None, detail="no sample since boot").text \
        .startswith("Baterai belum tersedia")
    assert "0 %" not in view(status="NOT_AVAILABLE", percent=None, volts=None).text
    t = view(status="STALE_REFRESH_FAILED", percent=48.3, volts=3.987, error=263, age_ms=95000).text
    assert t == "Baterai 48,3 % · refresh gagal (263) · 95 s lalu"
    assert view(status="UNSUPPORTED", percent=None).text == "Baterai: tidak didukung firmware ini"
    assert view(status="ERROR", percent=None, detail="BAD_FRAME").text == "Baterai: error BAD_FRAME"
    assert view(status="NO_REPLY", percent=None).text == "Baterai: tidak ada jawaban"
    assert "tidak ada jawaban" in view(status="NO_REPLY").text and "50,5" in view(status="NO_REPLY").text
    assert format_battery({"status": "DISCONNECTED", "connected": False}).text == "Baterai —"
    assert "nilai cache sebelum akuisisi" in view(streaming=True).text
    assert "menunggu refresh setelah STOP" in view(waiting_post_stop=True).text
    assert "105,2" in view(percent=105.25).text and "100" in view(percent=105.25).tooltip
    assert view(percent=12.0).level == "low" and view().level == "normal"
    for t in (view(), view(streaming=True), view(status="STALE_REFRESH_FAILED", error=263)):
        low = (t.text + t.tooltip).lower()
        assert "charging" not in low and "mengisi" not in low.replace("status pengisian tidak dilaporkan", "")
