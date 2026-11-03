# NoctiNIRS white/blue live-view prototype

This UI pass introduces:

- White scientific-instrument theme with blue typography, borders, and grids.
- Welcome screen with device state, processing summary, and probe preview.
- Four-step setup flow: Device, MBLL, Short Separation, Session.
- Live layout aligned to the physical probe:
  - CH1–CH8 in the left hemisphere column.
  - CH9–CH16 in the right hemisphere column.
- Sixteen simultaneous compact plots.
- ECG-style sweep plotting:
  - Static CSS grid.
  - One new segment drawn per sample.
  - Narrow erase gap ahead of the live cursor.
  - Full redraw only after resize, scale/window change, or explicit clear.
- Hb, optical-density, and intensity display modes.
- Channel details with explicit names such as `CH1_I735`, `CH1_I850`, `CH1_OD735`, and `CH1_OD850`.
- MBLL parameter controls and short-separation modes: disabled, 5 mm, 8 mm, or both.
- Demo mode for UI evaluation without hardware.

## Current live-processing behavior

The live prototype pairs 735/850 intensity values using the geometry library, creates a user-configurable intensity reference baseline, converts the data to optical density, then applies MBLL. When short-separation correction is enabled, rolling OD-domain regression is used before MBLL conversion.

Dark-current profile selection is intentionally marked as a future connection point in the setup UI. The firmware/raw stream is unchanged.

## Run

```bash
npm install
npm start
```

Open `http://localhost:8000`. Use **Preview demo** to inspect the 16-channel sweep display without a device.
