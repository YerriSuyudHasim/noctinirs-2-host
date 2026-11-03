# Historical host reference snapshots

These owner-supplied applications are archived design references only. They are not the active NoctiNIRS 2 host, and their legacy protocol is not the current firmware contract. No current-device compatibility or hardware validation is claimed. Do not start the bridge against powered hardware without explicit owner authorization.

## Layout

All three supplied snapshots are retained, including the nested copies:

- `legacy/liveview/offline/`
- `legacy/liveview/white-blue/offline/`
- `legacy/liveview/white-blue/white-blue/offline/`

Wrapper names were shortened to avoid GitHub Desktop's Windows `Filename too long` error. Mapping from the original directory names:

| Original component | Current component |
| --- | --- |
| `Noctinirs host (old-for reference only)` | `legacy` |
| `noctinirs-live view old` | `liveview` |
| `noctinirs-fnirs-liveview-white-blue-ui` | `white-blue` |
| `noctinirs-fnirs-liveview-offline` | `offline` |

Original source files are preserved; the only content changes in the supplied snapshots replace Wi-Fi passwords in their README files with `<DEVICE_WIFI_PASSWORD>`. Obtain device credentials separately, and never commit them or recorded subject data.

## Dependencies and offline use

Installed `node_modules` folders are intentionally excluded. In the chosen snapshot directory, use `npm ci --ignore-scripts` with internet access to restore the existing lockfile before attempting offline use. The existing dependency is `ws` (MIT; exact version and integrity recorded in each package-lock.json); no dependency versions were changed. Original launch instructions describing a bundled ZIP assume dependencies are already present.

Browser files in `public/lib` are source, not installed Python libraries, and must remain tracked. The scoped ignore exceptions here override the root Python `lib/` rule.

Keep checkout paths short on Windows. The directory changes do not require system-wide long-path settings or a Git global configuration change. Path shortening alone cannot guarantee compatibility with arbitrarily deep checkout locations.