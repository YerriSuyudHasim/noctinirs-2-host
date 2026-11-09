# Stage D2 author responses

There are no reviewer findings yet (AGENT07 PENDING). This file records the
author's own decisions and the defects found by the author's tests, so the
reviewer can check them.

## Decisions taken from the Owner's brief

| Decision | Author's reading |
| --- | --- |
| Screens follow the legacy UI | 3 screens. Setup is one screen with 4 steps. Session step includes the readiness summary. |
| Start press does everything for a recorded run | prepare folder, arm, START, in order, on the session worker. The old explicit Arm and Finalize buttons are gone from the UI. The run API for them remains and its Stage D tests still pass. |
| Auto finalize | After the device reports STOPPED. Disconnect and close keep the Stage D cleanup path. |
| X numbering | Highest `record_X` in the folder + 1, counting any known suffix, including `.preview.json`. A name taken between the check and the arm is still refused by the C recorder's exclusive creation; the operator sees the error and presses Start again. |
| Preview metadata | The sidecar writer of Stage C was not changed. Provenance is in `settings_at_start.preview` (written at arm), in events, and in `<stem>.preview.json`. |
| UI constants | The default ε/DPF values the Owner listed are in the core and are shown and editable in the forms. They carry the status `unverified`. |
| Evidence thresholds | The brief asks for "evidence the LED is on". RAW has only the scheduled phase class. The author used the observed ON−OFF step with a provisional threshold (50 counts). The number is the author's placeholder, not a measured or contract value. |

## Defects found by the author's tests and fixed before the commit

1. **Start rejected right after a recording ended.** The destination preview
   refresh occupied the disk job and `start_recorded` refused with "Wait for the
   recorder…". Fixed: the start request is queued and launched by `tick` as soon
   as the disk job is free.
2. **Background refresh hid real results.** The refresh used the same pending
   mechanism as user actions, so it replaced a visible result (the SAFE_OFF
   report) with "Working: preview destination" and cleared errors. Fixed: it is
   a background action that is neither shown nor clears errors.
3. **Test subprocess without PYTHONPATH** in the Stage C recovery test. Fixed in
   the test.
4. An early MBLL comparator run failed at 1e-12 because the random ε were
   ill conditioned. The claim and the domain were restated rather than the
   threshold loosened (see test-evidence).
5. First Live layout was too tall to read the plots. Re-laid out and checked on
   the real Windows platform.

## Open for the reviewer

- Whether a slot occurrence started right after stream start should stay
  `partial` (current, conservative: one slot per start or gap is lost).
- Whether the leak check should use the same threshold as the step check.
- `README.md` still describes the six-page wizard; it is a pre-existing dirty
  file and was not edited.
