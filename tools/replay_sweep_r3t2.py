"""Offline re-evaluation of a recorded calibration sweep with the host estimators.

Usage: python tools/replay_sweep_r3t2.py --source SRC --sweep record_N.sweep.json --geometry GEO.json
           [--slots 10,11] [--out OUT.json]
Reads the sweep sidecar and RAW archive only (never writes to them). Visits are rebuilt with the host
Visit class from the exact received datagrams; no device, transport or recording is opened.
Run with the baseline source path and with the candidate source path to compare estimators.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ap = argparse.ArgumentParser()
for name in ("source", "sweep", "geometry"):
    ap.add_argument("--" + name, required=True)
ap.add_argument("--out")
ap.add_argument("--slots", default="10,11")
args = ap.parse_args()
sys.path.insert(0, str(Path(args.source).resolve()))
from dataclasses import asdict, replace
from noctinirs_host.calibration import Assumptions, empirical_choice, level_exclusions
from noctinirs_host.calibration_sweep import Visit, levels_for_slot
from noctinirs_host.geometry import load_geometry
from noctinirs_host.record.raw_archive import RawReplay

index = json.loads(Path(args.sweep).read_text())
archive = Path(args.sweep).with_name(index["archive"])
geometry = load_geometry(args.geometry)
params = Assumptions()
packets = list(RawReplay(archive))
visits = []
for item in index["visits"]:
    visit = Visit(SimpleNamespace(codes=tuple(item["codes"]), profile_id=0, profile_hash=""),
                  max(getattr(params, "visit_target_occurrences", 31), params.min_occurrences + 1),
                  geometry.geometry_map_id, params.off_window, 10, None)
    for pk in packets[item["start_packet"]:item["start_packet"] + item["packet_count"]]:
        visit.feed(pk.raw, pk.wire, pk.host_rx_us)
    visits.append(visit)
reference = visits[0].profile.codes  # overwritten below by the plan reference when available
from noctinirs_host.calibration_sweep import SweepPlan
plan = SweepPlan()
short_slots = {s for s in range(20) if any(geometry.channel_for(s, i).kind == "short"
                                           for i in geometry.calibration_targets(s))}
reference = tuple(plan.short_codes[plan.reference_index] if s in short_slots else plan.long_codes[plan.reference_index]
                  for s in range(20))
report = {}
for slot in [int(x) for x in args.slots.split(",")]:
    targets = geometry.calibration_targets(slot)
    levels = levels_for_slot(visits, slot, reference, targets, params)
    default = 133
    safety = geometry.calibration_safety_targets(slot)
    ignored = tuple(i for i in range(4) if i not in safety)   # same geometry exclusion as Sweep.finish
    evaluated = [replace(l, peak=tuple(v if i in safety else 0 for i, v in enumerate(l.peak)),
                         minimum=tuple(v if i in safety else 0 for i, v in enumerate(l.minimum)),
                         envelope=tuple(v if i in safety else 0 for i, v in enumerate(l.envelope))) for l in levels]
    choice = empirical_choice(evaluated, targets, default, 10, params, short=slot in short_slots)
    levels = evaluated
    report[slot] = dict(targets=targets, choice=asdict(choice), levels=[
        dict(code=l.code, visits=l.visits, rows=l.count, step=[round(x, 1) for x in l.mean],
             sigma=[round(x, 1) for x in l.sigma],
             snr=[round(m / s, 1) if s else None for m, s in zip(l.mean, l.sigma)],
             unc=[round(u, 3) for u in getattr(l, "uncertainty", ())], warn=list(getattr(l, "warnings", ())),
             excl=list(level_exclusions(l, params))) for l in levels])
text = json.dumps(report, indent=1, default=list)
print(text if not args.out else f"written {args.out}")
if args.out:
    Path(args.out).write_text(text)
