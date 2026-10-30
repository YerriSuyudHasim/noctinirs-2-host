from pathlib import Path

from noctinirs_host import VERIFIED_AGAINST_C_IMPLEMENTATION
from noctinirs_host.pins import load_pins

ROOT = Path(__file__).resolve().parents[1]


def test_pins_declare_golden_checked_but_c_code_not_run():
    pins = load_pins(ROOT / "config" / "reference_pins.json")
    assert pins["firmware_commit"] == "e42f2bc9b453b223bf4557516f63fcceadfddbc8"
    assert pins["verified_against_golden_vectors"] is True
    assert pins["verified_against_c_implementation"] is False
    assert VERIFIED_AGAINST_C_IMPLEMENTATION is False
    contract = pins["pinned_files"]["docs/drafts/OPTICAL_DATA_CONTRACT_DRAFT.md"]
    assert contract["sha256"].startswith("4dd2b89b") and len(contract["sha256"]) == 64
    for entry in pins["golden_vectors"]["files"].values():
        assert len(entry["sha256"]) == 64
