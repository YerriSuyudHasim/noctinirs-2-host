"""R3 host/unit and simulation checks. No device or network access."""
from dataclasses import replace
from hashlib import sha256
from math import sin, pi
import json
from types import SimpleNamespace

import pytest

from noctinirs_host.preview.jitter import PreviewJitterBuffer
from noctinirs_host.preview.filters import FilterConfig, design, response
from noctinirs_host.preview.slots import SlotAssembler
from noctinirs_host.calibration import Assumptions, Level, empirical_choice, level_exclusions
from noctinirs_host.calibration_sweep import Visit, CalibrationProfile, levels_for_slot, save_sweep, spectral_noise
from noctinirs_host.control.diagnostics import counters, MessageCoverage, loss_summary
from noctinirs_host.control import protocol as p
from noctinirs_host.record.raw_archive import RawReplay, decode_raw
from noctinirs_host.geometry import load_geometry
from noctinirs_host.control.engineering_fake import EngineeringFake
from test_calibration import run_fake, complete_sweep
from test_engineering_run import GEOMETRY, poll


def packet(seq, count=40, gap=False, epoch=1):
    return SimpleNamespace(first_sample_sequence=seq, sample_count=count, gap_before=gap, slot_plan_epoch=epoch)


def test_jitter_reorders_without_changing_packets_and_flushes():
    b = PreviewJitterBuffer()
    packets = [packet(i) for i in (0,80,40)]
    for i, raw in enumerate(packets):
        assert b.push(raw, i*1000, i) == []
    result = b.drain(3_003_000)
    assert [r[0] for r in result] == [packets[0],packets[2],packets[1]]
    assert b.counts['missing_samples'] == 0
    assert b.push(packets[0], 4_000_000) == []
    assert b.counts['late'] == 1


def test_jitter_wrap_duplicates_gap_observer_and_capacity():
    gaps=[]
    b=PreviewJitterBuffer(2, SimpleNamespace(missing=gaps.append), max_packets=2)
    b.push(packet(0xfffffff0,16),0)
    b.push(packet(40),100)
    b.push(packet(40),101)
    b.push(packet(80),200)
    out=b.drain(0,flush=True)
    assert b.counts['duplicate']==1 and b.counts['overflow']==1
    assert gaps[0].first_sequence==0 and gaps[0].count==40
    assert len(out)==2
    with pytest.raises(ValueError): PreviewJitterBuffer(1)


def test_cardiac_band_survives_effective_filter_settings():
    cfg=FilterConfig(bandpass=True,band_high=.13,lowpass=True,low_cutoff=.58,detrend=True).cardiac_safe(10)
    for hz in (1.5,1.6,1.8):
        assert abs(response(design(cfg,10),hz,10)) > .95


def test_noise_estimator_excludes_pulse_bin():
    noise=[sin(2*pi*4*i/10) for i in range(100)]
    pulse=[n+100*sin(2*pi*1.5*i/10) for i,n in enumerate(noise)]
    assert spectral_noise(pulse)==pytest.approx(spectral_noise(noise), rel=1e-10)


def test_stable_three_repeats_allow_high_code_but_not_clipping():
    params=Assumptions()
    level=Level(177,(1000,),(10,),(2000,),(0,),3,72,repeat_relative_spread=(.05,))
    assert not level_exclusions(level,params)
    assert 'TIER2_ENVELOPE_INSUFFICIENT' in level_exclusions(replace(level,visits=2),params)
    assert 'TIER2_ENVELOPE_INSUFFICIENT' in level_exclusions(replace(level,repeat_relative_spread=(.11,)),params)
    assert 'CLIP_DETECTED' in level_exclusions(replace(level,peak=(params.headroom,)),params)


def test_dead_detector_is_flagged_and_fixed_live_target_scores():
    levels=[Level(67,(1000,1),(10,10),(2000,2000),(0,0),2,62)]
    choice=empirical_choice(levels,(0,1),133,10,Assumptions())
    assert choice.code==67 and choice.limiting_detector==0
    assert 'LOW_QUALITY_DETECTOR' in choice.flags
    weak=replace(levels[0],mean=(1,1))
    assert empirical_choice([weak],(0,1),32,10,Assumptions(),short=True).code is None


def test_board1_safety_exclusion_is_short_only():
    geo=load_geometry('config/optical_geometry_board1.json')
    default=load_geometry(GEOMETRY)
    for slot in range(20):
        target=geo.calibration_targets(slot)
        short=any(geo.channel_for(slot,i).kind=='short' for i in target)
        assert len(target)==(1 if short else 2)
        assert len(geo.calibration_safety_targets(slot))==(3 if short else 4)
        assert default.calibration_safety_targets(slot)==(0,1,2,3)


@pytest.mark.parametrize('r3',[False,True])
def test_r2_r3_netdiag_wire_and_unknown_counters(r3):
    ext=b'v4netdiag=1 heap_free_now=13000'
    if r3: ext+=b' raw_tx_failed_attempts=7 raw_tx_retried_ok=3 raw_tx_abandoned_packets=2 raw_tx_abandoned_samples=80 frame_send_enomem=4'
    wire=p.encode_reply(1,p.OP_GET_NETDIAG,0,state=p.ST_STOPPED,fault=0,simulated=True,
                        boot_id=9,t_rx_us=0,extension=ext)
    fields=p.decode_reply(wire).info
    assert counters(fields)['raw_tx_abandoned_samples']==(80 if r3 else None)
    assert counters({'raw_tx_abandoned_samples':'-1'})['raw_tx_abandoned_samples'] is None


def test_message_loss_counts_replies_and_recovers_reordering():
    coverage=MessageCoverage()
    for seq in (0xfffffffe,0,0xffffffff,1,3):coverage.observe(seq)
    assert coverage.snapshot()['missing']==1
    before=dict(boot_id=9)
    after=dict(boot_id=9,observed_info=dict(fields=dict(raw_tx_policy='hold_retry')),
               firmware_raw_diagnostics=dict(raw_tx_abandoned_samples=80,raw_tx_abandoned_packets=2))
    summary=loss_summary(before,after,coverage.snapshot())
    assert summary['air_or_host_message_gaps']==1 and summary['device_abandoned_samples']==80
    assert loss_summary(before,{},coverage.snapshot())['device_abandoned_samples'] is None


def test_sweep_archive_exact_datagrams_and_index(tmp_path):
    geo=load_geometry(GEOMETRY)
    fake=EngineeringFake(geometry=geo)
    fake.fs_hz=10
    profile=CalibrationProfile.create((67,)*20,{})
    visit=Visit(profile,31,geo.geometry_map_id,boot_id=9)
    wires=list(fake._cycle_packets(0,0))
    for i,wire in enumerate(wires):
        raw=decode_raw(wire,i*1000,9).raw
        visit.feed(raw,wire,i*1000)
    result=save_sweep(tmp_path,'record_1',[visit])
    replay=RawReplay(tmp_path/result['archive'])
    assert [r.wire for r in replay]==wires
    assert replay.archive_problem is None and replay.meta_problem is None
    index=json.loads((tmp_path/result['index']).read_text())['visits'][0]
    assert index['raw_sha256']==sha256(b''.join(wires)).hexdigest()==visit.hash.hexdigest()
    assert index['packet_count']==len(wires)


def test_recording_persists_sweep_and_netdiag_unavailable_on_old_fake(tmp_path):
    run,clock=run_fake()
    try:
        complete_sweep(run,clock)
        run.start_recorded(str(tmp_path))
        poll(run,lambda:run.recording_used)
        for _ in range(25):clock.now+=.05;run.tick()
        run.stop()
        poll(run,lambda:run.recorder_state=='FINALIZED')
        meta=json.loads((tmp_path/'record_1.json').read_text())['raw']
        assert 'netdiag' in meta['context_at_start'] and 'netdiag' in meta['context_at_end']
        assert meta['context_at_end']['loss_summary']['device_abandoned_samples'] is None
        assert (tmp_path/'record_1.sweep.raw.bin').stat().st_size>8
    finally:
        run.shutdown();poll(run,lambda:run.finished)


def test_small_gap_uses_valid_rows_and_drift_compares_noise():
    from noctinirs_host.preview.slots import SlotMeasurement
    params=Assumptions()
    def visit(mean,noise,count=24):
        rows=[]
        for j in range(count):
            value=mean+noise*(-1 if j%2 else 1)
            row=SlotMeasurement(0,1,j*800,j*100000,on_n=1,off_n=1)
            row.on_sum=[1000+value]*4;row.off_sum=[1000]*4
            rows.append(row)
        return SimpleNamespace(profile=SimpleNamespace(codes=(67,)*20),rows=[rows]*20,
            flags={'INCOMPLETE_DATA'},count=31,fs=10,maximum=[[2000]*4]*20,minimum=[[0]*4]*20)
    visits=[visit(100,50),visit(110,50)]
    level=levels_for_slot(visits,0,(67,)*20,(0,),params)[0]
    assert level.visits==2 and level.count==48
    assert not any('REFERENCE_DRIFT_SLOT_0' in v.flags for v in visits)
    changed=[visit(100,1),visit(150,1)]
    levels_for_slot(changed,0,(67,)*20,(0,),params)
    assert 'REFERENCE_DRIFT_SLOT_0' in changed[1].flags
    assert levels_for_slot([visit(100,1,23)],0,(67,)*20,(0,),params)[0].visits==0


def test_off_preset_counts_only_reported_stable_positions():
    from noctinirs_host.wire.optical import RawPacket,RawSample
    from noctinirs_host.preview.slots import PH_OFF_STABLE
    samples=tuple(RawSample(i,(i,)*4,PH_OFF_STABLE if 23<=i<=38 else 2,0) for i in range(40))
    second=(RawSample(40,(0,)*4,2,1),)
    packets=[RawPacket(1,0,40,False,1,samples),RawPacket(1,40,1,False,1,second)]
    for preset,expected in [('reported',16),('29-38',10)]:
        assembler=SlotAssembler(preset)
        assembler._fresh=False
        rows=[]
        for pk in packets:rows+=assembler.feed(pk,0)
        # The initial epoch boundary is deliberately partial; start a known clean occurrence.
        assembler=SlotAssembler(preset);assembler._epoch=1;assembler._fresh=False
        rows=[]
        for pk in packets:rows+=assembler.feed(pk,0)
        assert rows[0].off_n==expected


def test_r3_netdiag_snapshot_retains_counters_without_unrelated_fields():
    run,clock=run_fake()
    try:
        device=run.controller._t.simulator
        execute=device._execute
        def dispatch(req,op,args):
            if op==p.OP_GET_NETDIAG:
                return device._reply(req.req_id,op,0,extension=(
                    b'v4netdiag=1 raw_tx_policy=hold_retry raw_tx_failed_attempts=9 '
                    b'raw_tx_retried_ok=4 raw_tx_abandoned_packets=2 raw_tx_abandoned_samples=80 '
                    b'frame_send_enomem=6 unrelated=private-placeholder')),False
            return execute(req,op,args)
        device._execute=dispatch
        snapshot=run._netdiag_snapshot()
        assert snapshot['available']
        assert snapshot['counters']['raw_tx_retried_ok']==4
        assert snapshot['counters']['frame_send_enomem']==6
        assert 'unrelated' not in snapshot['fields']
    finally:
        run.shutdown();poll(run,lambda:run.finished)
