import assert from "node:assert/strict";
import {
  LONG_CHANNELS,
  SHORT_CHANNELS,
  extractNamedIntensities,
  getChannel,
  measurementName,
  shortChannelsForMode,
  shortRegressorIdsForChannel,
  slotIndexFor,
} from "../public/lib/fnirs-geometry.js";
import {
  getExtinctionCoefficients,
  mbllFromOpticalDensity,
  opticalDensity,
  resolveDpf,
  scholkmannWolfDpf,
} from "../public/lib/fnirs-mbll.js";
import {
  mbllWithShortSeparation,
  regressShortSeparation,
} from "../public/lib/fnirs-short-separation.js";

function almostEqual(actual, expected, tolerance = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
}

assert.equal(LONG_CHANNELS.length, 16);
assert.equal(SHORT_CHANNELS.length, 4);
assert.deepEqual(getChannel("CH1"), LONG_CHANNELS[0]);
assert.equal(getChannel("CH1").source, "L2");
assert.equal(getChannel("CH1").detector, "PD3");
assert.equal(getChannel("CH16").source, "L10");
assert.equal(getChannel("CH16").detector, "PD1");
assert.equal(slotIndexFor(1, 735), 2);
assert.equal(slotIndexFor(1, 850), 3);
assert.equal(measurementName("CH1", "I", 735), "CH1_I735");
assert.deepEqual(shortChannelsForMode("left", "both").map((entry) => entry.id), ["SC1L", "SC2L"]);
assert.deepEqual(shortRegressorIdsForChannel("CH1", "5mm"), ["SC1L"]);
assert.deepEqual(shortRegressorIdsForChannel("CH9", "both"), ["SC1R", "SC2R"]);

const slots = Array.from({ length: 20 }, (_, slotIndex) => ({
  valid: true,
  crcDropped: false,
  averages: [slotIndex * 10 + 0, slotIndex * 10 + 1, slotIndex * 10 + 2, slotIndex * 10 + 3],
}));
const named = extractNamedIntensities({ slots });
assert.equal(named.CH1_I735, 23);
assert.equal(named.CH1_I850, 33);
assert.equal(named.CH2_I735, 22);
assert.equal(named.SC1R_I735, 101);
assert.equal(named.SC2R_I850, 110);

almostEqual(scholkmannWolfDpf(735, 28), 6.255162955448327, 1e-12);
almostEqual(scholkmannWolfDpf(850, 28), 5.1768145679484405, 1e-12);
const ageDpf = resolveDpf({ dpfMode: "age-based", ageYears: 28 });
almostEqual(ageDpf.dpf735, 6.255162955448327, 1e-12);
almostEqual(opticalDensity(900, 1000), Math.log(1000 / 900), 1e-12);
const epsilon = getExtinctionCoefficients();
almostEqual(epsilon.HbO735, 420 * Math.log(10), 1e-12);

// Forward-model a known Hb change and recover it using MBLL.
const dpf735 = 6.2;
const dpf850 = 5.2;
const distanceMm = 30;
const HbO_M = 4e-6;
const HbR_M = -1.5e-6;
const distanceCm = distanceMm / 10;
const od735 = (epsilon.HbO735 * HbO_M + epsilon.HbR735 * HbR_M) * distanceCm * dpf735;
const od850 = (epsilon.HbO850 * HbO_M + epsilon.HbR850 * HbR_M) * distanceCm * dpf850;
const recovered = mbllFromOpticalDensity({ od735, od850, distanceMm, dpf735, dpf850 });
almostEqual(recovered.HbO, 4, 1e-10);
almostEqual(recovered.HbR, -1.5, 1e-10);

// Single-regressor correction recovers an orthogonal cerebral component.
const sampleCount = 256;
const short8 = Array.from({ length: sampleCount }, (_, i) => Math.sin((2 * Math.PI * i) / sampleCount));
const cerebral = Array.from({ length: sampleCount }, (_, i) => 0.2 * Math.sin((6 * Math.PI * i) / sampleCount));
const long = short8.map((value, i) => 2.5 * value + cerebral[i] + 0.7);
const reg8 = regressShortSeparation({
  long,
  short8,
  mode: "8mm",
  method: "ols",
  preserveLongMean: false,
  minimumSamples: 20,
});
almostEqual(reg8.coefficients[0], 2.5, 1e-10);
for (let i = 0; i < cerebral.length; i += 1) almostEqual(reg8.corrected[i], cerebral[i], 1e-10);

// Dual regressors use ridge by default and preserve output length.
const short5 = Array.from({ length: sampleCount }, (_, i) => Math.cos((4 * Math.PI * i) / sampleCount));
const dualLong = short5.map((value, i) => 0.9 * value + 1.3 * short8[i] + cerebral[i]);
const regBoth = regressShortSeparation({
  long: dualLong,
  short5,
  short8,
  mode: "both",
  method: "auto",
  ridgeLambda: 1e-9,
  preserveLongMean: false,
  minimumSamples: 20,
});
assert.equal(regBoth.method, "ridge");
assert.equal(regBoth.standardizedForRidge, true);
assert.equal(regBoth.corrected.length, dualLong.length);
almostEqual(regBoth.coefficients[0], 0.9, 1e-8);
almostEqual(regBoth.coefficients[1], 1.3, 1e-8);

// End-to-end function returns both raw and corrected named data paths.
const pipeline = mbllWithShortSeparation({
  channelId: "CH1",
  distanceMm: 30,
  dpf735: 6.2,
  dpf850: 5.2,
  longOD735: dualLong.map((v) => v * 1e-3),
  longOD850: dualLong.map((v) => v * 0.8e-3),
  short5OD735: short5.map((v) => v * 1e-3),
  short5OD850: short5.map((v) => v * 0.8e-3),
  short8OD735: short8.map((v) => v * 1e-3),
  short8OD850: short8.map((v) => v * 0.8e-3),
  regression: { enabled: true, mode: "both", method: "ridge", ridgeLambda: 1e-6, minimumSamples: 20 },
});
assert.equal(pipeline.names.correctedHbO, "CH1_HbO_CORR");
assert.equal(pipeline.raw.HbO.length, sampleCount);
assert.equal(pipeline.corrected.HbO.length, sampleCount);

console.log("NoctiNIRS geometry/MBLL/short-separation tests passed.");
