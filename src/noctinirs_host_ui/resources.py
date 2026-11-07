"""Paths shared by the desktop entry points."""

from pathlib import Path
import sys

GEOMETRY_NAME = "optical_geometry_v1.json"


def geometry_path() -> Path:
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])
    return Path(base) / "config" / GEOMETRY_NAME
