"""LED IREF calibration profile: `get-profile` (0x0034) reply parser, and request/test encoders.

Read-only support for the firmware "Calib Stage-1 FIX1" report. The host never fits, selects, derives or
sends a profile on its own; this module only decodes what the device reports.

Pinned source (the host does not extend the protocol; changes are raised in `noctinirs-2`):
  firmware repo noctinirs-2, base 519a96a26ede6517595d4f8a87cd64ab6b7a6fa5 plus the local patch series
    0001 sha256 b3f98fdd330950604e5b8148685ff65dff5342e1cefc2b18d82799d0e80a9c97
    0002 sha256 1c57ebe36676b5d7b8d5a9cd9e57824d258d5ab90b358d99d0411ecfc9954ef8
    0003 sha256 437e73fe9b6e7ea7eb4330ea09418ed088275f63a4c6f17282a324875955cc4c   (FIX1)
    0004 sha256 c691ab5392982ea08de619e4d70e6196789748e0d30f03c31181f54ff122e585   (FIX2: stop_evidence
         gains NOT_RUN and UNAVAILABLE; no key added or removed, worst case still 1404 of 1412 bytes)
  docs/adr/0004-host-protocol.md section 15 and docs/FW_CALIB_STAGE1_IREF_PROFILE.md, revision
  "local amendment (Stage 1 calibration FIX1) 2026-11-10". The series is not committed in the firmware
  repository yet, so there is no commit SHA: the patch hashes above identify the contract.
Not verified against a device. The text format was taken from the patch sources and the FIX1 report.

Wire format (text, ASCII, `key=value` separated by single spaces; no value contains a space):
  v4profile=1 profile_state profile_armed profile_id profile_hash [profile_code] last_profile_reject
  reject_slot default_code profile_source start_result mismatch_slot applied_profile_id
  applied_profile_hash requested_code applied_code applied_read applied_valid requested_ua applied_ua
  applied_at_stop stop_evidence stop_integrity stop_match stop_slot stop_error legacy_requested_ma
  iref_ua_per_lsb iref_code_default iref_code_max
or `v4profile=0 reason=unsupported|overflow`. Worst case 1404 of 1412 bytes. FIX1 removed `stop_read`.
Clients must ignore unknown keys (ADR-0004 section 15); this parser does, and keeps them.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

IREF_SLOTS = 20
IREF_CODE_MIN, IREF_CODE_MAX = 1, 222          # accepted by set-profile (0 and > 222 are refused, never clamped)
PROFILE_HASH_BYTES = 32
PROFILE_ARG_BYTES = 4 + PROFILE_HASH_BYTES + IREF_SLOTS     # set-profile: id u32 LE, hash[32], code[20]
MAX_TEXT = 1412                                              # firmware text capacity (payload 1440 - 28)

STATES = ("NONE", "ARMED", "APPLIED", "NOT_APPLIED")
SOURCES = ("NONE", "DEFAULT", "PROFILE")
START_RESULTS = ("NONE", "OK", "PROFILE_NOT_APPLIED", "START_FAILED", "START_CANCELLED")
STOP_EVIDENCE = ("NONE", "VALID_MATCH", "VALID_MISMATCH", "TRANSPORT_ERR", "TIMEOUT", "PREEMPTED",
                 "SKIPPED_EMERGENCY", "NOT_RUN", "UNAVAILABLE")
INTEGRITY = ("OK", "BLOCKED", "UNVERIFIED")
# The device documents exactly these evidence names; PREEMPTED is an internal capture result that FIX1 maps
# to SKIPPED_EMERGENCY (or TIMEOUT) before reporting. It is tolerated, not expected.
# FIX2 (patch 0004) adds NOT_RUN (normal STOP whose capture never ran) and UNAVAILABLE; both are UNVERIFIED.
DOCUMENTED_EVIDENCE = ("NONE", "VALID_MATCH", "VALID_MISMATCH", "TRANSPORT_ERR", "TIMEOUT", "SKIPPED_EMERGENCY",
                       "NOT_RUN", "UNAVAILABLE")
# stop_integrity as the firmware derives it from the evidence (nn2_led_stop_integrity_name)
INTEGRITY_OF = {"VALID_MATCH": "OK", "VALID_MISMATCH": "BLOCKED", "TRANSPORT_ERR": "BLOCKED"}

_HEX_HASH = re.compile(r"[0-9a-f]{64}")
_INT = re.compile(r"-?\d{1,11}")
_HEX_ID = re.compile(r"0x[0-9A-Fa-f]{1,8}")

_ENUM_KEYS = {"profile_state": STATES, "profile_source": SOURCES, "start_result": START_RESULTS,
              "stop_evidence": STOP_EVIDENCE, "stop_integrity": INTEGRITY}
_CODE_KEYS = ("profile_code", "default_code", "requested_code", "applied_code", "applied_at_stop",
              "requested_ua", "applied_ua", "legacy_requested_ma")
_INT_KEYS = ("reject_slot", "mismatch_slot", "stop_slot", "stop_error", "iref_ua_per_lsb",
             "iref_code_default", "iref_code_max")
_BOOL_KEYS = ("profile_armed", "applied_read", "applied_valid", "stop_match")
_ID_KEYS = ("profile_id", "applied_profile_id")
_HASH_KEYS = ("profile_hash", "applied_profile_hash")
_NAME_KEYS = ("last_profile_reject",)
KNOWN_KEYS = (("profile_state", "profile_armed", "profile_id", "profile_hash", "profile_code",
               "last_profile_reject", "reject_slot", "default_code", "profile_source", "start_result",
               "mismatch_slot", "applied_profile_id", "applied_profile_hash", "requested_code",
               "applied_code", "applied_read", "applied_valid", "requested_ua", "applied_ua",
               "applied_at_stop", "stop_evidence", "stop_integrity", "stop_match", "stop_slot",
               "stop_error", "legacy_requested_ma", "iref_ua_per_lsb", "iref_code_default",
               "iref_code_max"))
_REMOVED_KEYS = ("stop_read",)       # present in the pre-FIX1 series (0001/0002); ignored, not an error
MAX_EXTRA = 64


@dataclass(frozen=True)
class ProfileReport:
    """One parsed `get-profile` reply. Every field is None when the device did not send it (older firmware)
    or sent a value this parser cannot trust (then `warnings` says which). Nothing is inferred."""
    supported: bool                       # v4profile=1
    reason: str | None = None             # v4profile=0: unsupported | overflow | host-side parse reasons
    state: str | None = None
    armed: bool | None = None
    profile_id: int | None = None
    profile_hash: str | None = None       # 64 hex characters, None for "none"
    profile_code: tuple | None = None
    last_reject: str | None = None
    reject_slot: int | None = None
    default_code: tuple | None = None
    source: str | None = None
    start_result: str | None = None
    mismatch_slot: int | None = None
    applied_profile_id: int | None = None
    applied_profile_hash: str | None = None
    requested_code: tuple | None = None
    applied_code: tuple | None = None
    applied_read: bool | None = None
    applied_valid: bool | None = None
    requested_ua: tuple | None = None
    applied_ua: tuple | None = None
    applied_at_stop: tuple | None = None
    stop_evidence: str | None = None
    stop_integrity: str | None = None     # exactly what the device sent
    stop_match: bool | None = None
    stop_slot: int | None = None
    stop_error: int | None = None
    legacy_requested_ma: tuple | None = None
    iref_ua_per_lsb: int | None = None
    iref_code_default: int | None = None
    iref_code_max: int | None = None
    unknown: tuple = ()                   # ((key, value), ...) keys this host does not know, kept verbatim
    ignored: tuple = ()                   # keys removed by FIX1 that an older device still sends
    warnings: tuple = ()
    present: frozenset = frozenset()      # known keys that were sent
    text: str = ""                        # the received text (for the sidecar and for debugging)

    @property
    def stop_integrity_effective(self) -> str:
        """What the host will write down. The device value is used only when it agrees with the evidence
        class documented by the firmware; otherwise (absent, unknown, contradictory) it is UNVERIFIED.
        The host never turns missing evidence into OK."""
        evidence = self.stop_evidence
        if evidence not in DOCUMENTED_EVIDENCE:
            return "UNVERIFIED"
        expected = INTEGRITY_OF.get(evidence, "UNVERIFIED")
        return self.stop_integrity if self.stop_integrity == expected else "UNVERIFIED"

    @property
    def integrity_blocked(self) -> bool:
        return self.stop_integrity_effective == "BLOCKED"


def _hex_id(value: str) -> int | None:
    return int(value, 16) if _HEX_ID.fullmatch(value) else None


def _codes(value: str, *, hi: int) -> tuple | None:
    parts = value.split(",")
    if len(parts) != IREF_SLOTS or not all(p.isdecimal() and len(p) <= 5 for p in parts):
        return None
    numbers = tuple(int(p) for p in parts)
    return numbers if all(0 <= n <= hi for n in numbers) else None


def parse_profile(ext: bytes | str) -> ProfileReport:
    """Decode the `get-profile` text. Never raises: whatever cannot be used is reported in `warnings`, and a
    reply that is not a profile report at all becomes `supported=False` with a `reason`."""
    if isinstance(ext, str):
        text = ext
    else:
        text = bytes(ext).decode("ascii", errors="replace")
    shown = text[:MAX_TEXT]
    if not shown.strip():
        return ProfileReport(False, "empty_reply", warnings=("empty get-profile reply",), text="")
    tokens = shown.split(" ")
    head, _, version = tokens[0].partition("=")
    if head != "v4profile" or not version:
        return ProfileReport(False, "unrecognised_reply", text=shown[:200],
                             warnings=("reply does not start with v4profile=",))
    if version == "0":
        reason = next((t.partition("=")[2] for t in tokens[1:] if t.startswith("reason=")), "unknown")
        return ProfileReport(False, reason[:40] or "unknown", text=shown[:200])
    if version != "1":
        return ProfileReport(False, f"unknown_version_{version[:8]}", text=shown[:200],
                             warnings=(f"v4profile={version[:8]} is newer than this host understands",))

    values: dict[str, str] = {}
    warnings: list[str] = []
    unknown: list[tuple] = []
    ignored: list[str] = []
    for token in tokens[1:]:
        if not token:
            continue
        key, eq, value = token.partition("=")
        if not eq or not key:
            warnings.append(f"token without key=value: {token[:24]!r}")
            continue
        if key in values or any(k == key for k, _ in unknown):
            warnings.append(f"duplicate key {key[:32]}")
            continue
        if key in KNOWN_KEYS:
            values[key] = value
        elif key in _REMOVED_KEYS:
            ignored.append(key)
        elif len(unknown) < MAX_EXTRA:
            unknown.append((key[:48], value[:120]))
    fields: dict = {}
    for key, value in values.items():
        parsed = None
        if key in _ENUM_KEYS:
            parsed = value
            if value not in _ENUM_KEYS[key]:
                warnings.append(f"unknown {key} value {value[:32]!r}")
        elif key in _BOOL_KEYS:
            parsed = {"0": False, "1": True}.get(value)
        elif key in _INT_KEYS:
            parsed = int(value) if _INT.fullmatch(value) else None
        elif key in _ID_KEYS:
            parsed = _hex_id(value)
        elif key in _HASH_KEYS:
            parsed = value if _HEX_HASH.fullmatch(value) else None
            if value == "none":
                continue                      # a legitimate "no hash": the field stays None
        elif key in _CODE_KEYS:
            parsed = _codes(value, hi=0xFFFFFFFF if key.endswith("_ua") else 65535
                            if key == "legacy_requested_ma" else 255)
        elif key in _NAME_KEYS:
            parsed = value[:40]
        if parsed is None and key not in _ENUM_KEYS:
            warnings.append(f"unusable value for {key}")
        fields[key] = parsed
    # a code outside the accepted 0..222 domain is reported by the device as it is; flag it, do not clamp
    for key in ("profile_code", "requested_code", "applied_code", "applied_at_stop", "default_code"):
        codes = fields.get(key)
        if codes and any(c > IREF_CODE_MAX for c in codes):
            warnings.append(f"{key} has a code above {IREF_CODE_MAX}")
    evidence = fields.get("stop_evidence")
    integrity = fields.get("stop_integrity")
    if evidence in DOCUMENTED_EVIDENCE and integrity in INTEGRITY \
            and integrity != INTEGRITY_OF.get(evidence, "UNVERIFIED"):
        warnings.append(f"stop_integrity={integrity} contradicts stop_evidence={evidence}")

    rename = {"profile_state": "state", "profile_armed": "armed", "last_profile_reject": "last_reject",
              "profile_source": "source"}
    kwargs = {rename.get(k, k): v for k, v in fields.items()}
    return ProfileReport(True, None, unknown=tuple(unknown), ignored=tuple(ignored),
                         warnings=tuple(warnings), present=frozenset(values), text=shown, **kwargs)


def args_set_profile(profile_id: int, profile_hash: bytes, codes) -> bytes:
    """set-profile (0x0103) argument, 56 bytes: id u32 LE (not 0), hash[32] (opaque), code[20] (1..222).

    Protocol layer only: nothing in the host sends it by itself. The device refuses (INVALID_ARG) an id of 0
    and any code of 0 or above 222; the host refuses the same values locally and never clamps."""
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or not 1 <= profile_id <= 0xFFFFFFFF:
        raise ValueError("profile_id must be 1..0xFFFFFFFF")
    if not isinstance(profile_hash, (bytes, bytearray)) or len(profile_hash) != PROFILE_HASH_BYTES:
        raise ValueError(f"hash must be {PROFILE_HASH_BYTES} bytes")
    codes = list(codes)
    if len(codes) != IREF_SLOTS:
        raise ValueError(f"exactly {IREF_SLOTS} codes")
    for code in codes:
        if isinstance(code, bool) or not isinstance(code, int) or not IREF_CODE_MIN <= code <= IREF_CODE_MAX:
            raise ValueError(f"every code must be {IREF_CODE_MIN}..{IREF_CODE_MAX} (no clamping)")
    return struct.pack("<I", profile_id) + bytes(profile_hash) + bytes(codes)


def _list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def encode_profile_text(*, state="NONE", armed=False, profile_id=0, profile_hash=None, profile_code=None,
                        last_reject="NONE", reject_slot=-1, default_code=None, source="NONE",
                        start_result="NONE", mismatch_slot=-1, applied_profile_id=0,
                        applied_profile_hash=None, requested_code=None, applied_code=None,
                        applied_read=False, applied_valid=False, applied_at_stop=None,
                        stop_evidence="NONE", stop_integrity=None, stop_match=None, stop_slot=-1,
                        stop_error=0, legacy_requested_ma=None, iref_ua_per_lsb=225,
                        iref_code_default=133, iref_code_max=222, extra: str = "",
                        legacy_stop_read: bool | None = None) -> bytes:
    """The FIX1 text exactly as the firmware builds it (simulators and tests only). `extra` appends unknown
    keys; `legacy_stop_read` writes the pre-FIX1 `stop_read` key where FIX1 sends stop_evidence."""
    zeros = [0] * IREF_SLOTS
    default_code = default_code if default_code is not None else [133] * IREF_SLOTS
    requested_code = requested_code if requested_code is not None else zeros
    applied_code = applied_code if applied_code is not None else zeros
    applied_at_stop = applied_at_stop if applied_at_stop is not None else zeros
    legacy = legacy_requested_ma if legacy_requested_ma is not None else zeros
    integrity = stop_integrity if stop_integrity is not None else INTEGRITY_OF.get(stop_evidence, "UNVERIFIED")
    match = stop_match if stop_match is not None else stop_evidence == "VALID_MATCH"
    ua = lambda codes: _list(int(c) * 225 for c in codes)  # noqa: E731 (nominal 225 uA per code)
    out = [f"v4profile=1 profile_state={state} profile_armed={int(bool(armed))} "
           f"profile_id=0x{profile_id:08X} profile_hash={profile_hash.hex() if profile_hash else 'none'}"]
    if armed:
        out.append(f" profile_code={_list(profile_code if profile_code is not None else default_code)}")
    out.append(f" last_profile_reject={last_reject} reject_slot={reject_slot} default_code={_list(default_code)}"
               f" profile_source={source} start_result={start_result} mismatch_slot={mismatch_slot}"
               f" applied_profile_id=0x{applied_profile_id:08X} applied_profile_hash="
               f"{applied_profile_hash.hex() if applied_profile_hash else 'none'}"
               f" requested_code={_list(requested_code)} applied_code={_list(applied_code)}"
               f" applied_read={int(bool(applied_read))} applied_valid={int(bool(applied_valid))}"
               f" requested_ua={ua(requested_code)} applied_ua={ua(applied_code)}"
               f" applied_at_stop={_list(applied_at_stop)}")
    if legacy_stop_read is not None:
        out.append(f" stop_read={int(legacy_stop_read)} stop_match={int(bool(match))}")
    else:
        out.append(f" stop_evidence={stop_evidence} stop_integrity={integrity} stop_match={int(bool(match))}"
                   f" stop_slot={stop_slot} stop_error={stop_error}")
    out.append(f" legacy_requested_ma={_list(legacy)} iref_ua_per_lsb={iref_ua_per_lsb}"
               f" iref_code_default={iref_code_default} iref_code_max={iref_code_max}")
    text = "".join(out) + (f" {extra}" if extra else "")
    return text.encode("ascii")


EVENT_LIMIT = 500        # the RAW sidecar keeps 512 characters per event text


def profile_event_text(report: ProfileReport, *, phase: str, boot_id: int | None = None,
                       session_id: int | None = None, plan_epoch: int | None = None) -> str:
    """One compact sidecar line (<= EVENT_LIMIT characters). `phase` says when it was read. The hash fields are
    written in full; code vectors go to `profile_codes_event_text`."""
    head = [f"phase={phase}"]
    if boot_id is not None:
        head.append(f"boot=0x{boot_id:X}")
    if session_id:
        head.append(f"session={session_id}")
    if plan_epoch is not None:
        head.append(f"plan_epoch={plan_epoch}")
    if not report.supported:
        text = " ".join(head + ["supported=0", f"reason={report.reason}"] +
                        [f"warning={w}" for w in report.warnings[:2]])
        return text[:EVENT_LIMIT]

    def fmt(value, none="-"):
        return none if value is None else str(value)

    def hexid(value):
        return "-" if value is None else f"0x{value:08X}"

    parts = head + [
        "supported=1", f"state={fmt(report.state)}", f"armed={fmt(None if report.armed is None else int(report.armed))}",
        f"id={hexid(report.profile_id)}", f"hash={fmt(report.profile_hash, 'none')}",
        f"source={fmt(report.source)}", f"start_result={fmt(report.start_result)}",
        f"mismatch_slot={fmt(report.mismatch_slot)}", f"applied_id={hexid(report.applied_profile_id)}",
        f"applied_hash={fmt(report.applied_profile_hash, 'none')}",
        f"applied_valid={fmt(None if report.applied_valid is None else int(report.applied_valid))}",
        f"stop_evidence={fmt(report.stop_evidence)}",
        f"stop_integrity={report.stop_integrity_effective}", f"stop_slot={fmt(report.stop_slot)}",
        f"stop_error={fmt(report.stop_error)}"]
    if report.integrity_blocked:
        parts.insert(1, "DATA_INTEGRITY_BLOCKED")
    if report.unknown:
        parts.append("unknown_keys=" + ",".join(k for k, _ in report.unknown[:6]))
    if report.warnings:
        parts.append(f"warnings={len(report.warnings)}")
    text = " ".join(parts)
    return text if len(text) <= EVENT_LIMIT else text[:EVENT_LIMIT - 1] + "…"


def profile_codes_event_text(report: ProfileReport) -> str | None:
    """The requested / applied / at-stop code vectors, only when the device sent a usable set."""
    if not report.supported or not report.requested_code or not any(report.requested_code):
        return None                      # all zeros = no START has requested anything yet
    parts = ["codes", f"requested={_list(report.requested_code)}"]
    if report.applied_code:
        parts.append(f"applied={_list(report.applied_code)}")
    if report.applied_at_stop and any(report.applied_at_stop):
        parts.append(f"at_stop={_list(report.applied_at_stop)}")
    return " ".join(parts)[:EVENT_LIMIT]


def profile_summary(report: ProfileReport | None, *, failure: str = "") -> str:
    """Plain lines for the status area (English; the UI shows them verbatim)."""
    if report is None:
        return failure or "LED profile: not read yet"
    if not report.supported:
        why = {"unsupported": "this firmware reports no calibration profile",
               "overflow": "the firmware report overflowed (nothing usable)",
               "empty_reply": "the device sent an empty reply",
               "unrecognised_reply": "the device reply was not a profile report"}.get(report.reason, report.reason)
        return f"LED profile: not available ({why}). The device uses its own defaults."
    lines = [f"LED profile: state {report.state or '?'} · source {report.source or '?'} · "
             f"start result {report.start_result or '?'}"]
    if report.armed:
        lines.append(f"Armed profile id 0x{report.profile_id or 0:08X} hash {report.profile_hash or 'none'}")
    if report.applied_profile_id:
        lines.append(f"Applied profile id 0x{report.applied_profile_id:08X} · applied valid "
                     f"{'yes' if report.applied_valid else 'no'}")
    lines.append(f"Last stop: evidence {report.stop_evidence or 'not reported'} · integrity "
                 f"{report.stop_integrity_effective}"
                 + (" · DATA INTEGRITY BLOCKED" if report.integrity_blocked else ""))
    if report.warnings:
        lines.append("Parse warnings: " + "; ".join(report.warnings[:3]))
    if report.unknown:
        lines.append("Unknown keys ignored: " + ", ".join(k for k, _ in report.unknown[:6]))
    lines.append("Reported by the firmware; the host has not verified it. Physical LED current is not measured.")
    return "\n".join(lines)
