"""Stage 2 default-table wire codec (firmware contract S2-1.1)."""

from dataclasses import dataclass
import re

from .profile import args_set_profile, IREF_SLOTS

SHORT_STATES = ("UNSET", "PROVISIONAL", "SET")
SHORT_SLOTS = (0, 1, 10, 11)  # S2-1.1, geometry map 1; checked against host geometry at readback.


@dataclass(frozen=True)
class DefaultTable:
    source: str
    id: int
    hash: str | None
    short_status: str
    short_slots: tuple[int, ...]
    codes: tuple[int, ...]
    last_reject: str
    reject_slot: int


def _valid_codes(codes, status, slots):
    return (len(codes) == IREF_SLOTS and status in SHORT_STATES and slots == SHORT_SLOTS
            and all(type(code) is int and 1 <= code <= 133 for code in codes)
            and all(codes[i] < 133 if i in slots and status != "UNSET" else codes[i] == 133
                    for i in range(IREF_SLOTS)))


def args_set_default_table(ident, digest, codes, status, short_slots):
    codes = tuple(codes)
    if not _valid_codes(codes, status, tuple(short_slots)):
        raise ValueError("Default table needs code 133 for long slots and strict <133 for promoted short slots.")
    return args_set_profile(ident, digest, codes) + bytes([SHORT_STATES.index(status)])


def parse_default_table(ext):
    """None means unsupported or unusable. Never infer a table from a malformed reply."""
    try:
        fields = {}
        keys = {"v4deftable", "table_source", "default_table_id", "default_table_hash",
                "short_status", "short_slots", "last_table_reject", "reject_slot", "default_code"}
        for token in ext.decode("ascii").split():
            key, _, value = token.partition("=")
            if key in keys:
                if key in fields:
                    return None
                fields[key] = value
        if fields["v4deftable"] != "1":
            return None
        ident, digest = fields["default_table_id"], fields["default_table_hash"]
        if not re.fullmatch(r"0x[0-9a-fA-F]{8}", ident):
            return None
        ident = int(ident, 16)
        source, status = fields["table_source"], fields["short_status"]
        if source == "BUILD":
            if ident != 0 or digest != "none" or status != "UNSET":
                return None
            digest = None
        elif source != "RAM" or not ident or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return None
        slots = tuple(int(c) for c in fields["short_slots"].split(","))
        codes = tuple(int(c) for c in fields["default_code"].split(","))
        if not _valid_codes(codes, status, slots):
            return None
        return DefaultTable(source, ident, digest, status, slots, codes,
                            fields["last_table_reject"], int(fields["reject_slot"]))
    except (UnicodeDecodeError, ValueError, KeyError):
        return None
