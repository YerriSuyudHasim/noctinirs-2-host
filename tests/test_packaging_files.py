"""Packaging inputs that the build depends on."""

from __future__ import annotations

import pathlib

LIC = pathlib.Path(__file__).resolve().parents[1] / "packaging" / "licenses"


def test_lgpl_and_gpl_texts_present() -> None:
    lgpl = (LIC / "LGPL-3.0-only.txt").read_text(encoding="utf-8")
    gpl = (LIC / "GPL-3.0-only.txt").read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl and "Version 3" in lgpl
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "Version 3" in gpl
