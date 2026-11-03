"""CSV row codec for OPT2 frames: frame -> 80 rows, 80 rows -> exact payload bytes.

Format description for users: docs/recording-format.md.

Data columns are integers copied from the parsed frame and envelope; nothing derived is
stored as data. The info columns (source, wavelength, pd, channel, host_note) are
informational, come from `geometry` only and are ignored on reading.
"""

from __future__ import annotations

import struct

from ..geometry import Geometry
from ..wire import optical as opt
from ..wire.envelope import Envelope

FRAME_COLS = (
    "host_rx_us", "session_id", "msg_sequence", "env_flags", "device_ts_us", "adc_source",
    "slot_plan_epoch", "frame_sequence", "first_sample_sequence", "sample_count",
    "frame_flags", "geometry_map_id", "gate_config_id",
)
SLOT_COLS = (
    "slot", "quality", "reason", "n_on_used", "n_off_used", "n_on_avail", "n_off_avail",
    "on_commit_late", "off_commit_late", "witness_ain", "verified_mask",
)
DET_COLS = ("ain", "sum_on", "sum_off", "sd_on_q4", "sd_off_q4", "det_flags", "stab_q")
INFO_COLS = ("source", "wavelength", "pd", "channel", "host_note")
COLUMNS = FRAME_COLS + SLOT_COLS + DET_COLS + INFO_COLS
N_DATA = len(FRAME_COLS) + len(SLOT_COLS) + len(DET_COLS)
ROWS_PER_FRAME = opt.SLOT_COUNT * opt.DET_COUNT

_PREFIX = struct.Struct("<BBHIIHHBBHHH")
_SLOT_HEAD = struct.Struct("<BBHBBBBBBBB")
_DET = struct.Struct("<iiHHB")


def frame_rows(frame: opt.Opt2Frame, env: Envelope, rx_us: int,
               geometry: Geometry | None) -> list[list]:
    """The 80 rows of one frame, slot-major then AIN."""
    names_ok = geometry is not None and frame.geometry_map_id == geometry.geometry_map_id
    head = [rx_us, env.session_id, env.msg_sequence, env.flags, env.timestamp_us,
            frame.adc_source, frame.slot_plan_epoch, frame.frame_sequence,
            frame.first_sample_sequence, frame.sample_count, frame.frame_flags,
            frame.geometry_map_id, frame.gate_config_id]
    rows: list[list] = []
    for s in frame.slots:
        slot_part = [s.slot_index, s.quality, s.reason, s.n_on_used, s.n_off_used,
                     s.n_on_available, s.n_off_available, s.on_commit_late, s.off_commit_late,
                     s.witness_ain, s.verified_mask]
        for ain, d in enumerate(s.detectors):
            source = wl = pd = chan = ""
            if names_ok:  # names only through geometry (single translation point)
                src, wl = geometry.source_for_slot(s.slot_index)
                source, pd = src.id, geometry.pd_for_ain(ain).pd
                c = geometry.channel_for(s.slot_index, ain)
                chan = c.name if c is not None else ""
            notes = []
            if not frame.is_science:
                notes.append("non_science")
            if s.host_invalid:
                notes.append(f"host_invalid:{s.host_invalid}")
            if geometry is not None and not names_ok:
                notes.append("geometry_map_id_differs")
            rows.append(head + slot_part + [ain, d.sum_on, d.sum_off, d.sd_on_q4, d.sd_off_q4,
                                            d.det_flags, d.stab_q]
                        + [source, wl, pd, chan, ";".join(notes)])
    return rows


def rows_to_payload(rows: list[list[str]]) -> tuple[dict, bytes]:
    """80 CSV rows (strings) -> (frame-level values, OPT2 payload bytes).
    Raises ValueError when the rows do not form one consistent frame."""
    if len(rows) != ROWS_PER_FRAME:
        raise ValueError(f"expected {ROWS_PER_FRAME} rows, got {len(rows)}")
    nf, ns = len(FRAME_COLS), len(SLOT_COLS)
    first = [int(v) for v in rows[0][:nf]]
    frame_vals = dict(zip(FRAME_COLS, first))
    out = bytearray(_PREFIX.pack(
        frame_vals["adc_source"], opt.LAYOUT_OPT2, frame_vals["slot_plan_epoch"],
        frame_vals["frame_sequence"], frame_vals["first_sample_sequence"],
        frame_vals["sample_count"], frame_vals["frame_flags"], opt.SLOT_COUNT, opt.DET_COUNT,
        frame_vals["geometry_map_id"], frame_vals["gate_config_id"], 0))
    stab = bytearray()
    for slot in range(opt.SLOT_COUNT):
        base = slot * opt.DET_COUNT
        sv = [int(v) for v in rows[base][nf:nf + ns]]
        if sv[0] != slot:
            raise ValueError(f"row block {slot} carries slot {sv[0]}")
        out += _SLOT_HEAD.pack(*sv)
        for ain in range(opt.DET_COUNT):
            r = rows[base + ain]
            if [int(v) for v in r[:nf]][1:] != first[1:] or r[0] != rows[0][0]:
                raise ValueError("frame-level values differ between rows")
            if [int(v) for v in r[nf:nf + ns]] != sv:
                raise ValueError("slot-level values differ between rows")
            dv = [int(v) for v in r[nf + ns:N_DATA]]
            if dv[0] != ain:
                raise ValueError(f"slot {slot}: detector row order broken")
            out += _DET.pack(dv[1], dv[2], dv[3], dv[4], dv[5])
            stab.append(dv[6])
    return frame_vals, bytes(out) + bytes(stab)
