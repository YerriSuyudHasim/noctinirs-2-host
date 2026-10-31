// ============================================================
// Differential two-wavelength MBLL utilities for NoctiNIRS.
//
// Internal conventions:
//   - optical density uses natural logarithm: ln(Iref / I)
//   - distance is converted from mm to cm
//   - extinction coefficients are converted from decadic to
//     Napierian units by multiplying by ln(10)
//   - output concentration changes are expressed in micromolar
// ============================================================

const LN10 = Math.log(10);

export const EXTINCTION_DATASETS = Object.freeze({
  moaveni1970: Object.freeze({
    id: "moaveni1970",
    label: "Moaveni 1970",
    coefficientType: "decadic",
    unit: "M^-1 cm^-1",
    // 735 nm is linearly interpolated between tabulated 730 and 740 nm values.
    coefficients: Object.freeze({
      735: Object.freeze({ HbO: 420, HbR: 1510 }),
      850: Object.freeze({ HbO: 1060, HbR: 800 }),
    }),
  }),
});

export const DEFAULT_MBLL_CONFIG = Object.freeze({
  extinctionDataset: "moaveni1970",
  dpfMode: "manual",
  dpf735: 6.0,
  dpf850: 6.0,
  ageYears: null,
  baselineEstimator: "median",
  invalidValue: null,
});

function requireFinite(value, name) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) throw new TypeError(`${name} must be finite`);
  return numeric;
}

function requirePositive(value, name) {
  const numeric = requireFinite(value, name);
  if (numeric <= 0) throw new RangeError(`${name} must be greater than zero`);
  return numeric;
}

export function scholkmannWolfDpf(wavelengthNm, ageYears) {
  const wavelength = requirePositive(wavelengthNm, "wavelengthNm");
  const age = requireFinite(ageYears, "ageYears");
  if (age < 0) throw new RangeError("ageYears must be zero or greater");
  return 223.3
    + 0.05624 * (age ** 0.8493)
    - 5.723e-7 * (wavelength ** 3)
    + 0.001245 * (wavelength ** 2)
    - 0.9025 * wavelength;
}

export function resolveDpf(config = {}) {
  const merged = { ...DEFAULT_MBLL_CONFIG, ...config };
  if (merged.dpfMode === "age-based") {
    return Object.freeze({
      dpf735: scholkmannWolfDpf(735, merged.ageYears),
      dpf850: scholkmannWolfDpf(850, merged.ageYears),
      mode: "age-based",
    });
  }
  if (merged.dpfMode !== "manual") throw new RangeError("dpfMode must be manual or age-based");
  return Object.freeze({
    dpf735: requirePositive(merged.dpf735, "dpf735"),
    dpf850: requirePositive(merged.dpf850, "dpf850"),
    mode: "manual",
  });
}

export function darkCorrectIntensity(measuredIntensity, darkIntensity = 0) {
  const measured = requireFinite(measuredIntensity, "measuredIntensity");
  const dark = requireFinite(darkIntensity, "darkIntensity");
  const corrected = measured - dark;
  return corrected > 0 ? corrected : null;
}

export function opticalDensity(correctedIntensity, correctedReferenceIntensity) {
  const intensity = requirePositive(correctedIntensity, "correctedIntensity");
  const reference = requirePositive(correctedReferenceIntensity, "correctedReferenceIntensity");
  return Math.log(reference / intensity);
}

function isUsableSample(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

export function finiteValues(values) {
  if (!Array.isArray(values)) throw new TypeError("values must be an array");
  return values.filter(isUsableSample).map(Number);
}

export function estimateReference(values, estimator = "median") {
  const samples = finiteValues(values);
  if (samples.length === 0) throw new RangeError("reference window contains no finite values");
  if (estimator === "mean") return samples.reduce((sum, value) => sum + value, 0) / samples.length;
  if (estimator !== "median") throw new RangeError("baseline estimator must be median or mean");
  samples.sort((a, b) => a - b);
  const middle = Math.floor(samples.length / 2);
  return samples.length % 2 === 1
    ? samples[middle]
    : (samples[middle - 1] + samples[middle]) / 2;
}

export function darkCorrectSeries(values, darkIntensity = 0, invalidValue = null) {
  if (!Array.isArray(values)) throw new TypeError("values must be an array");
  return values.map((value) => {
    if (!isUsableSample(value)) return invalidValue;
    return darkCorrectIntensity(Number(value), darkIntensity) ?? invalidValue;
  });
}

export function intensitySeriesToOpticalDensity(values, {
  darkIntensity = 0,
  correctedReferenceIntensity = null,
  baselineEstimator = "median",
  invalidValue = null,
} = {}) {
  const corrected = darkCorrectSeries(values, darkIntensity, invalidValue);
  const reference = correctedReferenceIntensity === null
    ? estimateReference(corrected, baselineEstimator)
    : requirePositive(correctedReferenceIntensity, "correctedReferenceIntensity");
  const od = corrected.map((value) => {
    if (!isUsableSample(value) || Number(value) <= 0) return invalidValue;
    return opticalDensity(Number(value), reference);
  });
  return Object.freeze({ correctedIntensity: corrected, referenceIntensity: reference, opticalDensity: od });
}

export function getExtinctionCoefficients(datasetId = "moaveni1970") {
  const dataset = EXTINCTION_DATASETS[datasetId];
  if (!dataset) throw new RangeError(`Unknown extinction dataset: ${datasetId}`);
  const factor = dataset.coefficientType === "decadic" ? LN10 : 1;
  return Object.freeze({
    datasetId,
    HbO735: dataset.coefficients[735].HbO * factor,
    HbR735: dataset.coefficients[735].HbR * factor,
    HbO850: dataset.coefficients[850].HbO * factor,
    HbR850: dataset.coefficients[850].HbR * factor,
    unit: dataset.unit,
    convertedToNapierian: dataset.coefficientType === "decadic",
  });
}

/**
 * Convert one paired 735/850 optical-density sample to delta HbO/HbR/HbT.
 */
export function mbllFromOpticalDensity({
  od735,
  od850,
  distanceMm,
  dpf735,
  dpf850,
  extinctionDataset = "moaveni1970",
}) {
  const y735 = requireFinite(od735, "od735");
  const y850 = requireFinite(od850, "od850");
  const distanceCm = requirePositive(distanceMm, "distanceMm") / 10;
  const path735 = distanceCm * requirePositive(dpf735, "dpf735");
  const path850 = distanceCm * requirePositive(dpf850, "dpf850");
  const epsilon = getExtinctionCoefficients(extinctionDataset);

  const a = epsilon.HbO735 * path735;
  const b = epsilon.HbR735 * path735;
  const c = epsilon.HbO850 * path850;
  const d = epsilon.HbR850 * path850;
  const determinant = a * d - b * c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-18) {
    throw new RangeError("MBLL extinction matrix is singular or ill-conditioned");
  }

  const hboM = (y735 * d - b * y850) / determinant;
  const hbrM = (a * y850 - y735 * c) / determinant;
  const scaleToMicromolar = 1e6;
  const HbO = hboM * scaleToMicromolar;
  const HbR = hbrM * scaleToMicromolar;
  return Object.freeze({ HbO, HbR, HbT: HbO + HbR, unit: "uM" });
}

export function mbllSeriesFromOpticalDensity({
  od735,
  od850,
  distanceMm,
  dpf735,
  dpf850,
  extinctionDataset = "moaveni1970",
  invalidValue = null,
}) {
  if (!Array.isArray(od735) || !Array.isArray(od850)) throw new TypeError("od735 and od850 must be arrays");
  if (od735.length !== od850.length) throw new RangeError("od735 and od850 must have equal length");
  const HbO = [], HbR = [], HbT = [];
  for (let index = 0; index < od735.length; index += 1) {
    if (!isUsableSample(od735[index]) || !isUsableSample(od850[index])) {
      HbO.push(invalidValue); HbR.push(invalidValue); HbT.push(invalidValue);
      continue;
    }
    const sample = mbllFromOpticalDensity({
      od735: Number(od735[index]),
      od850: Number(od850[index]),
      distanceMm,
      dpf735,
      dpf850,
      extinctionDataset,
    });
    HbO.push(sample.HbO); HbR.push(sample.HbR); HbT.push(sample.HbT);
  }
  return Object.freeze({ HbO, HbR, HbT, unit: "uM" });
}

export function processIntensityPair({
  I735,
  I850,
  dark735 = 0,
  dark850 = 0,
  reference735,
  reference850,
  distanceMm,
  dpf735,
  dpf850,
  extinctionDataset = "moaveni1970",
}) {
  const corrected735 = darkCorrectIntensity(I735, dark735);
  const corrected850 = darkCorrectIntensity(I850, dark850);
  if (corrected735 === null || corrected850 === null) return null;
  const od735 = opticalDensity(corrected735, reference735);
  const od850 = opticalDensity(corrected850, reference850);
  const hb = mbllFromOpticalDensity({ od735, od850, distanceMm, dpf735, dpf850, extinctionDataset });
  return Object.freeze({ I735, I850, corrected735, corrected850, od735, od850, ...hb });
}

export function namedMbllOutput(channelId, sample) {
  if (!sample) return Object.freeze({
    [`${channelId}_HbO`]: null,
    [`${channelId}_HbR`]: null,
    [`${channelId}_HbT`]: null,
  });
  return Object.freeze({
    [`${channelId}_I735`]: sample.I735,
    [`${channelId}_I850`]: sample.I850,
    [`${channelId}_OD735`]: sample.od735,
    [`${channelId}_OD850`]: sample.od850,
    [`${channelId}_HbO`]: sample.HbO,
    [`${channelId}_HbR`]: sample.HbR,
    [`${channelId}_HbT`]: sample.HbT,
  });
}
