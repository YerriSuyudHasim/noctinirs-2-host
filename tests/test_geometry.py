import json
from pathlib import Path

import pytest

from noctinirs_host.geometry import GeometryError, load_geometry
from noctinirs_host.pins import load_pins, sha256_file

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "config" / "optical_geometry_v1.draft.json"


def test_loads_and_hash_matches_pin():
    geo = load_geometry(GEO)
    pins = load_pins(ROOT / "config" / "reference_pins.json")
    pinned = pins["pinned_files"]["docs/drafts/optical_geometry_v1.draft.json"]["sha256"]
    assert geo.sha256 == pinned == sha256_file(GEO)
    assert geo.is_draft
    assert len(geo.channels) == 20 and geo.geometry_map_id == 1


def test_ain_to_pd_single_map():
    geo = load_geometry(GEO)
    assert [geo.pd_for_ain(a).pd for a in range(4)] == ["PD3", "PD4", "PD1", "PD2"]


def test_channel_lookup_and_unselected_pair():
    geo = load_geometry(GEO)
    assert geo.channel_for(2, 3).name == "CH1"  # L2 (slot 2 = 735) -> PD2 (AIN3)
    assert geo.channel_for(3, 3).name == "CH1"  # same source, other wavelength
    assert geo.channel_for(0, 0) is None  # L1 -> PD3 is opposite side, not a channel


def test_contralateral_negative_control():
    geo = load_geometry(GEO)
    assert geo.is_contralateral(0, 0)  # L1 (side L) vs PD3 (side R)
    assert not geo.is_contralateral(0, 2)  # L1 vs PD1 (side L)
    assert geo.is_contralateral(10, 2)  # L6 (side R) vs PD1 (side L)


def _mutated(tmp_path, fn):
    g = json.loads(GEO.read_text())
    fn(g)
    p = tmp_path / "g.json"
    p.write_text(json.dumps(g))
    return p


@pytest.mark.parametrize("mutate", [
    lambda g: g["channels"].__setitem__(1, dict(g["channels"][1], name="CH1")),
    lambda g: g["sources"][0]["slot"].__setitem__("850", 5),
    lambda g: g["channels"][0].__setitem__("ain", 0),
    lambda g: g.__setitem__("schema", "other/1"),
    lambda g: g["adc"]["ain_to_pd"].__setitem__("0", "PD9"),
    lambda g: g["channels"].pop(),
])
def test_invalid_geometry_rejected(tmp_path, mutate):
    with pytest.raises(GeometryError):
        load_geometry(_mutated(tmp_path, mutate))


def test_not_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{nope")
    with pytest.raises(GeometryError):
        load_geometry(p)


def _bad_value(g, key_path, value):
    node = g
    for k in key_path[:-1]:
        node = node[k]
    node[key_path[-1]] = value


@pytest.mark.parametrize("path,value", [
    (("detectors", 0, "ain"), 2.9),
    (("detectors", 0, "ain"), True),
    (("detectors", 0, "ain"), "2"),
    (("channels", 0, "ain"), 3.5),
    (("geometry_map_id",), -1),
    (("geometry_map_id",), 65536),
    (("geometry_map_id",), 1.5),
    (("geometry_map_id",), True),
    (("channels", 0, "sd_mm"), float("nan")),
    (("channels", 0, "sd_mm"), float("inf")),
    (("channels", 0, "sd_mm"), -5),
    (("channels", 0, "sd_mm"), 0),
    (("channels", 0, "sd_mm"), "30"),
    (("sources", 0, "slot", "735"), 0.5),
])
def test_malformed_values_rejected(tmp_path, path, value):
    with pytest.raises(GeometryError):
        load_geometry(_mutated(tmp_path, lambda g: _bad_value(g, path, value)))
