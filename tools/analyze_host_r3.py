"""Create private offline comparison plots and limited calibration estimates."""
import argparse,json,hashlib
from pathlib import Path
from dataclasses import replace
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from noctinirs_host.calibration import Assumptions,Level,empirical_choice
from noctinirs_host.calibration_sweep import spectral_noise
from noctinirs_host.geometry import load_geometry

def side_of_pd(geo,ain):return geo.pd_for_ain(ain).side
def side_of_slot(geo,slot):return geo.source_for_slot(slot)[0].side  # tool-local; the library no longer has a leak/contralateral rule

p=argparse.ArgumentParser()
p.add_argument('--data',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True)
a=p.parse_args();out=a.evidence;geo=load_geometry('config/optical_geometry_board1.json')
summary={}

def psd(trace,column):
    bins={int(round(row[0]*10)):row[column] for row in trace if np.isfinite(row[column])}
    windows=[];w=np.hanning(64)
    if bins:
        for start in range(min(bins),max(bins)-63,32):
            if all(i in bins for i in range(start,start+64)):
                x=np.array([bins[i] for i in range(start,start+64)])
                x-=x.mean();windows.append(abs(np.fft.rfft(x*w))**2/(10*sum(w*w)))
    return np.fft.rfftfreq(64,.1),np.mean(windows,axis=0) if windows else np.full(33,np.nan),len(windows)

for n in (1,2):
    old=np.load(out/f'old_record_{n}.npz');new=np.load(out/f'new_record_{n}.npz')
    fig,axes=plt.subplots(3,1,figsize=(13,9),sharex=True)
    for ax,arr,title in [(axes[0],old['continuity'],'Baseline 524127f (SSR off; original narrow filters)'),
                          (axes[1],new['continuity'],'R3 continuity only (SSR off; same narrow filters)'),
                          (axes[2],new['trace'],'R3 effective preview (cardiac protection; slow SSR correction)')]:
        tx=[];yy=[]
        for i,row in enumerate(arr):
            if i and row[0]-arr[i-1,0]>.15:
                tx.append(arr[i-1,0]+.1);yy.append(np.nan)
                ax.axvspan(arr[i-1,0],row[0],color='gray',alpha=.15)
            if not np.isfinite(row[3]):ax.axvspan(row[0]-.05,row[0]+.05,color='gray',alpha=.15)
            tx.append(row[0]);yy.append(row[3])
        ax.plot(tx,yy,lw=.7);ax.set_title(title+'; gray = missing CH16');ax.set_ylabel('CH16 delta HbO / uM');ax.grid(alpha=.25)
    axes[-1].set_xlabel('Device sample time / s');axes[0].set_xlim(60,120)
    fig.tight_layout();fig.savefig(out/f'record_{n}_sawtooth.png',dpi=140);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(14,4));spectra={}
    for ax,column,label in [(axes[0],1,'CH16 I735'),(axes[1],2,'CH16 OD735'),(axes[2],6,'SC1R OD735')]:
        for name,arr in [('baseline',old['trace']),('R3',new['trace'])]:
            f,s,windows=psd(arr,column);ax.semilogy(f,s,label=f'{name}, {windows} windows');ax.set_title(label);ax.set_xlim(.5,3)
            band=(f>=1.4)&(f<=2);peak=float(f[band][np.argmax(s[band])]);spectra[label+'_'+name]=dict(peak_hz=peak,windows=windows,pulse_power=float(s[band].sum()))
        ax.axvspan(1.5,1.8,alpha=.12,color='green');ax.legend(fontsize=7);ax.grid(alpha=.2);ax.set_xlabel('Hz')
    fig.tight_layout();fig.savefig(out/f'record_{n}_cardiac_psd.png',dpi=140);plt.close(fig)
    off={};snrs=[]
    for ain in range(4):
        off[ain]={}
        for index,preset in enumerate(('reported','29-38')):
            rows=new[f'off_{index}'];values=[];noise=[]
            for slot in range(20):
                slotrows=rows[rows[:,1]==slot]
                x=slotrows[:,2+ain]
                if side_of_pd(geo,ain)!=side_of_slot(geo,slot) and len(x)>1:
                    values.append(float(np.median(x)))
                    delta=np.diff(slotrows[:,6+ain]);noise.append(float(1.4826*np.median(abs(delta-np.median(delta)))/np.sqrt(2)))
            off[ain][preset]=dict(negative_control_step_median=float(np.median(values)),off_frame_noise=float(np.median(noise)))
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for axis,key,title in [(axes[0],'negative_control_step_median','Negative-control step / counts'),
                           (axes[1],'off_frame_noise','OFF robust frame noise / counts')]:
        for k,preset in enumerate(('reported','29-38')):
            axis.bar(np.arange(4)+(k-.5)*.35,[off[i][preset][key] for i in range(4)],width=.35,label=preset)
        axis.set_xticks(range(4),[f'AIN{i}' for i in range(4)]);axis.set_title(title);axis.legend()
    fig.tight_layout();fig.savefig(out/f'record_{n}_off_preset.png',dpi=140);plt.close(fig)
    rows=new['off_0'];level_data=[]
    for slot in range(20):
        sr=rows[rows[:,1]==slot];scores=[]
        for ain in geo.calibration_targets(slot):
            segments=np.split(sr,np.where(np.diff(sr[:,0])!=800)[0]+1);sigmas=[]
            for seg in segments:
                for start in range(0,len(seg)-31,32):sigmas.append(spectral_noise(seg[start:start+32,2+ain].tolist()))
            sigma=float(np.median(sigmas)) if sigmas else None
            step=float(np.median(sr[:,2+ain]))
            snr=step/sigma if sigma and sigma>0 else None
            scores.append(snr or 0);level_data.append(dict(slot=slot,ain=ain,step=step,noise=sigma,snr=snr))
        if geo.channel_for(slot,geo.calibration_targets(slot)[0]).kind=='long':snrs.append(max(scores))
    artifact=json.loads((a.data/f'record_{n}.iref-v1.json').read_text())
    table=[]
    for slot,saved in enumerate(artifact['slots']):
        levels=[Level(**{k:(tuple(v) if isinstance(v,list) else v) for k,v in row.items() if k in Level.__dataclass_fields__}) for row in saved['levels']]
        safety=geo.calibration_safety_targets(slot)
        evaluated=[replace(level,peak=tuple(x if i in safety else 0 for i,x in enumerate(level.peak)),
                       minimum=tuple(x if i in safety else 0 for i,x in enumerate(level.minimum)),
                       envelope=tuple(x if i in safety else 0 for i,x in enumerate(level.envelope))) for level in levels]
        targets=geo.calibration_targets(slot);short=geo.channel_for(slot,targets[0]).kind=='short'
        choice=empirical_choice(evaluated,targets,artifact['default_table']['codes'][slot],10,Assumptions(),short=short)
        potential=sum(v['counts'][slot]>=24 and v['counts'][slot]/31>=.75 and 'SWEEP_CONFIG_MISMATCH' not in v['flags'] for v in artifact['visits'])
        table.append(dict(slot=slot,old_code=saved['selection']['code'],estimated_code=choice.code,
                          retained_visits=sum(l.visits for l in levels),count_eligible_visits=potential,
                          flags=choice.flags))
    summary[n]=dict(off=off,spectra=spectra,levels=level_data,long_best_snr_quantiles=np.quantile(snrs,[0,.25,.5,.75,1]).tolist(),
                    calibration_estimates=table,limitation='Old level sigma retained. Rejected visit rows unavailable; count-eligible is not recovered evidence.')
(out/'analysis.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
print(json.dumps({n:{'off':v['off'],'snr_quantiles':v['long_best_snr_quantiles'],'calibration':v['calibration_estimates']} for n,v in summary.items()},indent=2))
