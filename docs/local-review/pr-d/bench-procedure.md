# Combined host–firmware Stage D bench procedure

**PLANNED / NOT RUN.** Owner operation after separate authorization and
review of the host candidate and bench setup. Evidence class is on-target
bench only for steps actually performed on the physical rig. Use a lab
fixture with no subject and agree on the local physical abort procedure,
operator, safe limits and observation method before powering the rig.

## Prepare and identify the rig

1. Create a new local results folder outside Git. Record date/time zone,
   operator, authorization, case ID, host OS, Python/Qt or package version,
   host code SHA, build command or package SHA-256, and geometry map ID,
   digest and draft status. Verify the approved configuration and build.
   For the source launcher with dependencies already installed:

   ```powershell
   $env:PYTHONPATH = 'src'
   $hostSha = 'b4427e50e968e6905a8b24e6bc8c573a206942c3'
   git diff --exit-code $hostSha -- src tests config packaging
   if ($LASTEXITCODE -ne 0) { throw 'Review the local code or configuration differences first.' }
   ./.venv/Scripts/python.exe -m noctinirs_host_ui --host-sha $hostSha
   ```

2. Use the installed image's available build/programming record to identify
   it. Record the firmware SHA, image/board, build configuration and evidence
   source. The compatibility target is `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`,
   image `firmware/v4-idf61`, ADR0004/1. Treat absent build provenance
   as unknown. Save the peer's INFO text and boot ID separately from the
   operator's declared SHA. Require an approved disposition for a mismatch.
3. Join the rig's approved Wi-Fi manually and confirm the supplied IPv4 and
   UDP port. Select the UDP device endpoint and enter them explicitly.
   Record endpoint, source type, initial reported state and fault code.
   Check that there is no other active owner. Preserve unknown diagnostic
   fields as unknown. Keep Wi-Fi credentials out of the results.

## Controlled normal capture and reconnect

1. Connect and verify INFO advertises RAW_ENGINEERING and `fs_allowed`.
   With reported STOPPED, choose an advertised and bench-approved nominal Fs,
   apply ENGINEERING and verify it was confirmed. Record the reported settings
   and start-of-run firmware and host diagnostics. Some RAW INFO counters
   can refer to an earlier run before START resets them.
2. Choose a fresh stem in the results folder. Enter the firmware declaration
   only when supported by the operator's records. Verify the readiness
   summary. Open the panel and verify no START request on entry, using the
   command log or passive packet evidence. Arm RAW recording, verify ARMED
   and reported STOPPED, then explicitly press Start acquisition.
3. Record STARTING/STREAMING observations with UTC and boot/session IDs.
   Save RAW preview and diagnostics at agreed points. Record host receipt
   clock and device first-sample anchor as separate time domains. Keep
   nominal optical Fs, measured optical Fs if independently available,
   and RAW ADC anchor estimates in separate fields.
4. Press Stop acquisition. Log the result and any STOPPING interval. Wait
   for a correlated firmware STOPPED report. Verify physical LED state
   using the pre-approved independent rig observation. Refresh and save
   end diagnostics before leaving ENGINEERING. Finalize the recorder and
   verify the sidecar's `complete`, hashes and errors. If STOP stays
   unconfirmed, use the agreed bench abort procedure.
5. Disconnect and reconnect explicitly. Verify the reported state, boot
   and capabilities again. Confirm that reconnect sends no START and no
   host adopts an existing session. For another authorized capture, repeat
   configuration and use a fresh stem. Log any authorized link interruption
   or reboot as a separate recovery case with a new boot/session context.

## Lost START reply recovery case

Run this case only with separate authorization for intentional reply loss
and an approved, observable test harness. Use a fresh stem and verify a
STOPPED rig before each case. If targeted suppression and independent
observations are unavailable, mark this case NOT RUN.

1. Preflight the harness on a fake peer. Configure it to suppress only the
   correlated START replies at host ingress for every bounded retry while
   leaving the outgoing request path and source endpoint intact. Arrange a
   passive trace with host send/receive and firmware-side evidence showing
   which START executed. Use independent LED observation and the Owner's
   abort mechanism. Disable any active diagnostic client that could renew
   the owner lease during the quiet interval.
2. Arm recording and press START once. Record the original request ID,
   byte-equivalence of retries, times, suppression rule and firmware side
   effect if observed. Expect the host to show unresolved START, no owned
   session, and a separate recorder result. Verify unknown-session RAW is
   excluded. If all START requests were lost, retain that result separately
   from the accepted-START/lost-reply case.
3. Capture the interval after the final retry. Check for zero automatic
   STATUS, HEARTBEAT or START from the host and log the peer's observed
   lease/stop transition and physical LED result. The firmware silence plus
   grace profile is approximately 4.5 seconds from the last accepted
   owner traffic. Account for network delay, transition time and clocks.
   Elapsed time alone is insufficient evidence of STOP.
4. After the UI quiet window, explicitly check START outcome once. Save
   the correlated reply, boot, session and state, or the timeout and
   still-unresolved UI. An active/unknown result or lost reply remains
   unresolved. Record any intentional suppression of this probe separately
   and verify another probe is gated by the quiet interval. If the run
   exceeds the approved abort conditions, end it via the agreed procedure.
5. In a separate authorized run, test explicit SAFE_OFF while the host has
   no known session. Confirm the UI warning first. The action can affect
   another owner's acquisition, so isolate the rig and verify permission.
   Record the correlated SAFE_OFF reply or the failure and unresolved
   outcome, along with the separate physical observation. Record any
   manual recovery needed by the Owner. Do not auto-clear or restart.

## Archive, reconcile and record the outcome

- Keep the original `*.raw.bin`, `*.raw.csv`, `*.csv` and `*.json`.
  Copy the UI command log and initial/final diagnostics, firmware logs,
  passive trace and instrument observations if authorized. Save settings,
  STOP/SAFE_OFF evidence, exception text and the exact test interval.
  Preserve incomplete files and replay their valid binary prefix separately.
  Compute an independent hash inventory after files are closed:

  ```powershell
  $results = Read-Host 'Absolute bench results folder'
  Get-ChildItem -LiteralPath $results -File |
    Get-FileHash -Algorithm SHA256 |
    Format-Table Hash, Path -AutoSize
  ```

- Compare observed sequence holes and late fills with persisted continuity,
  RAW queue drops, unconfirmed enqueues and writer errors. Compare
  initial/final firmware RAW buffer and send diagnostics only within
  supported counter scope and boot/run boundaries. The UI exposes INFO
  RAW fields and STATUS fields. FRAME send errors require separately
  approved firmware diagnostics, for example the pinned `nn2ctl netdiag`
  read path when the peer allows it. Record snapshots before and after
  acquisition, and avoid diagnostic traffic during the quiet case.
  Handle reset, wrap, snapshot timing and differing packet/sample units.
  Loss is assessed against the pre-agreed bench limits and evidence. Record
  its size, supported attribution and any unexplained remainder. Missing
  evidence stays unknown.
- Stop or abort on unsafe rig behavior, unexpected owner, unsupported
  configuration, firmware fault, write failure, uncontrollable link or
  unconfirmed stop beyond the agreed time. Record who stopped it, when,
  the abort mechanism, last firmware report, physical observation and
  file disposition. Avoid additional starts until the Owner has decided
  how to proceed.

| Case | Expected | Actual and time | Evidence files/hashes | Outcome |
| --- | --- | --- | --- | --- |
| Build, identity and Wi-Fi | Approved setup, provenance recorded | | | NOT RUN |
| Normal capture | Explicit START, STOP report, archive and diagnostics | | | NOT RUN |
| Reconnect | No automatic START or ownership adoption | | | NOT RUN |
| Lost START replies | Unresolved, quiet wire, explicit correlated recovery | | | NOT RUN |
| SAFE_OFF | Explicit confirmation and separate physical observation | | | NOT RUN |
| Loss accounting | Quantified, correlated where supported, unknowns recorded | | | NOT RUN |
