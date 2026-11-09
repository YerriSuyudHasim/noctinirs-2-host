"""Replay a recorded RAW archive through the preview pipeline (read-only, host/unit evidence).

    python replay_preview.py --src <repo>/src --raw record_1.raw.bin --ssr off --out table.md

`--src` selects which version of the host code runs, so the same recording can be replayed
with the code before and after a change. The archive is only read. Nothing here is a device test.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to the host src directory to import")
    ap.add_argument("--raw", required=True, help="a .raw.bin archive (read only)")
    ap.add_argument("--geometry", help="geometry JSON (default: config/ next to --src)")
    ap.add_argument("--ssr", default="off", choices=["off", "5mm", "8mm", "both"])
    ap.add_argument("--baseline-s", type=float, default=10.0)
    ap.add_argument("--fs", type=float, default=10.0)
    ap.add_argument("--out", help="markdown table output")
    ap.add_argument("--json", help="JSON output")
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    sys.dont_write_bytecode = True
    from noctinirs_host.geometry import load_geometry
    from noctinirs_host.preview.params import PreviewConfig
    from noctinirs_host.preview.pipeline import PreviewPipeline
    from noctinirs_host.record.raw_archive import RawReplay
    from noctinirs_host.wire.envelope import parse_envelope
    from noctinirs_host.wire.optical import parse_raw

    geo_path = args.geometry or str(Path(args.src).parent / "config" / "optical_geometry_v1.json")
    geometry = load_geometry(geo_path)
    pipe = PreviewPipeline(geometry, PreviewConfig(baseline_s=args.baseline_s, ssr_mode=args.ssr), args.fs)
    takes_seq = "msg_sequence" in inspect.signature(pipe.feed).parameters
    frames, packets = [], 0
    for packet in RawReplay(args.raw):
        env = parse_envelope(packet.datagram())
        raw = parse_raw(env.payload)
        packets += 1
        if takes_seq:
            frames += pipe.feed(raw, packet.host_rx_us, env.msg_sequence)
        else:
            frames += pipe.feed(raw, packet.host_rx_us)
    frames += pipe.flush()

    names = [c.name for c in geometry.channels]
    kinds = {c.name: c.kind for c in geometry.channels}
    rows, summary = [], {}
    for name in names:
        states = Counter()
        warned = uncorrected = 0
        reasons = Counter()
        for fr in frames:
            p = fr.points[name]
            states[p.state] += 1
            if p.state == "VALID":
                if getattr(p, "warnings", ()):
                    warned += 1
                if getattr(p, "ssr_state", "") == "UNCORRECTED":
                    uncorrected += 1
            elif p.state == "INVALID":
                reasons[p.reason.split(";")[0].split(": ")[-1].split(" ")[0]] += 1
        n = len(frames)
        shown = states["VALID"]
        rows.append((name, kinds[name], n, shown, warned, uncorrected, states["BASELINE"],
                     states["INVALID"], states["SSR_WARMUP"]))
        summary[name] = dict(frames=n, valid=shown, warned=warned, uncorrected=uncorrected,
                             baseline=states["BASELINE"], invalid=states["INVALID"],
                             ssr_warmup=states["SSR_WARMUP"], top_invalid=reasons.most_common(3))

    def pct(x, n):
        return f"{100.0 * x / n:5.1f}"

    lines = [f"Replay of `{Path(args.raw).name}` ({packets} packets, {len(frames)} frames), "
             f"SSR {args.ssr}, baseline {args.baseline_s:g} s, code `{args.src}`", "",
             "| Channel | Kind | VALID % | of which warned % | of which uncorrected % | BASELINE % | INVALID % | SSR_WARMUP % |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for name, kind, n, v, w, u, b, i, s in rows:
        lines.append(f"| {name} | {kind} | {pct(v, n)} | {pct(w, n)} | {pct(u, n)} | {pct(b, n)} | "
                     f"{pct(i, n)} | {pct(s, n)} |")
    for kind in ("long", "short"):
        sel = [r for r in rows if r[1] == kind]
        tot = sum(r[2] for r in sel)
        lines.append(f"| **{kind} (mean)** | | {pct(sum(r[3] for r in sel), tot)} | {pct(sum(r[4] for r in sel), tot)} "
                     f"| {pct(sum(r[5] for r in sel), tot)} | {pct(sum(r[6] for r in sel), tot)} | "
                     f"{pct(sum(r[7] for r in sel), tot)} | {pct(sum(r[8] for r in sel), tot)} |")
    prov = pipe.provenance()
    lines += ["", "invalid_frame_counts (provenance): " + json.dumps(prov.get("invalid_frame_counts")),
              "loss: " + json.dumps(prov.get("loss"))]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
