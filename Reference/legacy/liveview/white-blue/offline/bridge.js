// ============================================================
// File: bridge.js
// NoctiNIRS UDP <-> WebSocket bridge and optional static server.
// The same local UDP socket (port 3334) sends control commands and
// receives both control replies and binary fNIRS stream packets.
// ============================================================

import dgram from "dgram";
import fs from "fs";
import http from "http";
import path from "path";
import { fileURLToPath } from "url";
import { WebSocket, WebSocketServer } from "ws";
import {
  NOCTI,
  decodeStreamPayload,
  parseStreamFrame,
  parseUdpPacket,
} from "./protocol.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDirectory = path.join(__dirname, "public");

function readArgument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  return fallback;
}

function readPort(name, fallback) {
  const parsed = Number.parseInt(readArgument(name, String(fallback)), 10);
  return Number.isInteger(parsed) && parsed > 0 && parsed <= 65535 ? parsed : fallback;
}

const config = {
  deviceIp: readArgument("device", process.env.NOCTI_DEVICE_IP || "192.168.4.1"),
  deviceControlPort: readPort("control-port", 3333),
  localUdpPort: readPort("stream-port", 3334),
  webSocketPort: readPort("ws-port", 8080),
  httpPort: readPort("http-port", 8000),
};

const stats = {
  bridgeStartedAt: Date.now(),
  udpPackets: 0,
  udpBytes: 0,
  udpCrcErrors: 0,
  streamCrcErrors: 0,
  malformedPackets: 0,
  unsupportedRecords: 0,
  packetLoss: 0,
  duplicateOrOutOfOrder: 0,
  fnirsFrames: 0,
  partialFrames: 0,
  underKSlots: 0,
  crcDroppedSlots: 0,
  lastUdpSequence: null,
  lastUdpSessionId: null,
  lastFrameAt: 0,
  lastControlAt: 0,
  lastDeviceAddress: null,
};

let latestFrame = null;
let latestConfig = null;
let latestSession = null;
let latestDeviceStatus = null;
let latestControlMessage = null;
let frameRateWindow = [];
let udpSocketReady = false;
let shuttingDown = false;

function isValidIpv4(address) {
  const parts = String(address).trim().split(".");
  return parts.length === 4 && parts.every((part) => {
    if (!/^\d{1,3}$/.test(part)) return false;
    const value = Number(part);
    return value >= 0 && value <= 255 && String(value) === String(Number(part));
  });
}

function safeJson(value) {
  return JSON.stringify(value, (_key, current) =>
    typeof current === "bigint" ? current.toString() : current);
}

function broadcast(message) {
  const encoded = safeJson(message);
  for (const client of webSocketServer.clients) {
    if (client.readyState === WebSocket.OPEN) client.send(encoded);
  }
}

function currentFrameRate() {
  const now = Date.now();
  frameRateWindow = frameRateWindow.filter((time) => now - time <= 3000);
  if (frameRateWindow.length < 2) return 0;
  const elapsedSeconds = (frameRateWindow.at(-1) - frameRateWindow[0]) / 1000;
  return elapsedSeconds > 0 ? (frameRateWindow.length - 1) / elapsedSeconds : 0;
}

function bridgeState() {
  const now = Date.now();
  const lastDeviceAt = Math.max(stats.lastFrameAt, stats.lastControlAt);
  return {
    type: "bridge_state",
    bridge: {
      udpReady: udpSocketReady,
      deviceIp: config.deviceIp,
      deviceControlPort: config.deviceControlPort,
      localUdpPort: config.localUdpPort,
      webSocketPort: config.webSocketPort,
      httpPort: config.httpPort,
      uptimeSeconds: Math.floor((now - stats.bridgeStartedAt) / 1000),
    },
    device: {
      online: lastDeviceAt > 0 && now - lastDeviceAt < 5000,
      lastSeenMsAgo: lastDeviceAt > 0 ? now - lastDeviceAt : null,
      lastAddress: stats.lastDeviceAddress,
      status: latestDeviceStatus,
    },
    stream: {
      frameRateHz: Number(currentFrameRate().toFixed(2)),
      ...stats,
    },
    latestConfig,
    latestSession,
  };
}

function sendState(target = null) {
  const encoded = safeJson(bridgeState());
  if (target && target.readyState === WebSocket.OPEN) target.send(encoded);
  else broadcast(bridgeState());
}

function sendDeviceCommand(command) {
  const normalized = String(command ?? "").trim().toUpperCase();
  if (!udpSocketReady) throw new Error("UDP socket is not ready");
  if (!normalized || normalized.length > 127 || /[^A-Z0-9 ._-]/.test(normalized)) {
    throw new Error("Invalid command");
  }
  const payload = Buffer.from(normalized, "ascii");
  udpSocket.send(payload, config.deviceControlPort, config.deviceIp);
  broadcast({ type: "command_tx", command: normalized, at: Date.now() });
}

function sendCommandBatch(commands) {
  const filtered = Array.isArray(commands) ? commands.slice(0, 10) : [];
  filtered.forEach((command, index) => {
    setTimeout(() => {
      try {
        sendDeviceCommand(command);
      } catch (error) {
        broadcast({ type: "bridge_error", message: error.message });
      }
    }, index * 130);
  });
}

function parseKeyValueMessage(text) {
  const values = {};
  for (const match of text.matchAll(/([A-Za-z0-9_]+)=([^\s]+)/g)) {
    const key = match[1];
    const raw = match[2];
    const numeric = Number(raw);
    values[key] = Number.isFinite(numeric) && /^-?\d+(\.\d+)?$/.test(raw) ? numeric : raw;
  }
  return values;
}

function handleControlText(buffer, remoteInfo) {
  const text = buffer.toString("utf8").replace(/\0/g, "").trim();
  if (!text) return;

  stats.lastControlAt = Date.now();
  stats.lastDeviceAddress = remoteInfo.address;
  latestControlMessage = text;

  if (text.startsWith("FNIRS STATUS")) {
    latestDeviceStatus = parseKeyValueMessage(text);
  } else if (text.startsWith("FNIRS STARTED")) {
    latestDeviceStatus = {
      ...(latestDeviceStatus ?? {}),
      ...parseKeyValueMessage(text),
      running: 1,
    };
  } else if (text.startsWith("FNIRS STOPPED")) {
    latestDeviceStatus = {
      ...(latestDeviceStatus ?? {}),
      ...parseKeyValueMessage(text),
      running: 0,
    };
  } else if (text.startsWith("FNIRS MODE")) {
    latestDeviceStatus = {
      ...(latestDeviceStatus ?? {}),
      mode: text.split(/\s+/).at(-1),
    };
  } else if (text.startsWith("FNIRS SLOT")) {
    latestDeviceStatus = {
      ...(latestDeviceStatus ?? {}),
      ...parseKeyValueMessage(text),
      slot: Number.parseInt(text.split(/\s+/)[2], 10),
    };
  } else if (text.startsWith("FNIRS CURRENT")) {
    const tokens = text.split(/\s+/);
    const wavelength = tokens[2];
    const current = Number.parseInt(tokens[3], 10);
    if (wavelength === "735") latestDeviceStatus = { ...(latestDeviceStatus ?? {}), current735: current };
    if (wavelength === "850") latestDeviceStatus = { ...(latestDeviceStatus ?? {}), current850: current };
  }

  broadcast({
    type: "control_rx",
    message: text,
    from: `${remoteInfo.address}:${remoteInfo.port}`,
    at: Date.now(),
  });
}

function looksLikeText(buffer) {
  if (buffer.length === 0 || buffer.length > 512) return false;
  let printable = 0;
  for (const byte of buffer) {
    if (byte === 9 || byte === 10 || byte === 13 || (byte >= 32 && byte <= 126)) printable += 1;
  }
  return printable / buffer.length > 0.95;
}

function updatePacketLoss(sessionId, sequence) {
  if (stats.lastUdpSessionId !== sessionId) {
    stats.lastUdpSessionId = sessionId;
    stats.lastUdpSequence = sequence;
    return;
  }

  if (stats.lastUdpSequence === null) {
    stats.lastUdpSequence = sequence;
    return;
  }

  const difference = (sequence - stats.lastUdpSequence) >>> 0;
  if (difference === 0 || difference >= 0x80000000) {
    stats.duplicateOrOutOfOrder += 1;
  } else {
    if (difference > 1) stats.packetLoss += difference - 1;
    stats.lastUdpSequence = sequence;
  }
}

function handleStreamPacket(packet, remoteInfo) {
  const udp = parseUdpPacket(packet);
  if (!udp.ok) {
    stats.malformedPackets += 1;
    broadcast({ type: "decode_warning", layer: "udp", error: udp.error });
    return;
  }
  if (!udp.crcOk) {
    stats.udpCrcErrors += 1;
    broadcast({ type: "decode_warning", layer: "udp", error: "crc" });
    return;
  }

  stats.udpPackets += 1;
  stats.udpBytes += packet.length;
  stats.lastFrameAt = Date.now();
  stats.lastDeviceAddress = remoteInfo.address;
  updatePacketLoss(udp.header.sessionId, udp.header.sequence);

  const stream = parseStreamFrame(udp.payload);
  if (!stream.ok) {
    stats.malformedPackets += 1;
    broadcast({ type: "decode_warning", layer: "stream", error: stream.error });
    return;
  }
  if (!stream.crcOk) {
    stats.streamCrcErrors += 1;
    broadcast({ type: "decode_warning", layer: "stream", error: "crc" });
    return;
  }

  const decoded = decodeStreamPayload(stream.header.type, stream.payload);
  const envelope = {
    udp: {
      sessionId: udp.header.sessionId,
      sequence: udp.header.sequence,
      timestampUs: udp.header.timestampUs.toString(),
    },
    stream: {
      sequence: stream.header.sequence,
      relativeTimeUs: stream.header.relativeTimeUs,
      recordType: stream.header.type,
    },
  };

  if (decoded.kind === "fnirs_frame") {
    stats.fnirsFrames += 1;
    if (decoded.partial) stats.partialFrames += 1;
    stats.underKSlots += decoded.underKSlotCount;
    stats.crcDroppedSlots += decoded.crcSlotCount;
    frameRateWindow.push(Date.now());
    latestFrame = { type: "fnirs_frame", ...envelope, data: decoded };
    broadcast(latestFrame);
    return;
  }

  if (decoded.kind === "fnirs_config") latestConfig = decoded;
  if (decoded.kind === "session") latestSession = decoded;
  if (decoded.kind === "unsupported") stats.unsupportedRecords += 1;

  broadcast({ type: decoded.kind, ...envelope, data: decoded });
}

const udpSocket = dgram.createSocket("udp4");
udpSocket.on("error", (error) => {
  console.error(`[UDP] ${error.stack || error.message}`);
  broadcast({ type: "bridge_error", message: `UDP: ${error.message}` });
});
udpSocket.on("message", (packet, remoteInfo) => {
  if (looksLikeText(packet)) handleControlText(packet, remoteInfo);
  else handleStreamPacket(packet, remoteInfo);
});
udpSocket.on("listening", () => {
  udpSocketReady = true;
  const address = udpSocket.address();
  console.log(`[UDP] listening on ${address.address}:${address.port}`);
  console.log(`[UDP] device control target ${config.deviceIp}:${config.deviceControlPort}`);
  setTimeout(() => sendCommandBatch(["HELLO", "INFO", "STATUS"]), 250);
});
udpSocket.bind(config.localUdpPort, "0.0.0.0");

const webSocketServer = new WebSocketServer({ port: config.webSocketPort });
webSocketServer.on("listening", () => {
  console.log(`[WS]  ws://localhost:${config.webSocketPort}`);
});
webSocketServer.on("connection", (socket) => {
  sendState(socket);
  if (latestControlMessage) {
    socket.send(safeJson({ type: "control_rx", message: latestControlMessage, at: Date.now() }));
  }
  if (latestFrame) socket.send(safeJson(latestFrame));

  socket.on("message", (raw) => {
    try {
      const message = JSON.parse(raw.toString());
      if (message.type === "command") {
        sendDeviceCommand(message.command);
      } else if (message.type === "command_batch") {
        sendCommandBatch(message.commands);
      } else if (message.type === "get_state") {
        sendState(socket);
      } else if (message.type === "set_device_ip") {
        if (!isValidIpv4(message.deviceIp)) throw new Error("Invalid IPv4 address");
        config.deviceIp = message.deviceIp;
        broadcast({ type: "bridge_notice", message: `Device IP set to ${config.deviceIp}` });
        sendCommandBatch(["HELLO", "INFO", "STATUS"]);
        sendState();
      }
    } catch (error) {
      socket.send(safeJson({ type: "bridge_error", message: error.message }));
    }
  });
});

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
};

const httpServer = http.createServer((request, response) => {
  if (request.url === "/health") {
    response.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
    response.end(safeJson(bridgeState()));
    return;
  }

  const requestedPath = decodeURIComponent((request.url || "/").split("?")[0]);
  const relativePath = requestedPath === "/" ? "index.html" : requestedPath.replace(/^\/+/, "");
  const absolutePath = path.resolve(publicDirectory, relativePath);
  if (!absolutePath.startsWith(`${path.resolve(publicDirectory)}${path.sep}`) && absolutePath !== path.resolve(publicDirectory, "index.html")) {
    response.writeHead(403).end("Forbidden");
    return;
  }

  fs.readFile(absolutePath, (error, content) => {
    if (error) {
      response.writeHead(error.code === "ENOENT" ? 404 : 500).end(error.code === "ENOENT" ? "Not found" : "Server error");
      return;
    }
    response.writeHead(200, {
      "Content-Type": mimeTypes[path.extname(absolutePath).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    response.end(content);
  });
});
httpServer.listen(config.httpPort, "127.0.0.1", () => {
  console.log(`[HTTP] http://localhost:${config.httpPort}`);
  console.log("[INFO] You may also open public/index.html with VS Code Live Server.");
});

const maintenanceTimer = setInterval(() => {
  const now = Date.now();
  const lastSeen = Math.max(stats.lastFrameAt, stats.lastControlAt);
  if (udpSocketReady) {
    if (lastSeen === 0 || now - lastSeen > 5000) sendDeviceCommand("HELLO");
    else if (now - stats.lastControlAt > 1800) sendDeviceCommand("STATUS");
  }
  sendState();
}, 2000);

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  clearInterval(maintenanceTimer);
  console.log("\n[INFO] shutting down...");
  try { udpSocket.close(); } catch {}
  try { webSocketServer.close(); } catch {}
  try { httpServer.close(); } catch {}
  setTimeout(() => process.exit(0), 100).unref();
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
