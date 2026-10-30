"""Errors raised by the wire parsers.

A WireError means: reject this packet and count it under `code`.
Callers keep one counter per code (for example collections.Counter).
"""

from __future__ import annotations


class WireError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
