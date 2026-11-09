# Stage D3 author responses

No reviewer findings yet (AGENT07 PENDING). This file records the author's decisions, the points
where the result differs from the brief, and the problems the author's own checks found.

## Decisions

| Topic | Decision |
| --- | --- |
| Leak decision | Per same-side AIN against the opposite-side steps of the same slot. Invalid needs a known sigma (8 occurrences) so a cold start cannot throw away data. The warning level is 4 σ, not 3 σ: at 3 σ the replay flagged about a fifth of the slots, which made the plots unreadable. |
| Sigma | The larger of the within-slot standard error and the between-occurrence robust sigma. The bench report measured the second kind (successive differences), which includes ambient and common-mode noise that the within-slot value cannot see. |
| "Only `I ≤ 0` is invalid" | Followed. A channel with no signal at all can therefore appear as a flagged value (`LOW_SIGNAL`) half of the time instead of as a gap. Two earlier tests that expected only gaps were changed to expect "gap or flagged". |
| Short channel as regressor | A `LOW_SIGNAL` short channel is rejected as a regressor, like an invalid one, because a regressor made of noise adds noise. The brief did not say this; it is the author's rule. |
| Uncorrected | Shown with `ssr_state = UNCORRECTED` and the reason, in the detail section, in the tooltip and with a violet marker. The earlier "no value while the regression warms up" rule is replaced by this, as the brief asked. |
| Robust auto scale | Added because a single bench spike (±100 µM after the probe was lifted) flattened the plot. Clamped points are marked in red. "Full range" is the old behaviour. |
| Pairing with the old UI | Layout, sections and the detail card follow `index.html` / `styles.css` in spirit (sidebar with settings, hemisphere board, selected-channel card). The code is new Qt code; nothing was copied. |
| Replay source | `.raw.bin`, not the `.raw.csv` (a projection of the same datagrams). The before run matches the analysis numbers exactly. |
| Geometry | Unchanged. The unverified 5/8 mm labels are only flagged in the UI text. |

## Where the result differs from the brief

1. The target "about 77 % of long channels valid with SCR off" was not met: 74.1 %. The analysis
   target came from a model that also kept the ambient event frames; D3 invalidates them
   (`AMBIENT_HIGH`) as asked. Leak and `I ≤ 0` frames still remove about 4–5 % each (see the test
   evidence). No constant was loosened to hit the number.
2. "Remaining gaps only baseline, lost packets and probe events" is not true: `LEAK_SUSPECT` and
   `NO_LED_STEP` remain.
3. The battery label stays in the header (visible on every screen, as the battery task required)
   instead of moving into the left panel.

## Problems found by the author's checks (fixed before the commit)

1. A first Live layout clipped the buttons of the left panel: a form row wider than the panel was
   cut because horizontal scrolling is off. Fixed with wrapped form rows and compact combo boxes.
2. The graph panel inherited the dark system palette (unreadable on dark mode). It now has its
   own white background.
3. The last plot row was squashed because it carries the time axis; it now gets a larger row
   stretch.
4. The first replay screenshot showed all channels as invalid at the end because the last frame
   of a flushed recording is cut. The screenshot script now skips incomplete frames; the panel
   still shows whatever frame the live stream delivers last.
5. The full suite took 588 s, close to a ten-minute tool limit. It was run in the background. The
   Qt tests are the slow part.

## Open for the reviewer

- Are the constants in `quality.py` acceptable as host-chosen defaults, or should any be a user
  setting?
- Should `LEAK_SUSPECT` use the same-side step of the specific wavelength, or the smaller of the
  two wavelengths (it uses each wavelength separately now)?
- Should frames cut by a packet loss at the edge of a slot be recovered (the current rule drops
  the partial slot)?
