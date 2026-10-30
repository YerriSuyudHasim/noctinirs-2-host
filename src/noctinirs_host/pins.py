"""Reference pins: which firmware documents this host code was written against."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_pins(path: str | Path) -> dict:
    pins = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("firmware_commit", "pinned_files", "pending_uncommitted",
                "verified_against_c_implementation"):
        if key not in pins:
            raise ValueError(f"reference pins: missing {key}")
    return pins
