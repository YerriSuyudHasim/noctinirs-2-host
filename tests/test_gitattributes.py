"""Pinned files must keep their exact bytes on every platform (Windows CRLF issue)."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_pinned_geometry_is_protected_from_eol_conversion() -> None:
    lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "config/optical_geometry_v1.json -text" in [ln.strip() for ln in lines]


def test_pinned_geometry_in_checkout_has_no_crlf() -> None:
    assert b"\r\n" not in (ROOT / "config" / "optical_geometry_v1.json").read_bytes()
