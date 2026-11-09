# Battery indicator · local status

**READY FOR LOCAL INDEPENDENT REVIEW. AGENT07 review PENDING. Not merged, not pushed.**

Claude (Sonnet 5.5, author) added a battery percentage indicator to the host. Everything was
done offline: no device, bench, acquisition, GitHub, CI, firmware or protocol change.

| Item | Local identity |
| --- | --- |
| Branch | `feat/20-host-battery-offline` (new, cut from `1c92772`) |
| **Depends on D2** | D2 code `c756a73165ec212df380607ea555f473a7edeb63`, D2 notes `1c92772cb3bea473dc6b6b25c28ef1b9c955933c`. D2 is not yet reviewed, so this review depends on it. |
| Battery code/tests | `d0b3d2b59099f73e4ec5b30b140fbec490abc369` |
| Battery notes | separate commit containing this folder |
| Firmware pin for the query | `519a96a26ede6517595d4f8a87cd64ab6b7a6fa5`, branch `feat/local-v4-battery-refresh`, read only |
| Firmware app SHA-256 reported by the Owner | `77a39a39f08701c7134ad4a162fdac0a144dac15d94f6b97b9e2ef8a4636cf78` (not checked by the host) |
| Contract | noctinirs-2 `docs/adr/0004-host-protocol.md` section 14, registry row 0x0033 (section 3.5), sections 3.3 and 3.4. Wire `ADR0004/1`, protocol_version 1, envelope_version 1. |
| Reference client | `tools/nn2ctl.py` @ 519a96a (`COMMANDS["get-battery"]`, `describe_reply`) |
| Evidence class | static, host/unit and simulation. No hardware. |

## Result

| Requirement | Status |
| --- | --- |
| `get_battery` 0x0033 request/reply, strict ASCII parser | Done. Golden bytes match the reference client byte for byte. |
| Pure `BatteryMonitor` (status, schedule) | Done (`control/battery.py`). |
| Query does not touch lease, session, state, control timeout counter | Done and tested. |
| Schedule: right after connect, 30 s idle, 60 s scanning, about 7 s after STOP (max 2 repeats), none while STARTING/STOPPING/uncertain START, stop after UNKNOWN_COMMAND | Done, tested with a fake clock. |
| Snapshot key `battery`; nothing in RAW files or sidecar | Done; a Qt test greps the sidecar. |
| Simulator, fake and UDP test peer support | Done. |
| Header label on every screen, no charging claim | Done. |
| Full suite, compileall, exe, screenshots | **720 passed, 0 skipped, 0 failed.** Exe built and launched. |

The exact device behaviour of the flashed 519a96a build was not observed.

Pre-existing working-tree files not in either commit and not modified:
`README.md`, `project.yaml`, `docs/local-review/pr-c/review.md`,
`docs/local-review/pr-d/review.md` (modified) and `docs/host-roadmap-offline.md`,
`docs/local-review/pr-d/recovery-2026-11-08-rc-disconnect.md` (untracked).

- [Implementation](implementation.md)
- [Test evidence](test-evidence.md)
- [Reviewer record (PENDING)](review.md)
- [Author responses](responses.md)
- [AGENT07 handoff](handoff.md)
