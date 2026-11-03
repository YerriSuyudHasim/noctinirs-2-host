// ============================================================
// Short-separation regression for NoctiNIRS optical-density data.
//
// User-selectable modes:
//   none  : no short-separation correction
//   5mm   : use SC1L or SC1R for every long channel in that side
//   8mm   : use SC2L or SC2R for every long channel in that side
//   both  : use paired 5 mm and 8 mm regressors from the same side
//
// Regression is performed independently for 735 nm and 850 nm,
// before MBLL conversion. Raw and corrected outputs are retained.
// ============================================================

import { mbllSeriesFromOpticalDensity } from "./fnirs-mbll.js";

export const SHORT_SEPARATION_MODES = Object.freeze(["none", "5mm", "8mm", "both"]);
export const SHORT_SEPARATION_METHODS = Object.freeze(["auto", "ols", "ridge"]);

export const DEFAULT_SHORT_SEPARATION_CONFIG = Object.freeze({
  enabled: false,
  mode: "8mm",
  method: "auto",
  ridgeLambda: 1e-3,
  standardizeRegressors: true,
  includeIntercept: true,
  preserveLongMean: true,
  minimumSamples: 30,
});

function assertArray(value, name) {
  if (!Array.isArray(value)) throw new TypeError(`${name} must be an array`);
  return value;
}

function validateMode(mode) {
  if (!SHORT_SEPARATION_MODES.includes(mode)) {
    throw new RangeError(`mode must be one of: ${SHORT_SEPARATION_MODES.join(", ")}`);
  }
  return mode;
}

function validateMethod(method) {
  if (!SHORT_SEPARATION_METHODS.includes(method)) {
    throw new RangeError(`method must be one of: ${SHORT_SEPARATION_METHODS.join(", ")}`);
  }
  return method;
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function isUsableSample(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function solveLinearSystem(matrix, vector, tolerance = 1e-12) {
  const n = vector.length;
  const augmented = matrix.map((row, i) => [...row, vector[i]]);
  for (let pivot = 0; pivot < n; pivot += 1) {
    let best = pivot;
    for (let row = pivot + 1; row < n; row += 1) {
      if (Math.abs(augmented[row][pivot]) > Math.abs(augmented[best][pivot])) best = row;
    }
    if (Math.abs(augmented[best][pivot]) < tolerance) {
      throw new RangeError("Regression matrix is singular; use ridge regression or a larger fitting window");
    }
    [augmented[pivot], augmented[best]] = [augmented[best], augmented[pivot]];
    const divisor = augmented[pivot][pivot];
    for (let col = pivot; col <= n; col += 1) augmented[pivot][col] /= divisor;
    for (let row = 0; row < n; row += 1) {
      if (row === pivot) continue;
      const factor = augmented[row][pivot];
      for (let col = pivot; col <= n; col += 1) augmented[row][col] -= factor * augmented[pivot][col];
    }
  }
  return augmented.map((row) => row[n]);
}

function selectRegressors({ short5, short8, mode }) {
  switch (validateMode(mode)) {
    case "none": return [];
    case "5mm": return [assertArray(short5, "short5")];
    case "8mm": return [assertArray(short8, "short8")];
    case "both": return [assertArray(short5, "short5"), assertArray(short8, "short8")];
    default: return [];
  }
}

function resolveMethod(mode, method) {
  const validated = validateMethod(method);
  if (validated !== "auto") return validated;
  return mode === "both" ? "ridge" : "ols";
}

/**
 * Fit one long-channel OD series against one or two short-channel OD series.
 * Missing/non-finite rows are excluded from fitting and remain null in output.
 */
export function regressShortSeparation({
  long,
  short5 = null,
  short8 = null,
  mode = "8mm",
  method = "auto",
  ridgeLambda = 1e-3,
  standardizeRegressors = true,
  includeIntercept = true,
  preserveLongMean = true,
  minimumSamples = 3,
  invalidValue = null,
}) {
  const longSeries = assertArray(long, "long");
  const regressors = selectRegressors({ short5, short8, mode });
  if (mode === "none") {
    return Object.freeze({
      corrected: longSeries.slice(),
      fittedNuisance: longSeries.map(() => 0),
      coefficients: [],
      intercept: 0,
      mode: "none",
      method: "none",
      sampleCount: longSeries.filter(isUsableSample).length,
    });
  }
  for (const regressor of regressors) {
    if (regressor.length !== longSeries.length) throw new RangeError("long and short series must have equal length");
  }

  const rows = [];
  for (let index = 0; index < longSeries.length; index += 1) {
    if (!isUsableSample(longSeries[index]) || regressors.some((series) => !isUsableSample(series[index]))) continue;
    const y = Number(longSeries[index]);
    const x = regressors.map((series) => Number(series[index]));
    rows.push({ index, y, x });
  }
  const requiredSamples = Math.max(3, Math.floor(Number(minimumSamples)), regressors.length + 1);
  if (!Number.isFinite(requiredSamples) || rows.length < requiredSamples) {
    throw new RangeError(`Not enough finite samples for short-separation regression: ${rows.length}`);
  }

  const actualMethod = resolveMethod(mode, method);
  const yMean = includeIntercept ? mean(rows.map((row) => row.y)) : 0;
  const xMeans = regressors.map((_, column) => includeIntercept ? mean(rows.map((row) => row.x[column])) : 0);
  const centeredY = rows.map((row) => row.y - yMean);
  const centeredX = rows.map((row) => row.x.map((value, column) => value - xMeans[column]));

  const p = regressors.length;
  const scales = Array(p).fill(1);
  if (actualMethod === "ridge" && standardizeRegressors) {
    for (let column = 0; column < p; column += 1) {
      const rms = Math.sqrt(centeredX.reduce((sum, row) => sum + row[column] ** 2, 0) / rows.length);
      if (!Number.isFinite(rms) || rms <= 1e-15) throw new RangeError("A short-separation regressor has near-zero variance");
      scales[column] = rms;
    }
  }
  const designX = centeredX.map((row) => row.map((value, column) => value / scales[column]));
  const xtx = Array.from({ length: p }, () => Array(p).fill(0));
  const xty = Array(p).fill(0);
  for (let row = 0; row < rows.length; row += 1) {
    for (let i = 0; i < p; i += 1) {
      xty[i] += designX[row][i] * centeredY[row];
      for (let j = 0; j < p; j += 1) xtx[i][j] += designX[row][i] * designX[row][j];
    }
  }
  const lambda = actualMethod === "ridge" ? Number(ridgeLambda) : 0;
  if (!Number.isFinite(lambda) || lambda < 0) throw new RangeError("ridgeLambda must be finite and non-negative");
  for (let i = 0; i < p; i += 1) xtx[i][i] += lambda;
  const standardizedCoefficients = solveLinearSystem(xtx, xty);
  const coefficients = standardizedCoefficients.map((value, column) => value / scales[column]);
  const intercept = includeIntercept
    ? yMean - coefficients.reduce((sum, beta, i) => sum + beta * xMeans[i], 0)
    : 0;

  const corrected = Array(longSeries.length).fill(invalidValue);
  const fittedNuisance = Array(longSeries.length).fill(invalidValue);
  const meanRestore = preserveLongMean ? yMean : 0;
  for (const row of rows) {
    const prediction = intercept + coefficients.reduce((sum, beta, i) => sum + beta * row.x[i], 0);
    fittedNuisance[row.index] = prediction;
    corrected[row.index] = row.y - prediction + meanRestore;
  }

  const actualMean = mean(rows.map((row) => row.y));
  const sse = rows.reduce((sum, row) => {
    const prediction = intercept + coefficients.reduce((inner, beta, i) => inner + beta * row.x[i], 0);
    return sum + (row.y - prediction) ** 2;
  }, 0);
  const sst = rows.reduce((sum, row) => sum + (row.y - actualMean) ** 2, 0);
  let regressorCorrelation = null;
  if (p === 2) {
    const sumXY = centeredX.reduce((sum, row) => sum + row[0] * row[1], 0);
    const sumX2 = centeredX.reduce((sum, row) => sum + row[0] ** 2, 0);
    const sumY2 = centeredX.reduce((sum, row) => sum + row[1] ** 2, 0);
    regressorCorrelation = sumX2 > 0 && sumY2 > 0 ? sumXY / Math.sqrt(sumX2 * sumY2) : null;
  }

  return Object.freeze({
    corrected,
    fittedNuisance,
    coefficients: Object.freeze(coefficients.slice()),
    intercept,
    mode,
    method: actualMethod,
    ridgeLambda: lambda,
    standardizedForRidge: actualMethod === "ridge" && Boolean(standardizeRegressors),
    sampleCount: rows.length,
    regressorCount: p,
    diagnostics: Object.freeze({
      rSquared: sst > 0 ? 1 - sse / sst : null,
      regressorCorrelation,
    }),
  });
}

export function regressOpticalDensityPair({
  long735,
  long850,
  short5_735 = null,
  short5_850 = null,
  short8_735 = null,
  short8_850 = null,
  ...options
}) {
  const result735 = regressShortSeparation({
    long: long735,
    short5: short5_735,
    short8: short8_735,
    ...options,
  });
  const result850 = regressShortSeparation({
    long: long850,
    short5: short5_850,
    short8: short8_850,
    ...options,
  });
  return Object.freeze({ OD735: result735, OD850: result850 });
}

/**
 * Complete OD-domain correction + raw/corrected MBLL conversion.
 */
export function mbllWithShortSeparation({
  channelId,
  distanceMm,
  dpf735,
  dpf850,
  longOD735,
  longOD850,
  short5OD735 = null,
  short5OD850 = null,
  short8OD735 = null,
  short8OD850 = null,
  extinctionDataset = "moaveni1970",
  regression = {},
  invalidValue = null,
}) {
  const raw = mbllSeriesFromOpticalDensity({
    od735: longOD735,
    od850: longOD850,
    distanceMm,
    dpf735,
    dpf850,
    extinctionDataset,
    invalidValue,
  });
  const regressionConfig = { ...DEFAULT_SHORT_SEPARATION_CONFIG, ...regression };
  if (!regressionConfig.enabled) regressionConfig.mode = "none";
  const correction = regressOpticalDensityPair({
    long735: longOD735,
    long850: longOD850,
    short5_735: short5OD735,
    short5_850: short5OD850,
    short8_735: short8OD735,
    short8_850: short8OD850,
    invalidValue,
    ...regressionConfig,
  });
  const corrected = mbllSeriesFromOpticalDensity({
    od735: correction.OD735.corrected,
    od850: correction.OD850.corrected,
    distanceMm,
    dpf735,
    dpf850,
    extinctionDataset,
    invalidValue,
  });
  return Object.freeze({
    channelId,
    raw,
    corrected,
    correction,
    names: Object.freeze({
      rawHbO: `${channelId}_HbO_RAW`,
      rawHbR: `${channelId}_HbR_RAW`,
      correctedHbO: `${channelId}_HbO_CORR`,
      correctedHbR: `${channelId}_HbR_CORR`,
    }),
  });
}

/**
 * Stateful rolling regression for live preview. Create one instance per
 * long channel and wavelength. Offline/export processing should refit the
 * complete recording or a scientifically chosen analysis window.
 */
export class RollingShortSeparationRegressor {
  constructor(config = {}) {
    this.config = { ...DEFAULT_SHORT_SEPARATION_CONFIG, ...config };
    validateMode(this.config.mode);
    validateMethod(this.config.method);
    const requestedWindow = Number(config.windowSamples ?? 300);
    if (!Number.isFinite(requestedWindow) || requestedWindow < this.config.minimumSamples) {
      throw new RangeError("windowSamples must be finite and at least minimumSamples");
    }
    this.windowSamples = Math.floor(requestedWindow);
    this.reset();
  }

  reset() {
    this.long = [];
    this.short5 = [];
    this.short8 = [];
    this.lastModel = null;
  }

  push({ long, short5 = null, short8 = null }) {
    this.long.push(long);
    this.short5.push(short5);
    this.short8.push(short8);
    while (this.long.length > this.windowSamples) {
      this.long.shift(); this.short5.shift(); this.short8.shift();
    }
    if (!this.config.enabled || this.config.mode === "none") return { corrected: long, ready: true, model: null };
    if (this.long.length < this.config.minimumSamples) return { corrected: null, ready: false, model: null };
    try {
      this.lastModel = regressShortSeparation({
        long: this.long,
        short5: this.short5,
        short8: this.short8,
        ...this.config,
      });
      return {
        corrected: this.lastModel.corrected.at(-1),
        ready: true,
        model: this.lastModel,
      };
    } catch (error) {
      return { corrected: null, ready: false, model: null, error };
    }
  }
}
