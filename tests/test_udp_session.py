"""Socket-loopback simulation; self-made replies, not firmware/hardware evidence.

The peer models the bd880f5 active control subset independently of DeviceSimulator.
Its asynchronous completion is driven explicitly by each test, never by sleeps.
"""

import socket
import struct
import threading
import time
import zlib

import pytest

from helpers import build_envelope, build_raw
from noctinirs_host.control import protocol as P
from noctinirs_host.control.session import SessionController
from noctinirs_host.control.udp import DEFAULT_PORT, UDPTransport


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Peer:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.endpoint = self.sock.getsockname()
        self.done = threading.Event()
        self.state, self.session, self.boot = P.ST_STOPPED, 0, 42
        self.owner = None
        self.fs, self.mode = 5, "ACQ"
        self.capability = "fs_min=2;fs_max=10;fs_allowed=2,4,5,8,10;current_max_ma=35"
        self.requests, self.cache, self.errors = [], {}, []
        self.executed_starts = 0
        self.filter = lambda peer, req, raw, reply, source: [reply]
        self.thread = threading.Thread(target=self.run, name="fake-udp-peer")
        self.thread.start()

    def reply(self, req, status=0, extension=b"", **kw):
        return P.encode_reply(req.req_id, req.opcode, status, state=self.state, fault=0,
                              simulated=False, boot_id=self.boot, t_rx_us=10,
                              session_id=self.session, extension=extension, **kw)

    def execute(self, req, source):
        op = req.opcode
        if req.session_id and req.session_id != self.session and op != P.OP_SAFE_OFF:
            return self.reply(req, 1)
        if self.owner and source != self.owner and op not in (
                P.OP_INFO, P.OP_GET_CONFIG, P.OP_GET_TIME, P.OP_SAFE_OFF):
            return self.reply(req, 1)
        ext = b""
        if op == P.OP_INFO:
            ext = (self.capability + ";mode=" + self.mode).encode("ascii")
        elif op == P.OP_GET_CONFIG:
            ext = struct.pack("<BB20H", self.fs, 20, *([30] * 20))
        elif op in P.TIMED_OPS:
            ext = req.args + struct.pack("<QQ", 10, 11)
            if op == P.OP_STATUS:
                ext += struct.pack("<5IB", len(self.requests), 0, 0, 0, 0, 0)
        elif op in (P.OP_SET_FS, P.OP_SET_MODE, P.OP_START):
            if self.state != P.ST_STOPPED:
                return self.reply(req, 1)
            if op == P.OP_SET_FS:
                self.fs = req.args[0]
            elif op == P.OP_SET_MODE:
                self.mode = "ENGINEERING" if req.args == b"\x01" else "ACQ"
            else:
                self.executed_starts += 1
                self.session = 100 + self.executed_starts
                self.owner, self.state = source, P.ST_STARTING
        elif op == P.OP_STOP:
            self.state = P.ST_STOPPING
        elif op == P.OP_SAFE_OFF:
            self.state, self.session, self.owner = P.ST_SAFE_OFF, 0, None
        elif op == P.OP_CLEAR_FAULT:
            self.state, self.session, self.owner = P.ST_STOPPED, 0, None
        return self.reply(req, extension=ext)

    def run(self):
        try:
            while True:
                raw, source = self.sock.recvfrom(65536)
                if self.done.is_set():
                    return
                req = P.decode_request(raw)
                self.requests.append((raw, req, source))
                key = source, raw
                if req.opcode in P.MUTATING_OPS and key in self.cache:
                    reply = self.cache[key]
                else:
                    reply = self.execute(req, source)
                    if req.opcode in P.MUTATING_OPS:
                        self.cache[key] = reply
                for datagram in self.filter(self, req, raw, reply, source):
                    self.sock.sendto(datagram, source)
        except BaseException as exc:
            self.errors.append(exc)

    def complete_start(self):
        self.state = P.ST_STREAMING

    def complete_stop(self):
        self.state, self.session, self.owner = P.ST_STOPPED, 0, None

    def close(self):
        self.done.set()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
            wake.sendto(b"", self.endpoint)
        self.thread.join(2)
        self.sock.close()
        assert not self.thread.is_alive(), "fake peer did not exit"
        assert not self.errors, self.errors


@pytest.fixture
def rig():
    peer = Peer()
    transport = UDPTransport(*peer.endpoint)
    clock = Clock()
    ctl = SessionController(transport, clock, timeout_s=0.025, retries=1)
    try:
        assert ctl.connect().ok
        yield peer, transport, clock, ctl
    finally:
        ctl.disconnect()
        peer.close()


def test_explicit_endpoint_default_port_and_context_cleanup():
    assert DEFAULT_PORT == 3333
    with UDPTransport("127.0.0.1") as transport:
        assert transport.endpoint == ("127.0.0.1", 3333)
        assert transport.local_endpoint[1] > 0
    assert transport.closed
    transport.close()
    with pytest.raises(OSError, match="closed"):
        transport.send(b"x")


@pytest.mark.parametrize("host,port", [("", 3333), ("localhost", 3333),
    ("0.0.0.0", 3333), ("224.0.0.1", 3333), ("255.255.255.255", 3333),
    ("127.0.0.1", 0), ("127.0.0.1", 65536), ("127.0.0.1", True)])
def test_invalid_endpoint_opens_no_socket(host, port):
    with pytest.raises(ValueError):
        UDPTransport(host, port)


@pytest.mark.parametrize("timeout,retries", [(0, 2), (-1, 2), (float("nan"), 2),
    (float("inf"), 2), (.5, -1), (.5, 11), (.5, True)])
def test_invalid_transaction_budget(rig, timeout, retries):
    with pytest.raises(ValueError):
        SessionController(rig[1], timeout_s=timeout, retries=retries)


def test_engineering_asynchronous_start_stop_and_heartbeat(rig):
    peer, tr, clock, ctl = rig
    assert ctl.settings.fs_allowed == (2, 4, 5, 8, 10)
    assert ctl.set_mode("ENGINEERING").ok and peer.mode == ctl.settings.mode == "ENGINEERING"
    assert ctl.set_fs(8).ok and peer.fs == 8
    assert ctl.start().ok
    sid = ctl.session_id
    assert sid and ctl.state_text == "STARTING"
    n = len(peer.requests)
    clock.now += .5
    ctl.tick()
    assert any(r.opcode == P.OP_HEARTBEAT and r.session_id == sid for _, r, _ in peer.requests[n:])
    assert not ctl.set_mode("ACQ").sent
    peer.complete_start()
    clock.now += .1
    ctl.tick()
    assert ctl.state_text == "STREAMING"
    assert ctl.stop().ok and ctl.state_text == "STOPPING" and ctl.session_id == sid
    assert ctl.last_end is None  # acceptance is not completed STOP
    clock.now += .5
    ctl.tick()
    assert ctl.session_id == sid  # keep heartbeat/lease through drain
    peer.complete_stop()
    clock.now += .1
    ctl.tick()  # stale session -> INVALID_STATE zero; next poll uses zero
    assert ctl.state_text == "STOPPED" and ctl.session_id == 0 and ctl.last_end == "HOST_STOP"
    clock.now += 2.1
    ctl.tick()
    assert ctl.state_text == "STOPPED" and peer.executed_starts == 1


@pytest.mark.parametrize("rate", [1, 3, 6, 7, 9, 11, True, 5.0])
def test_only_advertised_rates_send_packets(rig, rate):
    peer, _, _, ctl = rig
    before = len(peer.requests)
    assert ctl.set_fs(rate).status == "LOCAL_REJECT"
    assert len(peer.requests) == before


@pytest.mark.parametrize("caps,allowed", [
    ("fs_min=2;fs_max=10", ()), ("fs_allowed=", ()), ("fs_allowed=x,5", ()),
    ("fs_allowed=1,5", ()), ("fs_allowed=256", ()),
    ("fs_allowed=4,8", (4, 8)), ("fs_allowed=8,4,8", (4, 8)),
])
def test_capabilities_are_authoritative_without_range_fallback(rig, caps, allowed):
    peer, _, _, ctl = rig
    ctl.disconnect()
    peer.capability = caps
    assert ctl.connect().ok and ctl.settings.fs_allowed == allowed
    assert not ctl.set_fs(5).sent
    if allowed:
        assert ctl.set_fs(allowed[0]).ok


def test_current_limit_uses_advertised_maximum(rig):
    peer, _, _, ctl = rig
    n = len(peer.requests)
    assert not ctl.set_led_current(0, 36).sent
    assert len(peer.requests) == n
    assert ctl.set_led_current(0, 35).ok


def test_loss_retries_identical_bytes_without_repeating_start(rig):
    peer, _, _, ctl = rig
    dropped = []
    def lose_first(p, req, raw, reply, source):
        if req.opcode == P.OP_START and not dropped:
            dropped.append(raw)
            return []
        return [reply]
    peer.filter = lose_first
    assert ctl.start().ok
    starts = [raw for raw, req, _ in peer.requests if req.opcode == P.OP_START]
    assert starts == dropped * 2 and peer.executed_starts == 1
    assert ctl.counters["retries"] == 1


def rewrite(wire, offset, value, fmt):
    raw = bytearray(wire)
    struct.pack_into(fmt, raw, offset, value)
    raw[-4:] = struct.pack("<I", zlib.crc32(raw[:-4]))
    return bytes(raw)


@pytest.mark.parametrize("fault", ["crc", "length", "short", "oversize", "req_id", "opcode",
                                   "session", "echo", "flags", "msg_type", "state", "provenance"])
def test_bad_datagrams_cannot_match_control_transaction(rig, fault):
    peer, transport, _, ctl = rig
    assert ctl.start().ok
    def inject(p, req, raw, reply, source):
        if req.opcode != P.OP_HEARTBEAT:
            return [reply]
        bad = {
            "crc": lambda: reply[:-1] + bytes([reply[-1] ^ 1]),
            "length": lambda: rewrite(reply, 8, 1, "<H"),
            "short": lambda: reply[:20],
            "oversize": lambda: b"x" * 65500,
            "req_id": lambda: rewrite(reply, 28, req.req_id + 100, "<I"),
            "opcode": lambda: rewrite(reply, 32, P.OP_INFO, "<H"),
            "session": lambda: rewrite(reply, 12, p.session + 1, "<I"),
            "echo": lambda: rewrite(reply, 56, 123, "<Q"),
            "flags": lambda: rewrite(reply, 10, 4, "<H"),
            "msg_type": lambda: rewrite(reply, 5, 4, "<B"),
            "state": lambda: rewrite(reply, 36, 255, "<B"),
            "provenance": lambda: rewrite(reply, 39, 1, "<B"),
        }[fault]()
        return [bad, reply]
    peer.filter = inject
    res = ctl._run("heartbeat", P.OP_HEARTBEAT, P.args_host_time(1234))
    assert res.ok and res.reply.session_id == peer.session
    assert ctl.counters["retries"] == 0
    if fault == "oversize":
        assert transport.counters["oversize"] == 1
    else:
        assert sum(ctl.counters[k] for k in ("malformed", "unmatched", "wrong_session")) >= 1


def test_wrong_source_port_is_discarded_even_with_matching_reply(rig):
    peer, transport, _, ctl = rig
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
        foreign.bind(("127.0.0.1", 0))
        def inject(p, req, raw, reply, source):
            foreign.sendto(reply, source)
            return [reply]
        peer.filter = inject
        assert ctl.set_fs(4).ok
    assert transport.counters["wrong_source"] == 1


def test_duplicate_and_late_reply_do_not_change_state(rig):
    peer, _, _, ctl = rig
    peer.filter = lambda p, r, raw, reply, source: [reply, reply]
    assert ctl.set_fs(4).ok
    assert ctl.set_fs(8).ok
    # Drain the second reply; no old settings/state applied.
    ctl.pump()
    assert ctl.settings.fs_hz == 8 and ctl.counters["duplicate_replies"] >= 1


def test_timeout_is_bounded_and_late_start_cannot_acquire_lease(rig):
    peer, _, _, ctl = rig
    held = []
    def hold(p, req, raw, reply, source):
        if req.opcode == P.OP_START:
            held.append((reply, source))
            return []
        return [reply]
    peer.filter = hold
    before = time.monotonic()
    assert ctl.start().status == "TIMEOUT"
    assert time.monotonic() - before < 1
    assert ctl.session_id == 0 and peer.executed_starts == 1
    for raw, source in held:
        peer.sock.sendto(raw, source)
    assert ctl.refresh_status().ok
    assert ctl.state_text == "STARTING" and ctl.session_id == 0
    n = len(peer.requests)
    rig[2].now += .5
    ctl.tick()
    assert not any(r.opcode == P.OP_HEARTBEAT for _, r, _ in peer.requests[n:])
    assert not ctl.stop().sent


def test_second_endpoint_never_adopts_or_heartbeats_other_owner(rig):
    peer, _, _, owner = rig
    assert owner.start().ok
    clock = Clock()
    observer = SessionController(UDPTransport(*peer.endpoint), clock, timeout_s=.025, retries=0)
    try:
        assert observer.connect().ok
        assert observer.session_id == 0 and observer.state_text == "STARTING"
        assert not observer.start().sent and not observer.stop().sent
        clock.now += 2.1
        assert observer.refresh_status().status == "INVALID_STATE"
        observer.tick()
        assert observer.session_id == 0 and owner.session_id == peer.session
        source = observer._t.local_endpoint[1]
        assert not any(r.opcode == P.OP_HEARTBEAT and s[1] == source for _, r, s in peer.requests)
        assert observer.safe_off().ok  # explicit safety exception to peer lock
    finally:
        observer.disconnect()


def test_expired_owner_forgets_session_without_adopting_replacement(rig):
    peer, _, clock, ctl = rig
    assert ctl.start().ok
    peer.session += 1
    peer.owner = ("127.0.0.1", 1)
    clock.now += .5
    ctl.tick()
    assert ctl.session_id == 0 and ctl.last_end is None
    assert not ctl.stop().sent


def test_reboot_closes_socket_and_explicit_reconnect_never_starts(rig):
    peer, tr, _, ctl = rig
    assert ctl.start().ok
    peer.boot += 1
    peer.complete_stop()
    assert ctl.refresh_status().status == "TIMEOUT"
    assert not ctl.connected and ctl.session_id == 0 and tr.closed
    assert ctl.settings.fs_allowed == ()
    assert ctl.connect().ok and not tr.closed
    assert ctl.boot_id == peer.boot and ctl.state_text == "STOPPED"
    assert peer.executed_starts == 1 and ctl.session_id == 0


def test_stop_retry_after_asynchronous_completion_preserves_acceptance(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    dropped = []
    def lose_stop(p, req, raw, reply, source):
        if req.opcode == P.OP_STOP and not dropped:
            dropped.append(raw)
            p.complete_stop()
            return []
        return [reply]
    peer.filter = lose_stop
    assert ctl.stop().ok and ctl.state_text == "STOPPING"
    stops = [raw for raw, req, _ in peer.requests if req.opcode == P.OP_STOP]
    assert stops == dropped * 2
    assert ctl.refresh_status().status == "INVALID_STATE"
    assert ctl.session_id == 0 and ctl.last_end == "HOST_STOP"
    assert ctl.refresh_status().ok and ctl.state_text == "STOPPED"


def test_raw_interleaved_with_control_is_validated_and_routed_unchanged(rig):
    peer, _, _, ctl = rig
    assert ctl.set_mode("ENGINEERING").ok and ctl.start().ok
    seen, frames = [], []
    ctl.raw_sink, ctl.frame_sink = seen.append, frames.append
    good = build_envelope(build_raw(), session_id=peer.session)
    bad = build_envelope(build_raw(count=0), session_id=peer.session)
    foreign = build_envelope(build_raw(), session_id=peer.session + 1)
    peer.filter = lambda p, req, raw, reply, source: [foreign, bad, good, reply]
    assert ctl.get_time().ok
    assert seen == [good] and not frames
    assert ctl.counters["raw_routed"] == 1 and ctl.counters["malformed"] == 1
    assert ctl.counters["wrong_session"] == 1
    ctl.raw_sink = None
    assert ctl.get_time().ok and frames == [good]  # existing generic FRAME consumer


def test_stream_cannot_supply_reply_or_acquire_session(rig):
    peer, _, _, ctl = rig
    seen = []
    ctl.raw_sink = seen.append
    peer.filter = lambda p, req, raw, reply, source: [build_envelope(build_raw(), session_id=101)]
    assert ctl.get_time().status == "TIMEOUT" and not seen
    assert ctl.session_id == 0


def test_disconnect_idempotent_releases_socket_and_clears_capabilities(rig):
    peer, transport, clock, ctl = rig
    assert ctl.start().ok
    n = len(peer.requests)
    ctl.disconnect()
    ctl.disconnect()
    clock.now += 10
    ctl.tick()
    assert transport.closed and not ctl.connected and not ctl.info
    assert ctl.settings.fs_allowed == () and ctl.session_id == 0
    assert len(peer.requests) == n  # no implicit STOP or START
    assert ctl.connect().ok and ctl.session_id == 0
    assert peer.executed_starts == 1


def test_connect_timeout_closes_transport(rig):
    peer, tr, _, ctl = rig
    ctl.disconnect()
    peer.filter = lambda *args: []
    assert ctl.connect().status == "TIMEOUT"
    assert tr.closed and not ctl.connected and ctl.boot_id is None


def test_socket_failure_becomes_visible_link_error_and_cleans_up(rig):
    _, tr, _, ctl = rig
    tr.close()
    assert ctl.get_time().status == "IO_ERROR"
    assert not ctl.connected and tr.closed and ctl.session_id == 0
    assert ctl.counters["io_errors"] == 1
    assert any("transport error" in ev.text for ev in ctl.events)


def test_callback_cannot_start_overlapping_transaction(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    results = []
    ctl.raw_sink = lambda raw: results.append(ctl.set_fs(4))
    peer.filter = lambda p, r, raw, reply, source: [
        build_envelope(build_raw(), session_id=p.session), reply]
    assert ctl.get_time().ok
    assert len(results) == 1 and not results[0].sent


def test_invalid_mode_and_failed_precondition_leave_mode_unchanged(rig):
    peer, _, _, ctl = rig
    assert not ctl.set_mode("engineering").sent
    peer.filter = lambda p, req, raw, reply, source: [p.reply(req, 7)]
    assert ctl.set_mode("ENGINEERING").status == "PRECONDITION_FAILED"
    assert ctl.settings.mode == "ACQ"


def test_unsolicited_boot_id_does_not_reset_a_correlated_session(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    def inject(p, req, raw, reply, source):
        stale = rewrite(rewrite(reply, 28, req.req_id + 100, "<I"), 40, p.boot + 1, "<Q")
        return [stale, reply]
    peer.filter = inject
    assert ctl.get_time().ok
    assert ctl.connected and ctl.boot_id == peer.boot and ctl.session_id == peer.session


def test_zero_start_session_is_not_accepted(rig):
    peer, _, _, ctl = rig
    peer.filter = lambda p, r, raw, reply, source: [rewrite(reply, 12, 0, "<I")]
    assert ctl.start().status == "TIMEOUT" and ctl.session_id == 0
    # Explicit reconnect observes the running lease, without acquiring it.
    ctl.disconnect()
    peer.filter = lambda p, r, raw, reply, source: [reply]
    assert ctl.connect().ok and ctl.session_id == 0 and not ctl.stop().sent


def test_safe_off_ack_must_confirm_zero_session(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    peer.filter = lambda p, r, raw, reply, source: [rewrite(reply, 12, 999, "<I")]
    assert ctl.safe_off().status == "TIMEOUT"
    assert ctl.session_id != 999


def test_foreign_stop_ack_cannot_replace_held_session(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    sid = ctl.session_id
    peer.filter = lambda p, r, raw, reply, source: [rewrite(reply, 12, sid + 1, "<I")]
    assert ctl.stop().status == "TIMEOUT" and ctl.session_id == sid


def test_pending_stream_and_malformed_pump_never_mutate_state(rig):
    peer, tr, _, ctl = rig
    assert ctl.start().ok
    source = ("127.0.0.1", tr.local_endpoint[1])
    peer.sock.sendto(b"bad", source)
    peer.sock.sendto(build_envelope(build_raw(), session_id=peer.session + 1), source)
    # Use a round-trip fence; all earlier datagrams are handled before its ACK.
    assert ctl.get_time().ok
    assert ctl.counters["malformed"] >= 1 and ctl.counters["wrong_session"] >= 1
    assert ctl.device_state == P.ST_STARTING


def test_boot_change_during_connect_config_requires_fresh_reconnect(rig):
    peer, tr, _, ctl = rig
    ctl.disconnect()
    def reboot_between(p, req, raw, reply, source):
        if req.opcode == P.OP_GET_CONFIG:
            return [rewrite(reply, 40, p.boot + 1, "<Q")]
        return [reply]
    peer.filter = reboot_between
    assert ctl.connect().status == "TIMEOUT"
    assert not ctl.connected and not ctl.info and tr.closed


def test_status_after_read_error_closes_socket(rig):
    _, tr, _, ctl = rig
    tr.close()
    ctl.pump()
    assert not ctl.connected and ctl.counters["io_errors"] == 1


def test_receive_and_send_limits_fail_locally(rig):
    _, tr, _, _ = rig
    with pytest.raises(ValueError, match="MTU"):
        tr.send(b"x" * 1473)
    for timeout in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            tr.receive(timeout)


def test_transaction_deadline_is_not_reset_by_unrelated_packets(monkeypatch):
    from types import SimpleNamespace
    from noctinirs_host.control import session
    now = [0.0]
    class Flood:
        def __init__(self):
            self.sent, self.budgets = [], []
        def send(self, raw):
            self.sent.append(raw)
        def receive(self, timeout):
            self.budgets.append(timeout)
            now[0] += .2
            return [P.encode_reply(0xffffffff, P.OP_START, 0, state=P.ST_STARTING,
                                   fault=0, simulated=False, boot_id=1, t_rx_us=0, session_id=1)]
    tr = Flood()
    monkeypatch.setattr(session, "time", SimpleNamespace(monotonic=lambda: now[0]))
    ctl = SessionController(tr, retries=1, timeout_s=.5)
    assert ctl.get_time().status == "TIMEOUT"
    assert len(tr.sent) == 2 and tr.sent[0] == tr.sent[1]
    assert tr.budgets == pytest.approx([.5, .3, .1] * 2)


def test_request_id_wrap_keeps_retries_identical(rig):
    peer, _, _, ctl = rig
    ctl._next_id = 0xffffffff
    assert ctl.set_fs(4).ok and ctl.set_fs(8).ok
    changes = [r for _, r, _ in peer.requests if r.opcode == P.OP_SET_FS]
    assert [r.req_id for r in changes] == [0xffffffff, 1]


def test_heartbeat_not_sent_before_period(rig):
    peer, _, clock, ctl = rig
    assert ctl.start().ok
    clock.now += .49
    ctl.tick()
    assert not any(r.opcode == P.OP_HEARTBEAT for _, r, _ in peer.requests)
    clock.now += .011
    ctl.tick()
    assert sum(r.opcode == P.OP_HEARTBEAT for _, r, _ in peer.requests) == 1


def test_stop_during_starting_is_sent_with_owned_session(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok and ctl.state_text == "STARTING"
    sid = ctl.session_id
    assert ctl.stop().ok and ctl.state_text == "STOPPING"
    assert peer.requests[-1][1].session_id == sid
    assert not ctl.start().sent and not ctl.set_fs(4).sent


def test_control_poll_and_raw_sink_handle_duplicate_raw_without_rewriting(rig):
    peer, tr, _, ctl = rig
    assert ctl.set_mode("ENGINEERING").ok and ctl.start().ok
    seen = []
    ctl.raw_sink = seen.append
    raw = build_envelope(build_raw(), session_id=peer.session)
    source = ("127.0.0.1", tr.local_endpoint[1])
    peer.sock.sendto(raw, source)
    peer.sock.sendto(raw, source)
    assert ctl.get_time().ok
    assert seen == [raw, raw]  # transport does not invent a RAW ordering policy


def test_one_outstanding_request_guard_on_stream_callback(rig):
    peer, _, _, ctl = rig
    assert ctl.start().ok
    results = []
    ctl.raw_sink = lambda raw: results.append(ctl.get_time())
    peer.filter = lambda p, r, raw, reply, source: [
        build_envelope(build_raw(), session_id=p.session), reply]
    assert ctl.get_time().ok
    assert len(results) == 1 and not results[0].sent and results[0].status == "LOCAL_REJECT"


def test_safe_off_during_pending_stop_keeps_its_end_reason(rig):
    _, _, _, ctl = rig
    assert ctl.start().ok and ctl.stop().ok
    assert ctl.safe_off().ok and ctl.last_end == "SAFE_OFF"
    assert ctl.clear_fault().ok and ctl.refresh_status().ok
    assert ctl.last_end == "SAFE_OFF"
