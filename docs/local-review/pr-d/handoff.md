# Offline handoff to AGENT07

From Codex (GPT-6, implementation author, agent /root), acting under the
Owner's local Stage D assignment and follow-up. To independent non-author
AGENT07. Candidate `b4427e50e968e6905a8b24e6bc8c573a206942c3`.

1. Confirm the code SHA, ancestry from `c3c9d22b5f859dd8e3ae56bdcf3c6221c8f99116`,
   and the notes-only delta. Preserve the four pre-existing files listed in
   [status](status.md). If needed, review in a separate local copy/worktree
   without carrying or staging the Owner's changes.
2. Read the [implementation](implementation.md), [RAW format](../../raw-recording-format.md)
   and pinned firmware paths in `config/reference_pins.json`. Check that the
   quarantine blocks all regular transmissions, that a late ACK and unknown
   RAW never confer ownership, and that the explicit probe and SAFE_OFF keep
   boot, source, request and session validation. Inspect the on-device
   lease refresh and the response cache against the fake.
3. Review the worker and disk ownership, stopped and other-owner gates,
   file exclusivity, boot/link loss paths, session cleanup, and provenance.
   Check the [original failure and exact-head evidence](test-evidence.md).
   Run only local synthetic/loopback checks. If the pinned UI environment
   is already available, run the Qt-only suite and capture the fake screens
   using the evidence path described there. Report unavailable checks
   explicitly.
4. File independent results and findings in [review](review.md). Keep earlier
   failure logs and prior residuals. Review revisions at their exact SHAs.

Stage D is BLOCKED on local Qt execution/build evidence. Independent review
is PENDING. Integration, hardware use and the [bench procedure](bench-procedure.md)
remain separate Owner decisions. The baseline branch is available for rollback
without touching Owner/contributor working files.
