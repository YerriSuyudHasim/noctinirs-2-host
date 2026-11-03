"""S2 core: simulator frames, receiver counters, channel map, gaps. Simulation evidence only."""

from pathlib import Path

import pytest

from noctinirs_host.control.protocol import OP_START
from noctinirs_host.control.session import LoopbackTransport, SessionController
from noctinirs_host.control.simulator import DeviceSimulator
from noctinirs_host.geometry import load_geometry
from noctinirs_host.live.model import GAP, GOOD, MARGINAL, WAVELENGTHS, build_channels
from noctinirs_host.live.receiver import FrameReceiver
from noctinirs_host.live.synth import SlotSpec, SignalModel, build_opt2_payload
from noctinirs_host.control.protocol import encode_frame
from noctinirs_host.wire import optical as opt

GEO = load_geometry(Path(__file__).resolve().parents[1] / "config" / "optical_geometry_v1.json")


class Rig:
    def __init__(self, **sim_kw):
        self.t = [0.0]
        self.sim = DeviceSimulator(lambda: self.t[0], geometry=GEO, **sim_kw)
        self.tr = LoopbackTransport(self.sim)
        self.ctl = SessionController(self.tr, lambda: self.t[0])
        self.rx = FrameReceiver(GEO)
        self.ctl.frame_sink = self.rx.feed
        assert self.ctl.connect().ok

    def run(self, seconds, step=0.05):
        for _ in range(round(seconds / step)):
            self.t[0] += step
            self.ctl.tick()


def test_channel_map_follows_the_geometry_file():
    ch = build_channels(GEO)
    assert len(ch) == len(GEO.channels) == 20
    assert [c.name for c in ch] == [c.name for c in GEO.channels]
    for c in ch:
        for slot in c.slots:
            assert GEO.channel_for(slot, c.ain).name == c.name
    assert sum(c.kind == "short" for c in ch) == 4


def test_no_frames_unless_streaming():
    r = Rig()
    r.run(2.0)
    assert r.sim.frames_sent == 0 and r.rx.counters["frames"] == 0
    assert r.ctl.start().ok
    r.run(1.1)  # small margin: float time steps must not decide the count
    assert r.rx.counters["frames"] == 5  # Fs default 5 Hz
    assert r.ctl.stop().ok
    n = r.sim.frames_sent
    r.run(2.0)
    assert r.sim.frames_sent == n


@pytest.mark.parametrize("fs", [2, 5, 10])
def test_frame_rate_follows_fs(fs):
    r = Rig()
    assert r.ctl.set_fs(fs).ok and r.ctl.start().ok
    r.run(2.0)
    assert r.rx.counters["frames"] == 2 * fs
    assert r.rx.counters["lost"] == r.rx.counters["duplicate"] == r.rx.counters["rejected"] == 0


def test_every_simulated_frame_passes_the_strict_parser():
    r = Rig()
    assert r.ctl.start().ok
    r.run(1.0)
    assert r.rx.rejects.frames_ok == r.rx.counters["frames"] > 0
    assert r.rx.simulated is True
    for b in r.rx.buffers:
        assert b.latest_tier() == GOOD


def test_dead_led_slot_is_a_gap_on_that_wavelength_only():
    r = Rig()
    ch = r.rx.channels[0]
    r.ctl.set_led_current(ch.slots[1], 0)  # 850 nm LED of the first channel
    assert r.ctl.start().ok
    r.run(1.0)
    buf = r.rx.buffers[0]
    assert all(v is None for v in buf.series("850")[1])
    assert all(v is not None for v in buf.series("735")[1])
    assert buf.latest_tier() == GAP
    # a channel on a different source is untouched
    other = next(c for c in r.rx.channels if ch.slots[1] not in c.slots)
    assert r.rx.buffers[other.index].latest_tier() == GOOD


def test_dead_detector_hits_only_channels_on_that_detector():
    r = Rig()
    r.sim.dead_detectors = {3}
    assert r.ctl.start().ok
    r.run(1.0)
    for c, b in zip(r.rx.channels, r.rx.buffers):
        assert (b.latest_tier() == GAP) == (c.ain == 3)


def test_warmup_frames_never_produce_values():
    r = Rig()
    r.sim.warmup_frames = 3
    assert r.ctl.start().ok
    r.run(1.0)  # 5 frames: 3 warm-up, 2 science
    assert r.rx.counters["non_science"] == 3
    ys = r.rx.buffers[0].series("735")[1]
    assert ys[:3] == [None] * 3 and all(v is not None for v in ys[3:])


def test_lost_frames_are_counted_and_stay_visible_as_a_gap():
    r = Rig()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(0.5)
    r.tr.drop_next_frames = 2
    r.run(1.0)
    assert r.rx.counters["lost"] == 2
    xs, ys = r.rx.buffers[0].series("735")
    assert ys.count(None) == 1  # one explicit break, no invented values
    assert len(xs) == len(ys)


def test_duplicate_frame_counted_once_and_not_plotted_twice():
    r = Rig()
    assert r.ctl.start().ok
    r.run(0.5)
    r.tr.duplicate_next_frame = True
    r.run(0.5)
    assert r.rx.counters["duplicate"] == 1
    assert len(r.rx.buffers[0]) == r.rx.counters["frames"]  # the copy was not plotted


def test_reordered_frames_are_recognised_as_late_not_lost():
    r = Rig()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(0.3)
    r.tr.reorder_next_frames = True
    r.run(1.0)
    c = r.rx.counters
    assert c["late_recovered"] == 1 and c["lost"] == 0 and c["duplicate"] == 0


def test_new_session_clears_the_plots():
    r = Rig()
    assert r.ctl.start().ok
    r.run(1.0)
    assert len(r.rx.buffers[0]) > 0
    assert r.ctl.stop().ok
    assert r.ctl.start().ok
    r.run(0.25)
    assert len(r.rx.buffers[0]) == 1
    assert r.rx.last_seq == 0


def test_geometry_map_id_mismatch_shows_nothing():
    r = Rig()
    r.sim.geometry_map_id = GEO.geometry_map_id + 1
    assert r.ctl.start().ok
    r.run(1.1)
    assert r.rx.counters["geometry_mismatch"] == 5 and r.rx.counters["frames"] == 0
    assert len(r.rx.buffers[0]) == 0


def test_geometry_mismatch_on_a_new_session_clears_the_old_values():
    # PR31-F1: stale science data must not survive a new, incompatible session
    r = Rig()
    assert r.ctl.start().ok
    r.run(1.1)
    assert len(r.rx.buffers[0]) == 5 and r.rx.last_seq is not None
    assert r.ctl.stop().ok
    r.sim.geometry_map_id = GEO.geometry_map_id + 1
    assert r.ctl.start().ok
    r.run(1.1)
    assert all(len(b) == 0 for b in r.rx.buffers)
    assert all(b.latest_tier() == GAP for b in r.rx.buffers)
    assert r.rx.session_id == r.sim.session_id
    # per-session counters restart (documented policy); the mismatches of THIS session are counted
    assert r.rx.counters["frames"] == 0 and r.rx.counters["geometry_mismatch"] == 5


def test_rejected_first_frame_of_a_new_session_also_clears_the_old_values():
    r = Rig()
    assert r.ctl.start().ok
    r.run(1.1)
    assert len(r.rx.buffers[0]) == 5
    bad = encode_frame(b"\x01" * 10, simulated=True, session_id=r.sim.session_id + 7,
                       sequence=1, timestamp_us=0)
    assert r.rx.feed(bad) == "rejected"
    assert all(len(b) == 0 for b in r.rx.buffers)
    assert r.rx.summary()["reject_total"] >= 1  # reject counters stay cumulative


def test_garbage_and_non_frames_do_not_crash():
    rx = FrameReceiver(GEO)
    assert rx.feed(b"\x00" * 40) == "rejected"
    assert rx.feed(b"") == "rejected"
    bad = encode_frame(b"\x01" * 10, simulated=True, session_id=1, sequence=1, timestamp_us=0)
    assert rx.feed(bad) == "rejected"
    assert rx.summary()["reject_total"] >= 2 and rx.counters["frames"] == 0


def test_frames_without_a_sink_are_counted_not_lost_silently():
    r = Rig()
    r.ctl.frame_sink = None
    assert r.ctl.start().ok
    r.run(1.1)
    assert r.ctl.counters["frames_unrouted"] == 5 and r.ctl.counters["unmatched"] == 0


def test_frames_do_not_disturb_command_replies():
    r = Rig()
    assert r.ctl.set_fs(10).ok and r.ctl.start().ok
    r.run(0.5)
    assert r.ctl.refresh_status().ok and r.ctl.safe_off().ok
    assert r.ctl.counters["unmatched"] == 0 and r.ctl.counters["malformed"] == 0


def test_marginal_when_quality_is_only_minimum():
    sp = SignalModel(GEO).slot_spec(0, 0.0, 30, frozenset())
    specs = [sp] * 20
    specs[0] = SlotSpec(opt.Q_VALID_MINIMUM, 0, sp.mask, sp.witness, sp.dets)
    payload = build_opt2_payload(specs, frame_sequence=0, first_sample=0)
    rx = FrameReceiver(GEO)
    rx.feed(encode_frame(payload, simulated=True, session_id=1, sequence=1, timestamp_us=0))
    c0 = next(c for c in rx.channels if 0 in c.slots)
    assert rx.buffers[c0.index].points(WAVELENGTHS[0] if c0.slots[0] == 0 else WAVELENGTHS[1])[0].tier == MARGINAL


def test_signal_model_is_deterministic_and_scales_with_current():
    a = SignalModel(GEO, seed=3).slot_spec(2, 1.0, 30, frozenset())
    b = SignalModel(GEO, seed=3).slot_spec(2, 1.0, 30, frozenset())
    hi = SignalModel(GEO, seed=3).slot_spec(2, 1.0, 50, frozenset())
    assert a == b and hi.dets[0].sum_on > a.dets[0].sum_on
