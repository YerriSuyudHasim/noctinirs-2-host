# Stage D4 · implementation

## Design system (new package `src/noctinirs_host_ui/design/`)
- `tokens.py`: colour, spacing, radius, type and motion tokens for light and dark (contrast checked by tests).
- `qss.py`: one `string.Template` style sheet generated from the tokens.
- `manager.py`: `ThemeManager` (`set_theme`, `toggle`, `set_reduce_motion`, `ms()` returns 0 when motion is reduced, `build_palette`), persisted in QSettings.
- No new dependency. Fusion style under the generated style sheet.

## Widgets (`widgets.py`)
`ElidedLabel` (ellipsis + lazy tooltip), `StatusChip` (colour animates), `Section` (animated fold), `SmoothScrollArea`, `FadeStack` (screen cross-fade via opacity effect).

## Live screen (`preview_panel.py`, `engineering.py`)
- `channel_order(geometry)`: left hemisphere then right; per hemisphere the long channels by number, then the short channels. Derived from the geometry, not hard-coded.
  Final order: Left CH1–CH8, SC1L, SC2L · Right CH9–CH16, SC1R, SC2R (20 rows).
- One row per channel: HbO and HbR in one plot with legend, status text, per-row autoscale (percentiles 2–98, margin, clamped points marked, Y hysteresis); optional shared scale lock; X axes linked, only the footer shows the time axis (aligned with an invisible left axis).
- Exactly 3 rows fill the visible area at any size (`VISIBLE_ROWS=3`); the rest scrolls smoothly; hemisphere dividers between groups.
- Sweep display with erase gap; peak (min/max per bin) downsampling that keeps spikes and NaN gaps; 25 Hz precise timer; only visible, dirty rows are redrawn; timer stops while the panel is hidden.
- The pyqtgraph Y grid was replaced by a dashed zero line (it doubled the paint cost, measured).
- Action bar on top holds START / STOP / navigation so they stay visible when the left sidebar collapses (animated width).
- `EngineeringWindow` now uses `WA_DeleteOnClose`, like the old window, so closed windows are not restyled.

## Not touched
Signal processing (SSR/MBLL/quality rules), protocol, battery logic, recording format and file names.
