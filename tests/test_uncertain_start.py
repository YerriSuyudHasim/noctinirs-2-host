"""Deterministic control subset model of bd880f5 ownership, grace and cached ACKs."""

from collections import deque
import struct
from types import SimpleNamespace

import pytest

from helpers import build_envelope, build_raw
from noctinirs_host.control import protocol as p
from noctinirs_host.control import session
from noctinirs_host.control.session import SessionController


class LeasePeer:
    def __init__(self):
        self.now_us = 100_000_000
        self.boot = 42
        self.owner = None
        self.state, self.session = p.ST_STOPPED, 0
        self.anchor = self.starts = self.expirations = 0
        self.cache = deque(maxlen=4)
        self.mutation_ids = {}
        self.trace = []
        self.drop = set()
        self.fail_safe_off = False

    def clock(self):
        return self.now_us / 1_000_000

    def advance(self, seconds):
        self.now_us += round(seconds * 1_000_000)
        self.tick()

    def end(self, state):
        self.state, self.session, self.owner = state, 0, None
        self.cache.clear()

    def reboot(self):
        self.end(p.ST_STOPPED)
        self.mutation_ids.clear()
        self.boot += 1

    def tick(self):
        if self.owner is not None and self.now_us >= self.anchor + 1_500_000 + 3_000_000:
            self.expirations += 1
            self.end(p.ST_STOPPED)

    def receive(self, source, wire):
        self.tick()
        req = p.decode_request(wire)
        op = req.opcode
        self.trace.append((self.now_us, source, op, wire))
        conflict = False
        cacheable = op in p.MUTATING_OPS
        for endpoint, previous, reply in self.cache:
            if cacheable and (source, req.req_id) == (endpoint, previous.req_id):
                if (op, req.args, req.session_id) != (
                        previous.opcode, previous.args, previous.session_id):
                    conflict = True
                    break
                ack = p.decode_reply(reply)
                if self.owner == source and ack.ok and ack.session_id == self.session:
                    self.anchor = self.now_us
                return reply

        status, extension = p.STATUS_OK, b""
        unlocked = (p.OP_INFO, p.OP_GET_CONFIG, p.OP_GET_TIME, p.OP_SAFE_OFF)
        if conflict:
            status = p.STATUS_INVALID_ARG
        elif ((self.owner is not None and self.owner != source and op not in unlocked)
                or (op != p.OP_SAFE_OFF and req.session_id not in (0, self.session))):
            status = p.STATUS_INVALID_STATE
        elif cacheable and req.req_id <= self.mutation_ids.get(source, -1):
            status = p.STATUS_INVALID_ARG
        elif op == p.OP_INFO:
            extension = b"Lease fake;frames=RAW_ENGINEERING;fs_allowed=5;mode=ENGINEERING"
        elif op == p.OP_GET_CONFIG:
            extension = struct.pack("<BB20H", 5, 20, *([30] * 20))
        elif op in p.TIMED_OPS:
            extension = req.args + struct.pack("<QQ", self.now_us, self.now_us)
            if op == p.OP_STATUS:
                grace = self.owner is not None and self.now_us >= self.anchor + 1_500_000
                extension += struct.pack("<5IB", len(self.trace), 0, self.expirations, 0, 0, grace)
        elif op == p.OP_START:
            if self.state != p.ST_STOPPED:
                status = p.STATUS_INVALID_STATE
            else:
                self.starts += 1
                self.owner, self.session, self.state = source, 100 + self.starts, p.ST_STARTING
        elif op == p.OP_STOP:
            self.end(p.ST_STOPPED)
        elif op == p.OP_SAFE_OFF:
            if self.fail_safe_off:
                status = p.STATUS_NAMES.index("HW_FAULT")
            else:
                self.end(p.ST_SAFE_OFF)
        else:
            raise AssertionError(f"Unhandled model opcode {op}")
        if status == p.STATUS_OK and source == self.owner:
            self.anchor = self.now_us
        reply = p.encode_reply(req.req_id, op, status, state=self.state, fault=0, simulated=True,
                               boot_id=self.boot, t_rx_us=self.now_us, session_id=self.session,
                               extension=extension)
        if cacheable and status == p.STATUS_OK:
            self.mutation_ids[source] = req.req_id
            self.cache.append((source, req, reply))
        return reply


class Link:
    def __init__(self, peer, source="A"):
        self.peer, self.source = peer, source
        self.opened = False
        self.inbox = deque()

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False
        self.inbox.clear()

    def send(self, wire):
        assert self.opened
        reply = self.peer.receive(self.source, wire)
        if p.decode_request(wire).opcode not in self.peer.drop:
            self.inbox.append(reply)

    def receive(self, _timeout):
        return [self.inbox.popleft()] if self.inbox else []


@pytest.fixture
def rig():
    peer = LeasePeer()
    link = Link(peer)
    ctl = SessionController(link, peer.clock)
    assert ctl.connect().ok
    return peer, link, ctl


def test_all_start_replies_lost_allow_lease_expiry_without_status(rig):
    peer, _, ctl = rig
    peer.drop.add(p.OP_START)
    assert ctl.start().status == "TIMEOUT"
    assert peer.starts == 1 and ctl.session_id == 0
    starts = [wire for _, _, op, wire in peer.trace if op == p.OP_START]
    assert len(starts) == 3 and len(set(starts)) == 1
    after_start = len(peer.trace)
    for _ in range(5):
        peer.advance(2)
        ctl.tick()
    assert peer.state == p.ST_STOPPED and peer.expirations == 1, (
        f"owner={peer.owner}, state={p.state_name(peer.state)}, expirations={peer.expirations}, "
        f"post-timeout traffic={[(t, p.OPCODE_NAMES[op]) for t, _, op, _ in peer.trace[after_start:]]}")
    assert peer.trace[after_start:] == []
    assert ctl.start_uncertain and ctl.state_text == "STOPPED"  # stale pre-START report
    assert ctl.check_start_outcome().ok and not ctl.start_uncertain
    assert ctl.session_id == 0


def lose_start(peer, ctl):
    peer.drop.add(p.OP_START)
    assert ctl.start().status == "TIMEOUT"
    assert ctl.start_uncertain


def test_late_ack_and_raw_cannot_grant_ownership(rig):
    peer, link, ctl = rig
    lose_start(peer, ctl)
    seen = []
    ctl.raw_capture_sink = lambda *args: seen.append(args)
    ack = peer.cache[-1][-1]
    link.inbox.extend([ack, build_envelope(build_raw(), session_id=peer.session, flags=1)])
    ctl.tick()
    assert ctl.session_id == 0 and ctl.start_uncertain
    assert ctl.counters["unmatched"] == ctl.counters["wrong_session"] == 1
    assert not seen
    assert not ctl.start().sent
    assert not ctl.refresh_info().sent
    assert not ctl.refresh_status().sent
    assert not ctl.stop().sent
    assert not ctl.check_start_outcome().sent


def test_timed_out_probe_is_rate_limited_and_stays_unresolved(rig):
    peer, _, ctl = rig
    lose_start(peer, ctl)
    peer.advance(4.5)
    peer.drop.add(p.OP_STATUS)
    before = len(peer.trace)
    assert ctl.check_start_outcome().status == "TIMEOUT"
    assert ctl.start_uncertain and ctl.start_recheck_wait_s == 4.5
    ctl.tick()
    assert not ctl.check_start_outcome().sent
    assert [op for _, _, op, _ in peer.trace[before:]] == [p.OP_STATUS]
    peer.advance(4.5)
    peer.drop.remove(p.OP_STATUS)
    assert ctl.check_start_outcome().ok


@pytest.mark.parametrize("lost_ack", [False, True])
def test_safe_off_is_explicit_without_ownership_and_requires_ack(rig, lost_ack):
    peer, link, ctl = rig
    lose_start(peer, ctl)
    if lost_ack:
        peer.drop.add(p.OP_SAFE_OFF)
    before = len(peer.trace)
    result = ctl.safe_off()
    assert all(p.decode_request(wire).session_id == 0 for _, _, _, wire in peer.trace[before:])
    assert peer.state == p.ST_SAFE_OFF
    assert result.ok is not lost_ack
    assert ctl.start_uncertain is lost_ack
    if lost_ack:
        ctl.disconnect()
        assert not link.opened and not ctl.connect().sent
        peer.drop.remove(p.OP_SAFE_OFF)
        assert ctl.safe_off().ok and not link.opened and not ctl.start_uncertain
    assert not any(op == p.OP_CLEAR_FAULT for _, _, op, _ in peer.trace)


def test_safe_off_firmware_failure_retains_uncertainty(rig):
    peer, _, ctl = rig
    lose_start(peer, ctl)
    peer.fail_safe_off = True
    assert ctl.safe_off().status == "HW_FAULT"
    assert ctl.start_uncertain and peer.owner == "A"


def test_reboot_requires_a_fresh_correlated_report(rig):
    peer, link, ctl = rig
    lose_start(peer, ctl)
    peer.reboot()
    peer.advance(4.5)
    assert ctl.check_start_outcome().status == "TIMEOUT"  # B rejects boot change
    assert ctl.start_uncertain and not ctl.connected and not link.opened
    peer.advance(4.5)
    result = ctl.check_start_outcome()
    assert result.ok and result.reply.boot_id == peer.boot
    assert not ctl.start_uncertain and not link.opened and ctl.session_id == 0


def test_recheck_does_not_adopt_other_owner(rig):
    peer, _, ctl = rig
    lose_start(peer, ctl)
    peer.advance(4.5)
    peer.drop.clear()
    other = SessionController(Link(peer, "B"), peer.clock)
    assert other.connect().ok and other.start().ok
    anchor = peer.anchor
    peer.advance(0.1)
    result = ctl.check_start_outcome()
    assert not result.ok and result.reply.session_id == other.session_id
    assert ctl.start_uncertain and ctl.session_id == 0 and not ctl.stop().sent
    assert peer.anchor == anchor and peer.owner == "B"
    assert other.stop().ok


def test_in_flight_cached_ack_normal_heartbeat_and_stop(rig):
    peer, link, ctl = rig
    send = link.send

    def lose_one(wire):
        send(wire)
        if p.decode_request(wire).opcode == p.OP_START:
            link.send = send
            link.inbox.clear()

    link.send = lose_one
    assert ctl.start().ok and not ctl.start_uncertain
    starts = [wire for _, _, op, wire in peer.trace if op == p.OP_START]
    assert len(starts) == 2 and starts[0] == starts[1] and peer.starts == 1
    peer.state = p.ST_STREAMING
    peer.advance(0.5)
    ctl.tick()
    assert peer.trace[-1][2] == p.OP_HEARTBEAT
    assert ctl.stop().ok and ctl.state_text == "STOPPED" and ctl.session_id == 0


def test_ack_delivered_after_original_start_window_is_discarded(rig, monkeypatch):
    peer, link, ctl = rig
    monkeypatch.setattr(session, "time", SimpleNamespace(monotonic=peer.clock))
    ctl.timeout_s = 30
    receive = link.receive

    def delayed_ack(budget):
        assert budget == pytest.approx(1.5)
        peer.advance(1.501)
        return receive(budget)

    link.receive = delayed_ack
    assert ctl.start().status == "TIMEOUT"
    assert ctl.start_uncertain and ctl.session_id == 0 and peer.starts == 1
    assert [op for _, _, op, _ in peer.trace].count(p.OP_START) == 1
