// ============================================================
// File: protocol.js
// NoctiNIRS UDP + stream protocol decoder/encoder.
// All multibyte payload fields are little-endian unless noted.
// ============================================================

export const NOCTI = Object.freeze({
  UDP_MAGIC: 0x4e4f4354,
  UDP_VERSION: 1,
  UDP_HEADER_BYTES: 28,
  UDP_TYPE_STREAM: 1,

  STREAM_MAGIC: 0xa5,
  STREAM_VERSION: 1,
  STREAM_HEADER_BYTES: 12,
  STREAM_CRC_BYTES: 2,

  REC_STATUS: 1,
  REC_EVENT: 2,
  REC_TIME64: 3,
  REC_FNIRS: 4,
  REC_FNIRS_CFG: 6,
  REC_FNIRS_FRAME: 7,
  REC_SESSION: 8,

  FNIRS_SLOT_COUNT: 20,
  FNIRS_PD_COUNT: 4,
  FNIRS_FRAME_PAYLOAD_BYTES: 356,
});

export function crc16CcittFalse(buffer, initial = 0xffff) {
  let crc = initial & 0xffff;
  for (let index = 0; index < buffer.length; index += 1) {
    crc ^= buffer[index] << 8;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc & 0x8000) !== 0
        ? ((crc << 1) ^ 0x1021) & 0xffff
        : (crc << 1) & 0xffff;
    }
  }
  return crc & 0xffff;
}

export function parseUdpPacket(packet) {
  if (!Buffer.isBuffer(packet) || packet.length < NOCTI.UDP_HEADER_BYTES) {
    return { ok: false, error: "udp_packet_too_short" };
  }

  const magic = packet.readUInt32LE(0);
  const version = packet.readUInt8(4);
  const type = packet.readUInt8(5);
  const flags = packet.readUInt16LE(6);
  const sessionId = packet.readUInt32LE(8);
  const sequence = packet.readUInt32LE(12);
  const timestampUs = packet.readBigUInt64LE(16);
  const payloadLength = packet.readUInt16LE(24);
  const receivedCrc = packet.readUInt16LE(26);
  const totalLength = NOCTI.UDP_HEADER_BYTES + payloadLength;

  if (magic !== NOCTI.UDP_MAGIC) {
    return { ok: false, error: "udp_bad_magic" };
  }
  if (version !== NOCTI.UDP_VERSION) {
    return { ok: false, error: "udp_bad_version", version };
  }
  if (type !== NOCTI.UDP_TYPE_STREAM) {
    return { ok: false, error: "udp_unsupported_type", type };
  }
  if (packet.length !== totalLength) {
    return {
      ok: false,
      error: "udp_length_mismatch",
      expected: totalLength,
      actual: packet.length,
    };
  }

  const crcInput = Buffer.from(packet);
  crcInput.writeUInt16LE(0, 26);
  const calculatedCrc = crc16CcittFalse(crcInput);

  return {
    ok: true,
    crcOk: calculatedCrc === receivedCrc,
    header: {
      magic,
      version,
      type,
      flags,
      sessionId,
      sequence,
      timestampUs,
      payloadLength,
      receivedCrc,
      calculatedCrc,
    },
    payload: packet.subarray(NOCTI.UDP_HEADER_BYTES),
  };
}

export function parseStreamFrame(frame) {
  const minimumLength = NOCTI.STREAM_HEADER_BYTES + NOCTI.STREAM_CRC_BYTES;
  if (!Buffer.isBuffer(frame) || frame.length < minimumLength) {
    return { ok: false, error: "stream_frame_too_short" };
  }

  const magic = frame.readUInt8(0);
  const version = frame.readUInt8(1);
  const type = frame.readUInt8(2);
  const flags = frame.readUInt8(3);
  const sequence = frame.readUInt16LE(4);
  const relativeTimeUs = frame.readUInt32LE(6);
  const payloadLength = frame.readUInt16LE(10);
  const frameWithoutCrcLength = NOCTI.STREAM_HEADER_BYTES + payloadLength;
  const totalLength = frameWithoutCrcLength + NOCTI.STREAM_CRC_BYTES;

  if (magic !== NOCTI.STREAM_MAGIC) {
    return { ok: false, error: "stream_bad_magic" };
  }
  if (version !== NOCTI.STREAM_VERSION) {
    return { ok: false, error: "stream_bad_version", version };
  }
  if (frame.length !== totalLength) {
    return {
      ok: false,
      error: "stream_length_mismatch",
      expected: totalLength,
      actual: frame.length,
    };
  }

  // Stream CRC is stored big-endian by nocti_stream.c.
  const receivedCrc = frame.readUInt16BE(frameWithoutCrcLength);
  const calculatedCrc = crc16CcittFalse(frame.subarray(0, frameWithoutCrcLength));

  return {
    ok: true,
    crcOk: receivedCrc === calculatedCrc,
    header: {
      magic,
      version,
      type,
      flags,
      sequence,
      relativeTimeUs,
      payloadLength,
      receivedCrc,
      calculatedCrc,
    },
    payload: frame.subarray(NOCTI.STREAM_HEADER_BYTES, frameWithoutCrcLength),
  };
}

export function decodeStreamPayload(type, payload) {
  switch (type) {
    case NOCTI.REC_SESSION:
      return decodeSessionV1(payload);
    case NOCTI.REC_TIME64:
      return decodeTime64(payload);
    case NOCTI.REC_FNIRS_CFG:
      return decodeFnirsConfigV1(payload);
    case NOCTI.REC_EVENT:
      return decodeEventV1(payload);
    case NOCTI.REC_FNIRS_FRAME:
      return decodeFnirsFrameV1(payload);
    default:
      return { kind: "unsupported", recordType: type, payloadLength: payload.length };
  }
}

export function decodeSessionV1(payload) {
  if (payload.length !== 16) {
    return { kind: "decode_error", error: "session_size", actual: payload.length };
  }
  return {
    kind: "session",
    version: payload.readUInt8(0),
    sessionId: payload.readUInt32LE(4),
    t0Us: payload.readBigUInt64LE(8).toString(),
  };
}

export function decodeTime64(payload) {
  if (payload.length !== 8) {
    return { kind: "decode_error", error: "time64_size", actual: payload.length };
  }
  // nocti_stream.c writes TIME64 bytes most-significant byte first.
  return {
    kind: "time64",
    absoluteTimeUs: payload.readBigUInt64BE(0).toString(),
  };
}

export function decodeFnirsConfigV1(payload) {
  if (payload.length !== 12) {
    return { kind: "decode_error", error: "fnirs_config_size", actual: payload.length };
  }
  return {
    kind: "fnirs_config",
    version: payload.readUInt8(0),
    mapMode: payload.readUInt8(1),
    frameRateHz: payload.readUInt8(2),
    slotCount: payload.readUInt8(3),
    kFixed: payload.readUInt8(4),
    current735Ma: payload.readUInt8(5),
    current850Ma: payload.readUInt8(6),
    settleUs: payload.readUInt16LE(8),
    windowUs: payload.readUInt16LE(10),
  };
}

export function decodeEventV1(payload) {
  if (payload.length !== 8) {
    return { kind: "decode_error", error: "event_size", actual: payload.length };
  }
  return {
    kind: "event",
    version: payload.readUInt8(0),
    level: payload.readUInt8(1),
    code: payload.readUInt16LE(2),
    argument: payload.readUInt32LE(4),
  };
}

export function decodeFnirsFrameV1(payload) {
  if (payload.length !== NOCTI.FNIRS_FRAME_PAYLOAD_BYTES) {
    return {
      kind: "decode_error",
      error: "fnirs_frame_size",
      expected: NOCTI.FNIRS_FRAME_PAYLOAD_BYTES,
      actual: payload.length,
    };
  }

  const version = payload.readUInt8(0);
  const flags = payload.readUInt8(1);
  const frameId = payload.readUInt16LE(2);
  const validMask = payload.readUInt32LE(4);
  const underKMask = payload.readUInt32LE(8);
  const crcMask = payload.readUInt32LE(12);
  const averagesOffset = 16;
  const nUsedOffset = averagesOffset + NOCTI.FNIRS_SLOT_COUNT * NOCTI.FNIRS_PD_COUNT * 4;
  const slots = [];

  for (let slotIndex = 0; slotIndex < NOCTI.FNIRS_SLOT_COUNT; slotIndex += 1) {
    const averageBase = averagesOffset + slotIndex * NOCTI.FNIRS_PD_COUNT * 4;
    const averages = [];
    for (let pd = 0; pd < NOCTI.FNIRS_PD_COUNT; pd += 1) {
      averages.push(payload.readInt32LE(averageBase + pd * 4));
    }

    const bit = 1 << slotIndex;
    slots.push({
      slotIndex,
      sourceId: Math.floor(slotIndex / 2),
      wavelengthNm: (slotIndex & 1) === 0 ? 735 : 850,
      valid: (validMask & bit) !== 0,
      underK: (underKMask & bit) !== 0,
      crcDropped: (crcMask & bit) !== 0,
      nUsed: payload.readUInt8(nUsedOffset + slotIndex),
      averages,
    });
  }

  return {
    kind: "fnirs_frame",
    version,
    flags,
    frameId,
    partial: (flags & 0x01) !== 0,
    validMask,
    underKMask,
    crcMask,
    validSlotCount: popcount32(validMask & 0x000fffff),
    underKSlotCount: popcount32(underKMask & 0x000fffff),
    crcSlotCount: popcount32(crcMask & 0x000fffff),
    slots,
  };
}

export function popcount32(value) {
  let v = value >>> 0;
  let count = 0;
  while (v !== 0) {
    v &= v - 1;
    count += 1;
  }
  return count;
}

// Encoder helpers below are used by simulator.js and protocol self-tests.
export function buildStreamFrame({ type, sequence, relativeTimeUs, payload, flags = 0x01 }) {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(payload ?? []);
  const frame = Buffer.alloc(NOCTI.STREAM_HEADER_BYTES + body.length + NOCTI.STREAM_CRC_BYTES);
  frame.writeUInt8(NOCTI.STREAM_MAGIC, 0);
  frame.writeUInt8(NOCTI.STREAM_VERSION, 1);
  frame.writeUInt8(type, 2);
  frame.writeUInt8(flags, 3);
  frame.writeUInt16LE(sequence & 0xffff, 4);
  frame.writeUInt32LE(relativeTimeUs >>> 0, 6);
  frame.writeUInt16LE(body.length, 10);
  body.copy(frame, NOCTI.STREAM_HEADER_BYTES);
  const crcOffset = NOCTI.STREAM_HEADER_BYTES + body.length;
  frame.writeUInt16BE(crc16CcittFalse(frame.subarray(0, crcOffset)), crcOffset);
  return frame;
}

export function buildUdpPacket({ sessionId, sequence, timestampUs, payload }) {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(payload ?? []);
  const packet = Buffer.alloc(NOCTI.UDP_HEADER_BYTES + body.length);
  packet.writeUInt32LE(NOCTI.UDP_MAGIC, 0);
  packet.writeUInt8(NOCTI.UDP_VERSION, 4);
  packet.writeUInt8(NOCTI.UDP_TYPE_STREAM, 5);
  packet.writeUInt16LE(0, 6);
  packet.writeUInt32LE(sessionId >>> 0, 8);
  packet.writeUInt32LE(sequence >>> 0, 12);
  packet.writeBigUInt64LE(BigInt(timestampUs), 16);
  packet.writeUInt16LE(body.length, 24);
  packet.writeUInt16LE(0, 26);
  body.copy(packet, NOCTI.UDP_HEADER_BYTES);
  packet.writeUInt16LE(crc16CcittFalse(packet), 26);
  return packet;
}
