"""Offline R3 evidence. Run separately with baseline and candidate source paths.

Usage: python tools/replay_host_r3.py --source SRC --data DIR --output DIR --variant old|new
Only derived outputs are written. No transport/session is opened.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from dataclasses import replace

parser=argparse.ArgumentParser()
for name in ('source','data','output','variant'):parser.add_argument('--'+name,required=True)
a=parser.parse_args()
sys.path.insert(0,str(Path(a.source).resolve()))
import numpy as np
from noctinirs_host.record.raw_archive import RawReplay,RawArchive,MAGIC
from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.pipeline import PreviewPipeline
from noctinirs_host.preview.params import PreviewConfig
from noctinirs_host.preview.filters import FilterConfig
from noctinirs_host.preview.slots import SlotAssembler
if a.variant=='new':
    from noctinirs_host.preview.jitter import PreviewJitterBuffer,signed_delta

out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
data=Path(a.data)
geo=load_geometry(Path(a.source).parent/'config/optical_geometry_v1.json')
for n in (1,2):
    source=data/f'record_{n}.raw.bin'
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    recorded=json.loads((data/f'record_{n}.preview.json').read_text())['config_changes'][-1]['config']
    f=recorded['display_filters'];ss=recorded['short_separation'];base=recorded['baseline']
    cfg=PreviewConfig(baseline_s=base['duration_s'],min_baseline_samples=base['min_samples'],
        ssr_mode=ss['mode'],ssr_method=ss['method'],ridge_lambda=ss['ridge_lambda'],
        ssr_window_s=ss['window_s'],ssr_min_samples=ss['min_samples'],filters=FilterConfig(
        **{k:v for k,v in f.items() if k in FilterConfig.__dataclass_fields__}))
    pipeline=PreviewPipeline(geo,cfg,10)
    # Isolate continuity from the new effective cutoff/SSR behavior in a third trace.
    continuity=PreviewPipeline(geo,replace(cfg,ssr_mode='off'),10)
    if a.variant=='new':
        from noctinirs_host.preview.filters import PreviewFilters
        continuity.filters=PreviewFilters(cfg.filters,10)
    counts={'resets':0,'continuity_resets':0}
    for key,pl in [('resets',pipeline),('continuity_resets',continuity)]:
        original=pl.filters.reset
        def counted(original=original,key=key):counts[key]+=1;original()
        pl.filters.reset=counted
    traces=[];continuous=[]
    def take(frames,target):
        for frame in frames:
            p=frame.points['CH16']
            q=frame.points['SC1R']
            target.append((frame.t_s,p.i735,p.od735,p.d_hbo,float(p.filter_warming),q.i735,q.od735))
    jitter=PreviewJitterBuffer() if a.variant=='new' else None
    clock=None
    def release(packets):
        global clock
        for raw,host,msg in packets:
            if a.variant=='new':
                if clock is None:clock=(raw.first_sample_sequence,host)
                seq,start=clock
                host=start+round(signed_delta(raw.first_sample_sequence,seq)*125)
                clock=(raw.first_sample_sequence,host)
            take(pipeline.feed(raw,host,msg),traces)
            take(continuity.feed(raw,host,msg),continuous)
    assemblers=[SlotAssembler()]
    if a.variant=='new':assemblers.append(SlotAssembler('29-38'))
    measurements=[[] for _ in assemblers]
    def rows(which,values):
        for row in values:
            if not(row.partial or row.gap) and row.on_n and row.off_n:
                measurements[which].append((row.first_seq,row.slot,*[row.on_mean(i)-row.off_mean(i) for i in range(4)],
                                           *[row.off_mean(i) for i in range(4)]))
    replay=RawReplay(source)
    archive=RawArchive(out,f'{a.variant}_record_{n}')
    with archive.path.open('xb') as fh:
        archive.wire=fh;fh.write(MAGIC)
        for packet in replay:
            # The same received packet goes to the original archive writer BEFORE preview.
            archive.write_packet(packet)
            release(jitter.push(packet.raw,packet.host_rx_us,packet.envelope.msg_sequence) if jitter else
                    [(packet.raw,packet.host_rx_us,packet.envelope.msg_sequence)])
            for which,assembler in enumerate(assemblers):rows(which,assembler.feed(packet.raw,packet.host_rx_us))
        if jitter:release(jitter.drain(0,flush=True))
        take(pipeline.flush(),traces);take(continuity.flush(),continuous)
    arrays={'trace':np.asarray(traces),'continuity':np.asarray(continuous)}
    arrays.update({f'off_{i}':np.asarray(v) for i,v in enumerate(measurements)})
    np.savez_compressed(out/f'{a.variant}_record_{n}.npz',**arrays)
    after=hashlib.sha256(source.read_bytes()).hexdigest()
    result=dict(record=n,variant=a.variant,input_sha256=before,source_unchanged=before==after,
                output_sha256=hashlib.sha256(archive.path.read_bytes()).hexdigest(),
                byte_identical=source.read_bytes()==archive.path.read_bytes(),
                archive_problem=replay.archive_problem,meta_problem=replay.meta_problem,
                frames=len(traces),warming_fraction=float(np.mean(arrays['trace'][:,4])),
                counts=counts,loss=pipeline.loss_stats(),config=pipeline.config.to_metadata())
    (out/f'{a.variant}_record_{n}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('config','loss')}),flush=True)
