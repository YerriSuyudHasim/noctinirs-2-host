// ============================================================
// NoctiNIRS probe geometry and logical channel mapping.
//
// Canonical detector naming follows the live-view labels:
//   PD0 in live view === P0 in the physical geometry
//   PD1 in live view === P1 in the physical geometry
//   PD2 in live view === P2 in the physical geometry
//   PD3 in live view === P3 in the physical geometry
//
// Firmware source IDs are zero-based while physical LED labels
// are one-based: sourceId 0 === L1, ..., sourceId 9 === L10.
// ============================================================

export const WAVELENGTHS_NM = Object.freeze([735, 850]);
export const SOURCE_COUNT = 10;
export const PD_COUNT = 4;

export const SOURCES = Object.freeze(
  Array.from({ length: SOURCE_COUNT }, (_, sourceId) => Object.freeze({
    id: `L${sourceId + 1}`,
    sourceId,
    wavelengthsNm: WAVELENGTHS_NM,
  })),
);

export const PHOTODIODES = Object.freeze(
  Array.from({ length: PD_COUNT }, (_, detectorIndex) => Object.freeze({
    id: `PD${detectorIndex}`,
    detectorIndex,
  })),
);

function channel(definition) {
  return Object.freeze({
    type: "long",
    ...definition,
    distanceCm: definition.distanceMm / 10,
  });
}

function shortChannel(definition) {
  return Object.freeze({
    type: "short",
    ...definition,
    distanceCm: definition.distanceMm / 10,
  });
}

export const SHORT_CHANNELS = Object.freeze([
  shortChannel({ id: "SC1L", hemisphere: "left", source: "L1", sourceId: 0, detector: "PD2", detectorIndex: 2, distanceMm: 5 }),
  shortChannel({ id: "SC2L", hemisphere: "left", source: "L1", sourceId: 0, detector: "PD3", detectorIndex: 3, distanceMm: 8 }),
  shortChannel({ id: "SC1R", hemisphere: "right", source: "L6", sourceId: 5, detector: "PD1", detectorIndex: 1, distanceMm: 5 }),
  shortChannel({ id: "SC2R", hemisphere: "right", source: "L6", sourceId: 5, detector: "PD0", detectorIndex: 0, distanceMm: 8 }),
]);

export const LONG_CHANNELS = Object.freeze([
  channel({ id: "CH1", channelNumber: 1, hemisphere: "left", source: "L2", sourceId: 1, detector: "PD3", detectorIndex: 3, distanceMm: 30.0 }),
  channel({ id: "CH2", channelNumber: 2, hemisphere: "left", source: "L2", sourceId: 1, detector: "PD2", detectorIndex: 2, distanceMm: 28.2 }),
  channel({ id: "CH3", channelNumber: 3, hemisphere: "left", source: "L3", sourceId: 2, detector: "PD3", detectorIndex: 3, distanceMm: 30.0 }),
  channel({ id: "CH4", channelNumber: 4, hemisphere: "left", source: "L3", sourceId: 2, detector: "PD2", detectorIndex: 2, distanceMm: 22.5 }),
  channel({ id: "CH5", channelNumber: 5, hemisphere: "left", source: "L4", sourceId: 3, detector: "PD3", detectorIndex: 3, distanceMm: 30.0 }),
  channel({ id: "CH6", channelNumber: 6, hemisphere: "left", source: "L4", sourceId: 3, detector: "PD2", detectorIndex: 2, distanceMm: 18.5 }),
  channel({ id: "CH7", channelNumber: 7, hemisphere: "left", source: "L5", sourceId: 4, detector: "PD3", detectorIndex: 3, distanceMm: 30.0 }),
  channel({ id: "CH8", channelNumber: 8, hemisphere: "left", source: "L5", sourceId: 4, detector: "PD2", detectorIndex: 2, distanceMm: 20.3 }),

  channel({ id: "CH9", channelNumber: 9, hemisphere: "right", source: "L7", sourceId: 6, detector: "PD0", detectorIndex: 0, distanceMm: 30.0 }),
  channel({ id: "CH10", channelNumber: 10, hemisphere: "right", source: "L7", sourceId: 6, detector: "PD1", detectorIndex: 1, distanceMm: 28.2 }),
  channel({ id: "CH11", channelNumber: 11, hemisphere: "right", source: "L8", sourceId: 7, detector: "PD0", detectorIndex: 0, distanceMm: 30.0 }),
  channel({ id: "CH12", channelNumber: 12, hemisphere: "right", source: "L8", sourceId: 7, detector: "PD1", detectorIndex: 1, distanceMm: 22.5 }),
  channel({ id: "CH13", channelNumber: 13, hemisphere: "right", source: "L9", sourceId: 8, detector: "PD0", detectorIndex: 0, distanceMm: 30.0 }),
  channel({ id: "CH14", channelNumber: 14, hemisphere: "right", source: "L9", sourceId: 8, detector: "PD1", detectorIndex: 1, distanceMm: 18.5 }),
  channel({ id: "CH15", channelNumber: 15, hemisphere: "right", source: "L10", sourceId: 9, detector: "PD0", detectorIndex: 0, distanceMm: 30.0 }),
  channel({ id: "CH16", channelNumber: 16, hemisphere: "right", source: "L10", sourceId: 9, detector: "PD1", detectorIndex: 1, distanceMm: 20.3 }),
]);

export const ALL_CHANNELS = Object.freeze([...SHORT_CHANNELS, ...LONG_CHANNELS]);

const CHANNEL_BY_ID = new Map(ALL_CHANNELS.map((entry) => [entry.id, entry]));

export function getChannel(channelId) {
  const result = CHANNEL_BY_ID.get(String(channelId));
  if (!result) throw new RangeError(`Unknown NoctiNIRS channel: ${channelId}`);
  return result;
}

export function slotIndexFor(sourceId, wavelengthNm) {
  const source = Number(sourceId);
  if (!Number.isInteger(source) || source < 0 || source >= SOURCE_COUNT) {
    throw new RangeError(`sourceId must be an integer from 0 to ${SOURCE_COUNT - 1}`);
  }
  if (wavelengthNm !== 735 && wavelengthNm !== 850) {
    throw new RangeError("wavelengthNm must be 735 or 850");
  }
  return source * 2 + (wavelengthNm === 850 ? 1 : 0);
}

export function slotMetadata(slotIndex) {
  const slot = Number(slotIndex);
  if (!Number.isInteger(slot) || slot < 0 || slot >= SOURCE_COUNT * 2) {
    throw new RangeError(`slotIndex must be an integer from 0 to ${SOURCE_COUNT * 2 - 1}`);
  }
  const sourceId = Math.floor(slot / 2);
  return Object.freeze({
    slotIndex: slot,
    sourceId,
    source: `L${sourceId + 1}`,
    wavelengthNm: (slot & 1) === 0 ? 735 : 850,
  });
}

export function measurementName(channelId, quantity, wavelengthNm = null) {
  getChannel(channelId);
  const normalizedQuantity = String(quantity).trim();
  if (!normalizedQuantity) throw new TypeError("quantity is required");
  if (wavelengthNm === null || wavelengthNm === undefined) return `${channelId}_${normalizedQuantity}`;
  if (wavelengthNm !== 735 && wavelengthNm !== 850) throw new RangeError("wavelengthNm must be 735 or 850");
  return `${channelId}_${normalizedQuantity}${wavelengthNm}`;
}

export function shortChannelsForHemisphere(hemisphere) {
  const side = String(hemisphere).toLowerCase();
  if (side !== "left" && side !== "right") throw new RangeError("hemisphere must be left or right");
  return SHORT_CHANNELS.filter((entry) => entry.hemisphere === side);
}

export function shortChannelsForMode(hemisphere, mode) {
  const channels = shortChannelsForHemisphere(hemisphere);
  switch (mode) {
    case "5mm": return [channels.find((entry) => entry.distanceMm === 5)];
    case "8mm": return [channels.find((entry) => entry.distanceMm === 8)];
    case "both": return channels.slice().sort((a, b) => a.distanceMm - b.distanceMm);
    case "none": return [];
    default: throw new RangeError("short-separation mode must be none, 5mm, 8mm, or both");
  }
}

export function shortRegressorIdsForChannel(channelId, mode) {
  const longChannel = getChannel(channelId);
  if (longChannel.type !== "long") throw new RangeError(`${channelId} is not a long channel`);
  return shortChannelsForMode(longChannel.hemisphere, mode).map((entry) => entry.id);
}

function unwrapSlots(frame) {
  const slots = frame?.slots ?? frame?.data?.slots;
  if (!Array.isArray(slots)) throw new TypeError("frame must contain a slots array");
  return slots;
}

/**
 * Extract wavelength-specific raw intensities from one decoded fNIRS frame.
 * Output names follow the agreed convention, e.g. CH1_I735 and CH1_I850.
 */
export function extractNamedIntensities(frame, { includeShort = true, includeLong = true } = {}) {
  const slots = unwrapSlots(frame);
  const selected = [
    ...(includeShort ? SHORT_CHANNELS : []),
    ...(includeLong ? LONG_CHANNELS : []),
  ];
  const output = {};

  for (const channelEntry of selected) {
    for (const wavelengthNm of WAVELENGTHS_NM) {
      const slotIndex = slotIndexFor(channelEntry.sourceId, wavelengthNm);
      const slot = slots[slotIndex];
      const valid = Boolean(slot)
        && slot.valid !== false
        && slot.crcDropped !== true
        && Array.isArray(slot.averages)
        && Number.isFinite(Number(slot.averages[channelEntry.detectorIndex]));
      const key = measurementName(channelEntry.id, "I", wavelengthNm);
      output[key] = valid ? Number(slot.averages[channelEntry.detectorIndex]) : null;
      output[`${key}_VALID`] = valid;
    }
  }

  return output;
}

export function channelPairsBySource() {
  const groups = new Map();
  for (const entry of ALL_CHANNELS) {
    const existing = groups.get(entry.source) ?? [];
    existing.push(entry.id);
    groups.set(entry.source, existing);
  }
  return Object.freeze(Object.fromEntries(groups));
}
