"""Offline replay of a recording through the PREVIEW pipeline with the Tahap 2 quality markers.

Usage: python tools/replay_quality_r3t2.py --source SRC --data DIR --record N --geometry GEO.json
           --output DIR [--ssr off|5mm] [--no-pulse-protection]
Reads <DIR>/record_N.raw.bin only (SHA-256 checked before and after). Writes derived files to --output.
Frames are fed through the same jitter buffer and ADC-clock time base as the live session.
The RAW archive is re-written byte-for-byte to prove that preview/quality never touch RAW.
"""
import argparse, hashlib, json, sys
from pathlib import Path

ap = argparse.ArgumentParser()
for n in ("source", "data", "record", "geometry", "output"):
    ap.add_argument("--" + n, required=True)
ap.add_argument("--ssr", default="off")
ap.add_argument("--no-pulse-protection", action="store_true")
a = ap.parse_args()
sys.path.insert(0, str(Path(a.source).resolve()))
import numpy as np
from dataclasses import replace
from noctinirs_host.geometry import load_geometry
from noctinirs_host.preview.filters import FilterConfig
from noctinirs_host.preview.jitter import PreviewJitterBuffer, signed_delta
from noctinirs_host.preview.params import PreviewConfig
from noctinirs_host.preview.pipeline import PreviewPipeline
from noctinirs_host.record.raw_archive import MAGIC, RawArchive, RawReplay

out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
src = Path(a.data) / f"record_{a.record}.raw.bin"
before = hashlib.sha256(src.read_bytes()).hexdigest()
geo = load_geometry(a.geometry)
cfg = PreviewConfig(baseline_s=10.0, ssr_mode=a.ssr, filters=FilterConfig(pulse_protection=not a.no_pulse_protection))
pl = PreviewPipeline(geo, cfg, 10)
names = [c.name for c in geo.channels]
rows, clock = [], None

def take(frames):
    for f in frames:
        r = dict(t=f.t_s, motion_L=int(f.motion.get("L", False)), motion_R=int(f.motion.get("R", False)))
        for n, p in f.points.items():
            r[n] = (p.state == "VALID", p.i735, p.i850, p.od735, p.od850, p.d_hbo, p.d_hbr, p.sigma735, p.sigma850,
                    p.sci, p.cardiac_db, p.r_hbo_hbr, p.tier, p.motion_z, ";".join(p.warnings), p.ssr_state, p.ssr_reason)
        rows.append(r)

def release(packets):
    global clock
    for raw, host, msg in packets:
        if clock is None:
            clock = (raw.first_sample_sequence, host)
        seq, start = clock
        host = start + round(signed_delta(raw.first_sample_sequence, seq) * 125)
        clock = (raw.first_sample_sequence, host)
        take(pl.feed(raw, host, msg))

jitter = PreviewJitterBuffer()
replay = RawReplay(src)
import tempfile
tmpdir = Path(tempfile.mkdtemp(prefix="r3t2_copy_"))
archive = RawArchive(tmpdir, f"copy_record_{a.record}")
with archive.path.open("xb") as fh:
    archive.wire = fh
    fh.write(MAGIC)
    for packet in replay:
        archive.write_packet(packet)
        release(jitter.push(packet.raw, packet.host_rx_us, packet.envelope.msg_sequence))
    release(jitter.drain(0, flush=True))
take(pl.flush())
after = hashlib.sha256(src.read_bytes()).hexdigest()
tag = f"r{a.record}_ssr{a.ssr}{'_nopp' if a.no_pulse_protection else ''}"
cols = ["valid", "i735", "i850", "od735", "od850", "hbo", "hbr", "s735", "s850", "sci", "card_db", "r_hbo_hbr",
        "tier", "motion_z", "warn", "ssr_state", "ssr_reason"]
np.savez_compressed(out / f"{tag}.npz", t=np.array([r["t"] for r in rows]),
                    motion_L=np.array([r["motion_L"] for r in rows]), motion_R=np.array([r["motion_R"] for r in rows]),
                    channels=np.array(names), cols=np.array(cols),
                    data=np.array([[r[n] for n in names] for r in rows], dtype=object))
prov = pl.provenance()
summary = dict(record=a.record, tag=tag, frames=len(rows), input_sha256=before, input_unchanged=before == after,
               raw_copy_byte_identical=src.read_bytes() == archive.path.read_bytes(),
               archive_problem=replay.archive_problem, meta_problem=replay.meta_problem,
               motion=prov["signal_quality"]["motion"], short_separation=prov["short_separation"],
               warning_counts=prov["warning_counts"], ignored_pd_clipped=prov["ignored_pd_clipped"],
               invalid_reason_counts=prov["invalid_reason_counts"])
(out / f"{tag}.summary.json").write_text(json.dumps(summary, indent=1, default=str))
archive.path.unlink()
print(json.dumps({k: summary[k] for k in ("record", "tag", "frames", "input_unchanged", "raw_copy_byte_identical",
                                          "archive_problem", "motion")}, default=str))
