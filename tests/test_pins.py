from pathlib import Path

from noctinirs_host import VERIFIED_AGAINST_C_IMPLEMENTATION
from noctinirs_host.pins import load_pins

ROOT = Path(__file__).resolve().parents[1]


def test_pins_declare_unverified_against_c():
    pins = load_pins(ROOT / "config" / "reference_pins.json")
    assert pins["firmware_commit"] == "2ccbbffb00ea735a1cd652d271e3a1bed463a25d"
    assert pins["verified_against_c_implementation"] is False
    assert pins["golden_vectors"] is None
    assert VERIFIED_AGAINST_C_IMPLEMENTATION is False
    for entry in pins["pending_uncommitted"].values():
        assert len(entry["sha256"]) == 64
