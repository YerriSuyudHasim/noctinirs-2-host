# NoctiNIRS geometry, MBLL, and short-separation library

The processing library is intentionally separate from the UDP protocol and hardware acquisition code.
It has not yet been connected to the dashboard UI.

## Files

```text
public/lib/fnirs-geometry.js
public/lib/fnirs-mbll.js
public/lib/fnirs-short-separation.js
public/lib/noctinirs-fnirs.js
```

## Canonical channel naming

```text
CH1 = L2–PD3
CH1_I735
CH1_I850
CH1_OD735
CH1_OD850
CH1_HbO
CH1_HbR
CH1_HbT
```

Corrected outputs use explicit suffixes:

```text
CH1_HbO_RAW
CH1_HbR_RAW
CH1_HbO_CORR
CH1_HbR_CORR
```

## Short channels

```text
SC1L = L1–PD2 = 5 mm
SC2L = L1–PD3 = 8 mm
SC1R = L6–PD1 = 5 mm
SC2R = L6–PD0 = 8 mm
```

The selectable regression modes are:

```text
none
5mm
8mm
both
```

`5mm` and `8mm` use one short regressor from the same hemisphere. `both` uses the paired 5-mm and 8-mm regressors. In `auto` regression mode, a single short channel uses OLS while `both` uses ridge regression to reduce instability from correlated short regressors.

## Geometry example

```js
import {
  extractNamedIntensities,
  getChannel,
} from "./lib/noctinirs-fnirs.js";

const intensities = extractNamedIntensities(decodedFnirsFrame);
console.log(intensities.CH1_I735, intensities.CH1_I850);
console.log(getChannel("CH1"));
```

## DPF configuration

Manual:

```js
const mbllConfig = {
  dpfMode: "manual",
  dpf735: 6.2,
  dpf850: 5.2,
};
```

Age-based:

```js
import { resolveDpf } from "./lib/noctinirs-fnirs.js";

const dpf = resolveDpf({
  dpfMode: "age-based",
  ageYears: 28,
});
```

## MBLL from optical density

```js
import { mbllFromOpticalDensity } from "./lib/noctinirs-fnirs.js";

const hb = mbllFromOpticalDensity({
  od735: CH1_OD735,
  od850: CH1_OD850,
  distanceMm: 30,
  dpf735: 6.2,
  dpf850: 5.2,
});
```

The output is `HbO`, `HbR`, and `HbT` in micromolar change.

## MBLL with short-separation regression

```js
import { mbllWithShortSeparation } from "./lib/noctinirs-fnirs.js";

const result = mbllWithShortSeparation({
  channelId: "CH1",
  distanceMm: 30,
  dpf735: 6.2,
  dpf850: 5.2,
  longOD735: CH1_OD735_series,
  longOD850: CH1_OD850_series,
  short5OD735: SC1L_OD735_series,
  short5OD850: SC1L_OD850_series,
  short8OD735: SC2L_OD735_series,
  short8OD850: SC2L_OD850_series,
  regression: {
    enabled: true,
    mode: "both",       // 5mm | 8mm | both
    method: "auto",     // auto | ols | ridge
    ridgeLambda: 1e-3,
    standardizeRegressors: true,
    minimumSamples: 30,
  },
});

console.log(result.raw.HbO);
console.log(result.corrected.HbO);
```

## Live preview

`RollingShortSeparationRegressor` is provided for a rolling online preview. It should not replace offline reprocessing of the saved raw data. Create one instance per long channel and per wavelength.

## Validation

```bash
npm test
npm run check
```

## Implementation references

- Scholkmann F, Wolf M. General equation for the differential pathlength factor of the frontal human head depending on wavelength and age. J Biomed Opt. 2013;18(10):105004. DOI: 10.1117/1.JBO.18.10.105004.
- Moaveni hemoglobin extinction table hosted by the Oregon Medical Laser Center: https://omlc.org/spectra/hemoglobin/moaveni.html
- Gagnon L, Yücel MA, Boas DA, Cooper RJ. Further improvement in reducing superficial contamination in NIRS using double short separation measurements. NeuroImage. 2014;85:127–135. DOI: 10.1016/j.neuroimage.2013.01.073.
