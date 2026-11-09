# Stage D3 reviewer record

**AGENT07 independent review: PENDING.**

The author (Claude, Sonnet 5.5) cannot review this change. Per AGENTS.md and hub section 5.1 the
reviewer must be from a different model family. Nothing here is a review result.

## Requested scope

Candidate: the D3 code/tests commit on `feat/20-host-preview-d3-offline` (see the
[handoff](handoff.md) for the SHA), base `3a343a6`. **D3 sits on top of the battery change
(`d0b3d2b`) and D2 (`c756a73`), none of which has been reviewed.** A verdict on those is a
prerequisite for merging this one.

1. `preview/quality.py` and `preview/pipeline.py`: the rules for invalid, warning and
   uncorrected; the online sigma; the ambient and saturation tests; the per-AIN leak rule;
   whether any path can still turn a missing or invalid value into 0.
2. The counts after SSR and MBLL, the loss counters and the provenance fields.
3. The SCR fallback: a long channel is shown without correction when the short regressor is
   unusable. Is the explicit label enough to avoid a silent raw/corrected mix?
4. The replay evidence (before/after tables) and the stated tuning limits: the constants were
   tuned on one bench recording that the report calls not valid as an optical baseline.
5. Layout and sizing: the splitter and scroll areas, the navigation row at 1280 × 720,
   1366 × 768, maximized and fullscreen, the stretching plots, the robust auto scale with red
   clamp marks.
6. Unchanged areas stay unchanged: protocol, RAW parser, routing, recording, START/STOP/SAFE_OFF,
   heartbeat, battery, MBLL formula, ε/DPF, geometry.

## Findings

None recorded yet. Author notes are in [responses](responses.md) and the [handoff](handoff.md).
