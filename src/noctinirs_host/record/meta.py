"""Sidecar metadata: required fields and a checker (acceptance 3 of issue #33)."""

from __future__ import annotations

SCHEMA = "noctinirs.recording/1"
CONTRACT = "OPT2 layout 2, firmware ADR-0004 section 3.17 (status Proposed)"
OPTICAL_PIN = "e42f2bc9b453b223bf4557516f63fcceadfddbc8"
OPTICAL_CONTRACT = "OPTICAL_DATA_CONTRACT_DRAFT v0.4.3 (draft); OPT2 layout 2"

REQUIRED_FIELDS = (
    "schema", "complete", "host_version", "created_utc", "started_utc", "stopped_utc",
    "simulated", "evidence_class", "firmware_pin", "contract",
    "geometry.map_id", "geometry.sha256", "geometry.status",
    "gate_config_ids", "fs_hz", "led_current_ma", "session_ids", "events",
    "counters.frames_written", "counters.rows_written", "counters.queue_drops",
    "counters.rejected", "counters.not_frame", "counters.raw_packets_not_recorded",
    "counters.geometry_mismatch_frames", "csv_file", "csv_sha256", "csv_bytes",
    "host_sha", "host_sha_source", "firmware_pin_source", "optical_firmware_pin",
    "optical_contract", "firmware.declared_sha", "firmware.sha_source",
    "firmware.observed_info", "firmware.boot_id", "host_time_basis",
    "settings_at_start", "writer_error", "close_timed_out", "queue_capacity",
)


def validate_metadata(meta: dict) -> list[str]:
    """Names of required fields that are absent (empty list = complete)."""
    missing = []
    for dotted in REQUIRED_FIELDS:
        node = meta
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                missing.append(dotted)
                break
            node = node[part]
    return missing
