# Stage C offline status

Codex is the implementation and revision author, assigned by the Owner in the
offline Stage C task. Nori coordinates. AGENT07 (Independent Reviewer / Grok
Bot) is the assigned non-author reviewer. Review is **PENDING**. See the
[handoff](handoff.md) for the review target and procedure.

- Branch `feat/20-raw-recorder-offline` in the Owner-designated host clone at
  `C:/Users/doeki/Documents/CODEX/2026-11-07/kerjakan-pr-a-pada-repo-yerrisuyudhasim/noctinirs-2-host`.
- Starting HEAD `5fb8abd842214e316163134740bd8dd772b4e3ce`, the B closeout.
  The B code/test baseline `7738593b3ea6e15d82d417b3954e1b6c24756ee4` matched
  `src/` and `tests/` at that HEAD. The Owner selected this offline baseline.
  The current remote default branch is unverified.
- The only initial working change was the approved `project.yaml` metadata.
  Its bytes (SHA-256 `9cf1ca7c855f9a1d4992206a5a1cbb2efbc17706410e4195f4e7d13ef177d465`)
  were preserved and kept unstaged. Stage C implementation and its review
  notes are separate commits.
- Candidate code/test SHA `eb890b93c17fb6af04b03d641bc92e0806c34e1e`.
  Contract target `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, image
  `firmware/v4-idf61`, ADR-0004 Proposed rev 4 §13 / optical draft v0.4.3.
- Author status **READY FOR LOCAL INDEPENDENT REVIEW**. No integration or
  hardware authorization is implied. Online CI and remote freshness are
  **NOT RUN**. The Owner's instructions cover this local branch and scope;
  the hub registration was read from local committed `main` and the hub
  working policy draft was excluded from the applicable policy.

The candidate adds RAW binary capture, a sample CSV, replay and diagnostics
in the UI-independent core. It retains the OPT2 path. UI expansion, USB,
auto Wi-Fi, firmware changes, OPT2 firmware production, physiological
processing, hardware operation and Stage D are outside scope.

All B residuals N1–N7 and the unresolved PR-A recorder `PermissionError` carry
forward as filed in the [B review](../pr-b/review.md). Non-recurrence in a new
run provides no closure. Stage B's prior technical approval covers its own
code SHA only. Local author evidence and governance disposition are separate.
