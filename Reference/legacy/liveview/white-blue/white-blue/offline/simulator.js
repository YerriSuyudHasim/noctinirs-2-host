// ============================================================
// File: simulator.js
// Local NoctiNIRS fNIRS device simulator for UI/bridge testing.
// Run bridge with: npm run start:local-device
// Run simulator in another terminal: npm run simulate
// ============================================================

import dgram from "dgram";
import {
  NOCTI,
  buildStreamFrame,
  buildUdpPacket,
} from "./protocol.js";

const DEVICE_CONTROL_PORT = 3333;
const TARGET_STREAM_PORT = 3334;
const socket = dgram.createSocket("udp4");

const state = {
  targetAddress: "127.0.0.1",
  running: false,
  mode: "DARK",
  selectedSlot: 0,
  current735: 10,
  current850: 10,
  sessionId: 0,
  udpSequence: 0,
  streamSequence: 0,
  frameId: 0,
  startedAtUs: 0n,
  frames: 0,
  partial: 0,
  underKSlots: 0,
};

function nowUs() {
  return process.hrtime.bigint() / 1000n;
}

function sendText(text, remoteInfo) {
  socket.send(Buffer.from(`${text.trim()}\n`, "ascii"), remoteInfo.port, remoteInfo.address);
}

function statusText() {
  return `FNIRS STATUS running=${state.running ? 1 : 0} mode=${state.mode} slot=${state.selectedSlot} ` +
    `current735=${state.current735} current850=${state.current850} frames=${state.frames} partial=${state.partial} ` +
    `underk_slots=${state.underKSlots} adc_ok=${state.frames * 200} adc_err=0 crc_bad=0 ` +
    `led_err=0 stream_err=0 drops=0 session=${state.sessionId}`;
}

function sendRecord(type, payload, relativeTimeUs = 0) {
  state.streamSequence = (state.streamSequence + 1) & 0xffff;
  const streamFrame = buildStreamFrame({
    type,
    sequence: state.streamSequence,
    relativeTimeUs,
    payload,
  });
  state.udpSequence = (state.udpSequence + 1) >>> 0;
  const packet = buildUdpPacket({
    sessionId: state.sessionId,
    sequence: state.udpSequence,
    timestampUs: nowUs(),
    payload: streamFrame,
  });
  socket.send(packet, TARGET_STREAM_PORT, state.targetAddress);
}

function sessionPayload() {
  const payload = Buffer.alloc(16);
  payload.writeUInt8(1, 0);
  payload.writeUInt32LE(state.sessionId, 4);
  payload.writeBigUInt64LE(state.startedAtUs, 8);
  return payload;
}

function configPayload() {
  const payload = Buffer.alloc(12);
  payload.writeUInt8(1, 0);
  payload.writeUInt8(0, 1);
  payload.writeUInt8(10, 2);
  payload.writeUInt8(20, 3);
  payload.writeUInt8(3, 4);
  payload.writeUInt8(state.current735, 5);
  payload.writeUInt8(state.current850, 6);
  payload.writeUInt16LE(200, 8);
  payload.writeUInt16LE(3200, 10);
  return payload;
}

function eventPayload() {
  const modeCode = state.mode === "DARK" ? 0 : state.mode === "SINGLE" ? 1 : 2;
  const argument = (modeCode & 0xff) | ((state.selectedSlot & 0xff) << 8);
  const payload = Buffer.alloc(8);
  payload.writeUInt8(1, 0);
  payload.writeUInt8(0, 1);
  payload.writeUInt16LE(0xf100, 2);
  payload.writeUInt32LE(argument, 4);
  return payload;
}

function gaussianNoise(scale = 1) {
  const u1 = Math.max(Number.EPSILON, Math.random());
  const u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2) * scale;
}

function framePayload() {
  const payload = Buffer.alloc(NOCTI.FNIRS_FRAME_PAYLOAD_BYTES);
  payload.writeUInt8(1, 0);
  payload.writeUInt8(0, 1);
  payload.writeUInt16LE(state.frameId & 0xffff, 2);
  const validMask = 0x000fffff;
  payload.writeUInt32LE(validMask, 4);
  payload.writeUInt32LE(0, 8);
  payload.writeUInt32LE(0, 12);

  const timeSeconds = state.frameId / 10;
  const averagesOffset = 16;
  const nUsedOffset = 336;

  for (let slot = 0; slot < 20; slot += 1) {
    const source = Math.floor(slot / 2);
    const wavelength = (slot & 1) === 0 ? 735 : 850;
    const active = state.mode === "SCAN" || (state.mode === "SINGLE" && slot === state.selectedSlot);
    const current = wavelength === 735 ? state.current735 : state.current850;

    for (let pd = 0; pd < 4; pd += 1) {
      const baseline = 120000 + pd * 17000 + source * 900;
      const coupling = Math.max(0.18, 1 - Math.abs(pd - (source % 4)) * 0.21);
      const optical = active ? current * 1250 * coupling : 0;
      const physiology = active
        ? 1500 * Math.sin(2 * Math.PI * (0.08 + pd * 0.01) * timeSeconds + source * 0.25)
        : 0;
      const value = Math.round(baseline + optical + physiology + gaussianNoise(180));
      payload.writeInt32LE(value, averagesOffset + (slot * 4 + pd) * 4);
    }
    payload.writeUInt8(3, nUsedOffset + slot);
  }
  return payload;
}

function startAcquisition(remoteInfo) {
  state.targetAddress = remoteInfo.address;
  state.running = true;
  state.sessionId += 1;
  state.udpSequence = 0;
  state.streamSequence = 0;
  state.frameId = 0;
  state.frames = 0;
  state.startedAtUs = nowUs();
  sendRecord(NOCTI.REC_SESSION, sessionPayload(), 0);
  sendRecord(NOCTI.REC_TIME64, (() => {
    const payload = Buffer.alloc(8);
    payload.writeBigUInt64BE(state.startedAtUs, 0);
    return payload;
  })(), 0);
  sendRecord(NOCTI.REC_FNIRS_CFG, configPayload(), 0);
  sendRecord(NOCTI.REC_EVENT, eventPayload(), 0);
  sendText(`FNIRS STARTED session=${state.sessionId} mode=${state.mode} slot=${state.selectedSlot} current735=${state.current735} current850=${state.current850}`, remoteInfo);
}

function stopAcquisition(remoteInfo) {
  state.running = false;
  sendText(`FNIRS STOPPED frames=${state.frames} adc_ok=${state.frames * 200} adc_err=0 crc_bad=0`, remoteInfo);
}

function handleCommand(packet, remoteInfo) {
  const command = packet.toString("ascii").trim().toUpperCase();
  state.targetAddress = remoteInfo.address;

  if (command === "HELLO") return sendText("NOCTI HELLO OK", remoteInfo);
  if (command === "PING") return sendText("PONG", remoteInfo);
  if (command === "INFO") return sendText("NOCTI FNIRS_TEST ctrl=3333 stream=3334 modes=DARK,SINGLE,SCAN", remoteInfo);
  if (command === "STATUS") return sendText(statusText(), remoteInfo);
  if (command === "REGS") return sendText("FNIRS REGS MODE=0x0000 CLOCK=0x0F02 GAIN=0x0000 DRDY=2000", remoteInfo);
  if (command === "START") return startAcquisition(remoteInfo);
  if (command === "STOP") return stopAcquisition(remoteInfo);

  if (command.startsWith("MODE ")) {
    if (state.running) return sendText("FNIRS ERROR stop_before_mode_change", remoteInfo);
    const mode = command.slice(5);
    if (!["DARK", "SINGLE", "SCAN"].includes(mode)) return sendText("FNIRS ERROR mode_must_be_DARK_SINGLE_or_SCAN", remoteInfo);
    state.mode = mode;
    return sendText(`FNIRS MODE ${mode}`, remoteInfo);
  }

  if (command.startsWith("SLOT ")) {
    if (state.running) return sendText("FNIRS ERROR stop_before_slot_change", remoteInfo);
    const slot = Number.parseInt(command.slice(5), 10);
    if (!Number.isInteger(slot) || slot < 0 || slot > 19) return sendText("FNIRS ERROR slot_range_0_to_19", remoteInfo);
    state.selectedSlot = slot;
    return sendText(`FNIRS SLOT ${slot} led=${Math.floor(slot / 2)} wavelength=${(slot & 1) === 0 ? 735 : 850}`, remoteInfo);
  }

  const currentMatch = command.match(/^CURRENT (735|850) (\d+)$/);
  if (currentMatch) {
    if (state.running) return sendText("FNIRS ERROR stop_before_current_change", remoteInfo);
    const wavelength = currentMatch[1];
    const current = Number.parseInt(currentMatch[2], 10);
    if (current < 0 || current > 50) return sendText("FNIRS ERROR current_range_0_to_50", remoteInfo);
    if (wavelength === "735") state.current735 = current;
    else state.current850 = current;
    return sendText(`FNIRS CURRENT ${wavelength} ${current} mA`, remoteInfo);
  }

  return sendText("FNIRS ERROR unknown_command; send HELP", remoteInfo);
}

socket.on("message", handleCommand);
socket.on("error", (error) => console.error(`[SIM] ${error.stack || error.message}`));
socket.bind(DEVICE_CONTROL_PORT, "127.0.0.1", () => {
  console.log(`[SIM] control listening on 127.0.0.1:${DEVICE_CONTROL_PORT}`);
  console.log(`[SIM] stream target 127.0.0.1:${TARGET_STREAM_PORT}`);
});

const frameTimer = setInterval(() => {
  if (!state.running) return;
  const relativeTimeUs = Number(nowUs() - state.startedAtUs) >>> 0;
  sendRecord(NOCTI.REC_FNIRS_FRAME, framePayload(), relativeTimeUs);
  state.frameId = (state.frameId + 1) & 0xffff;
  state.frames += 1;
}, 100);

function shutdown() {
  clearInterval(frameTimer);
  try { socket.close(); } catch {}
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
