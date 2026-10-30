"""Test-only packet builders. Self-made data; NOT the firmware golden vectors."""

import struct
import zlib

INT32_MIN = -(2 ** 31)


def build_envelope(payload=b"", msg_type=2, flags=0, session_id=1, msg_sequence=0, ts=0,
                   env_version=1, proto=1, magic=0x32534E4E, bad_crc=False, length=None):
    n = len(payload) if length is None else length
    head = struct.pack("<IBBHHHIIQ", magic, env_version, msg_type, proto, n, flags,
                       session_id, msg_sequence, ts)
    body = head + payload
    crc = zlib.crc32(body) & 0xFFFFFFFF
    if bad_crc:
        crc ^= 1
    return body + struct.pack("<I", crc)


def det(sum_on=1000 * 10, sum_off=100 * 10, sd_on=48, sd_off=48, flags=0x1C, stab=8):
    """Default: ON mean 1000, OFF mean 100 (n=10), sd 3.0 counts, STEP_Z|STABLE|FLOOR."""
    return dict(sum_on=sum_on, sum_off=sum_off, sd_on=sd_on, sd_off=sd_off, flags=flags, stab=stab)


def blank_det(flags=0):
    return dict(sum_on=INT32_MIN, sum_off=INT32_MIN, sd_on=0xFFFF, sd_off=0xFFFF,
                flags=flags, stab=0xFF)


def slot(i, quality=2, reason=0, n_on=10, n_off=10, mask=0x0F, witness=0, dets=None,
         avail_on=None, avail_off=None):
    """avail_* default to the used counts (available == used)."""
    if dets is None:
        dets = [det() if (mask >> d) & 1 else blank_det() for d in range(4)]
    return dict(i=i, quality=quality, reason=reason, n_on=n_on, n_off=n_off, mask=mask,
                witness=witness, dets=dets,
                avail_on=n_on if avail_on is None else avail_on,
                avail_off=n_off if avail_off is None else avail_off)


def blank_slot(i, reason=1 << 10):
    return slot(i, quality=0, reason=reason, n_on=10, n_off=10, mask=0, witness=0xFF)


def eng_slot(i):
    dets = [det(flags=0x40) for _ in range(4)]
    return slot(i, quality=2, reason=0, mask=0, witness=0xFF, dets=dets)


def build_opt2(slots=None, frame_flags=0, gate_config_id=1, adc_source=1, layout=2,
               slot_count=20, det_count=4, reserved=0, frame_sequence=5, first_sample=800,
               epoch=1, geometry_map_id=1, slot_overrides=None):
    slots = list(slots) if slots is not None else [slot(i) for i in range(20)]
    for k, v in (slot_overrides or {}).items():
        slots[k] = v
    out = struct.pack("<BBHIIHHBBHHH", adc_source, layout, epoch, frame_sequence, first_sample,
                      800, frame_flags, slot_count, det_count, geometry_map_id,
                      gate_config_id, reserved)
    stab = bytearray()
    for s in slots:
        out += struct.pack("<BBHBBBBBBBB", s["i"], s["quality"], s["reason"], s["n_on"],
                           s["n_off"], s["avail_on"], s["avail_off"], 0, 0, s["witness"], s["mask"])
        for d in s["dets"]:
            out += struct.pack("<iiHHB", d["sum_on"], d["sum_off"], d["sd_on"], d["sd_off"],
                               d["flags"])
            stab.append(d["stab"])
    return out + bytes(stab)


def i24(v):
    return (v & 0xFFFFFF).to_bytes(3, "little")


def raw_sample(ain=(0, 0, 0, 0), phase=0, slot_idx=0xFF):
    return b"".join(i24(a) for a in ain) + bytes([phase, slot_idx])


def build_raw(samples=None, raw_flags=1, first=1000, adc_source=1, layout=3, epoch=1, geo=1,
              reserved=0, count=None):
    samples = samples if samples is not None else [raw_sample()]
    n = len(samples) if count is None else count
    return struct.pack("<BBHIHHHH", adc_source, layout, epoch, first, n, raw_flags, geo,
                       reserved) + b"".join(samples)
