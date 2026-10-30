"""Shared golden vectors from the firmware repository (pinned, hash-checked).

The vector files are NOT copied into this repository. Set NN2_GOLDEN_DIR to a folder
holding the four pinned files (CI downloads them; see .github/workflows/ci.yml and
config/reference_pins.json). Without the folder these tests are skipped, and the
skip is visible in the pytest summary.
"""

import json
import os
import struct
from pathlib import Path

import pytest

from noctinirs_host.gate import GateParams, evaluate
from noctinirs_host.geometry import load_geometry
from noctinirs_host.pins import load_pins, sha256_file
from noctinirs_host.wire import optical as o
from noctinirs_host.wire.errors import WireError

ROOT = Path(__file__).resolve().parents[1]
_DIR = os.environ.get("NN2_GOLDEN_DIR")
pytestmark = pytest.mark.skipif(not _DIR, reason="NN2_GOLDEN_DIR not set (golden vectors absent)")

FILES = ("optical_frame_vectors.json", "optical_gate_vectors.json",
         "optical_geometry_v1.json", "optical_gate_configs.json")


def _load(name):
    return json.loads((Path(_DIR) / name).read_text(encoding="utf-8"))


def test_golden_files_match_pinned_hashes():
    pins = load_pins(ROOT / "config" / "reference_pins.json")["golden_vectors"]
    for name in FILES:
        assert sha256_file(Path(_DIR) / name) == pins["files"][name]["sha256"], name


def _frame_vectors():
    return _load("optical_frame_vectors.json")["vectors"] if _DIR else []


def _diffs_opt2(f, exp):
    d = []
    for k in ("slot_plan_epoch", "frame_sequence", "first_sample_sequence", "sample_count",
              "frame_flags", "geometry_map_id", "gate_config_id"):
        if getattr(f, k) != exp[k]:
            d.append(k)
    for i, s in enumerate(exp["slot"]):
        r = f.slots[i]
        for k in ("slot_index", "quality", "reason", "n_on_used", "n_off_used",
                  "n_on_available", "n_off_available", "on_commit_late", "off_commit_late",
                  "witness_ain", "verified_mask"):
            if getattr(r, k) != s[k]:
                d.append(f"slot{i}.{k}")
        for j, dd in enumerate(s["det"]):
            x = r.detectors[j]
            for k in ("sum_on", "sum_off", "sd_on_q4", "sd_off_q4", "det_flags"):
                if getattr(x, k) != dd[k]:
                    d.append(f"slot{i}.det{j}.{k}")
            if x.stab_q != exp["stab_q"][i * 4 + j]:
                d.append(f"slot{i}.det{j}.stab_q")
    return d


def _diffs_raw(p, exp):
    d = [k for k in ("slot_plan_epoch", "first_sample_sequence", "sample_count",
                     "geometry_map_id") if getattr(p, k) != exp[k]]
    if p.gap_before != bool(exp["raw_flags"] & 2):
        d.append("gap_before")
    for i, s in enumerate(exp["sample"]):
        r = p.samples[i]
        if list(r.ain) != s["ain"] or r.phase_class != s["phase_class"] or r.slot_index != s["slot_index"]:
            d.append(f"sample{i}")
    return d


@pytest.mark.parametrize("v", [v for v in _frame_vectors() if v["expected_hex"] is not None],
                         ids=lambda v: v["name"])
def test_frame_vector(v):
    payload = bytes.fromhex(v["expected_hex"])
    if v["decode_result"] != "OK":
        # the C decoder rejects this packet; the host must reject it as well
        with pytest.raises(WireError):
            o.parse_frame(payload)
        return
    out = o.parse_frame(payload)
    if v["layout"] == 3:
        assert _diffs_raw(out, v["input"]) == []
        assert out.is_science is False
        return
    # the host keeps the wire values; it only closes science access where the C
    # receiver invalidated a slot (decoded_input), so compare with the wire input
    assert _diffs_opt2(out, v["input"]) == []
    if "decoded_input" in v:
        for i, s in enumerate(v["decoded_input"]["slot"]):
            if s["quality"] not in (o.Q_VALID_MINIMUM, o.Q_VALID_TARGET):
                assert out.science_mask(i) == 0, f"science leak in slot {i}"


def test_unverified_slots_never_become_science_in_any_ok_vector():
    for v in _frame_vectors():
        if v["layout"] != 2 or v["decode_result"] != "OK" or v["expected_hex"] is None:
            continue
        f = o.parse_frame(bytes.fromhex(v["expected_hex"]))
        for i, s in enumerate(f.slots):
            allowed = s.verified_mask if f.is_science else 0
            assert f.science_mask(i) & ~allowed == 0, (v["name"], i)


def _hex(x):
    return None if x is None else struct.pack(">d", x).hex()


@pytest.mark.parametrize("v", _load("optical_gate_vectors.json")["vectors"] if _DIR else [],
                         ids=lambda v: v["name"])
def test_gate_vector_bit_exact(v):
    w, p, e = v["wire"], v["parameters"], v["expected"]
    params = GateParams(1, p["z_x10"], p["z_x10"], p["stable_k_x10"], p["floor_counts"],
                        p["se_floor_x100"])
    r = evaluate(w["n_on"], w["n_off"], w["sum_on"], w["sum_off"], w["sd_on_q4"],
                 w["sd_off_q4"], w["stab_q"], params, p["z_x10"])
    got = {"STEP_Z_PASS": r.step_z_pass, "FLOOR_PASS": r.floor_pass, "STABLE_PASS": r.stable_pass,
           "diff_f64_hex": _hex(r.diff) if r.evaluated else None,
           "thr_f64_hex": _hex(r.threshold) if r.evaluated else None}
    assert got == e


def test_official_geometry_loads_and_has_same_map_as_draft():
    off = load_geometry(Path(_DIR) / "optical_geometry_v1.json")
    draft = load_geometry(ROOT / "config" / "optical_geometry_v1.draft.json")
    assert off.is_draft  # status draft_pending_caliper: distances not locked
    assert off.geometry_map_id == draft.geometry_map_id
    assert off.detectors == draft.detectors and off.sources == draft.sources
    assert off.channels == draft.channels
