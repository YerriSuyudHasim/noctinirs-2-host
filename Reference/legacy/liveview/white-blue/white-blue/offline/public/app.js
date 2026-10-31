import {
  LONG_CHANNELS,
  SHORT_CHANNELS,
  extractNamedIntensities,
  estimateReference,
  mbllFromOpticalDensity,
  opticalDensity,
  resolveDpf,
  scholkmannWolfDpf,
  RollingShortSeparationRegressor,
} from "./lib/noctinirs-fnirs.js";

const $ = (id) => document.getElementById(id);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const elements = {
  welcomeScreen: $("welcomeScreen"), liveScreen: $("liveScreen"), setupDialog: $("setupDialog"), settingsDialog: $("settingsDialog"), diagnosticsDialog: $("diagnosticsDialog"),
  newSessionButton: $("newSessionButton"), demoButton: $("demoButton"), welcomeConnectButton: $("welcomeConnectButton"), quickMbllButton: $("quickMbllButton"), useLastConfigButton: $("useLastConfigButton"),
  welcomeWsDot: $("welcomeWsDot"), welcomeWsText: $("welcomeWsText"), welcomeDeviceDot: $("welcomeDeviceDot"), welcomeDeviceText: $("welcomeDeviceText"), welcomeDeviceStatus: $("welcomeDeviceStatus"), welcomeDeviceMeta: $("welcomeDeviceMeta"),
  lastConfigLabel: $("lastConfigLabel"), lastScrLabel: $("lastScrLabel"), probeNodes: $("probeNodes"),
  homeButton: $("homeButton"), liveStatePill: $("liveStatePill"), liveStateText: $("liveStateText"), elapsedValue: $("elapsedValue"), frameRateValue: $("frameRateValue"), frameCountValue: $("frameCountValue"), packetLossValue: $("packetLossValue"), baselineStatusValue: $("baselineStatusValue"),
  diagnosticsButton: $("diagnosticsButton"), settingsButton: $("settingsButton"), stopSessionButton: $("stopSessionButton"), startPauseButton: $("startPauseButton"), startPauseIcon: $("startPauseIcon"), startPauseText: $("startPauseText"), pauseDisplayButton: $("pauseDisplayButton"), markerButton: $("markerButton"),
  windowSelect: $("windowSelect"), scaleSelect: $("scaleSelect"), resetBaselineButton: $("resetBaselineButton"), sidebarDpf735: $("sidebarDpf735"), sidebarDpf850: $("sidebarDpf850"), sidebarScrMode: $("sidebarScrMode"), sidebarRegression: $("sidebarRegression"), sidebarDeviceDot: $("sidebarDeviceDot"), sidebarDeviceText: $("sidebarDeviceText"), sessionNameFooter: $("sessionNameFooter"),
  displayNotice: $("displayNotice"), clearSweepsButton: $("clearSweepsButton"), leftChannelGrid: $("leftChannelGrid"), rightChannelGrid: $("rightChannelGrid"), channelCardTemplate: $("channelCardTemplate"),
  selectedChannelName: $("selectedChannelName"), selectedHemisphere: $("selectedHemisphere"), selectedGeometry: $("selectedGeometry"), selectedQualityDot: $("selectedQualityDot"), selectedHbO: $("selectedHbO"), selectedHbR: $("selectedHbR"), selectedHbT: $("selectedHbT"), selectedI735Label: $("selectedI735Label"), selectedI850Label: $("selectedI850Label"), selectedOD735Label: $("selectedOD735Label"), selectedOD850Label: $("selectedOD850Label"), selectedI735: $("selectedI735"), selectedI850: $("selectedI850"), selectedOD735: $("selectedOD735"), selectedOD850: $("selectedOD850"), selectedScrMode: $("selectedScrMode"), selectedRegressor: $("selectedRegressor"), selectedBeta735: $("selectedBeta735"), selectedBeta850: $("selectedBeta850"), qualityMeterFill: $("qualityMeterFill"), selectedQualityText: $("selectedQualityText"), selectedValidSamples: $("selectedValidSamples"),
  setupForm: $("setupForm"), setupBackButton: $("setupBackButton"), setupNextButton: $("setupNextButton"), launchLiveButton: $("launchLiveButton"), setupConnectButton: $("setupConnectButton"), setupDeviceBadge: $("setupDeviceBadge"),
  deviceIpInput: $("deviceIpInput"), modeSelect: $("modeSelect"), current735Input: $("current735Input"), current850Input: $("current850Input"), baselineDurationInput: $("baselineDurationInput"), baselineEstimatorSelect: $("baselineEstimatorSelect"), dpf735Input: $("dpf735Input"), dpf850Input: $("dpf850Input"), ageYearsInput: $("ageYearsInput"), computedDpfText: $("computedDpfText"), manualDpfFields: $("manualDpfFields"), ageDpfFields: $("ageDpfFields"), sessionNameInput: $("sessionNameInput"), subjectIdInput: $("subjectIdInput"), protocolSelect: $("protocolSelect"), operatorInput: $("operatorInput"), sessionNotesInput: $("sessionNotesInput"),
  liveScrModeSelect: $("liveScrModeSelect"), liveDpf735Input: $("liveDpf735Input"), liveDpf850Input: $("liveDpf850Input"), liveBaselineDurationInput: $("liveBaselineDurationInput"), applyLiveSettingsButton: $("applyLiveSettingsButton"),
  diagWsValue: $("diagWsValue"), diagDeviceValue: $("diagDeviceValue"), diagSessionValue: $("diagSessionValue"), diagLastFrameValue: $("diagLastFrameValue"), diagCrcValue: $("diagCrcValue"), diagFrameQualityValue: $("diagFrameQualityValue"), deviceLog: $("deviceLog"), clearLogButton: $("clearLogButton"), requestStatusButton: $("requestStatusButton"), markerMenu: $("markerMenu"),
};

const DEFAULT_CONFIG = Object.freeze({
  deviceIp: "192.168.4.1",
  mode: "SCAN",
  current735: 15,
  current850: 40,
  baselineDuration: 10,
  baselineEstimator: "median",
  dpfMode: "manual",
  dpf735: 6.0,
  dpf850: 6.0,
  ageYears: 28,
  scrMode: "none",
  sessionName: "NoctiNIRS Live Session",
  subjectId: "",
  protocol: "Resting state",
  operator: "",
  notes: "",
});

const storedConfig = (() => {
  try { return JSON.parse(localStorage.getItem("noctinirs-ui-config") ?? "null"); }
  catch { return null; }
})();

const state = {
  socket: null,
  reconnectAttempt: 0,
  reconnectTimer: null,
  bridgeConnected: false,
  deviceOnline: false,
  deviceRunning: false,
  running: false,
  demo: false,
  displayPaused: false,
  selectedChannelId: "CH1",
  viewMode: "hb",
  config: { ...DEFAULT_CONFIG, ...(storedConfig ?? {}) },
  dpf: { dpf735: 6, dpf850: 6, mode: "manual" },
  frameCount: 0,
  frameRateHz: 10,
  packetLoss: 0,
  sessionId: "—",
  sessionStartedAt: null,
  lastFrameAt: 0,
  lastFrameQuality: "—",
  sampleIndex: 0,
  demoTimer: null,
  demoStart: 0,
  baseline: { ready: false, samples: {}, references: {}, count: 0, target: 100 },
  latestSamples: new Map(),
  validSamples: new Map(),
  regressionModels: new Map(),
  regressors: new Map(),
  plots: new Map(),
  cards: new Map(),
  markers: [],
};

function setDot(element, status) {
  element.classList.remove("online", "offline", "pending");
  element.classList.add(status);
}

function setLiveState(kind, text) {
  elements.liveStatePill.classList.remove("idle", "live", "baseline", "demo");
  elements.liveStatePill.classList.add(kind);
  elements.liveStateText.textContent = text;
}

function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const secs = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function formatNumeric(value, digits = 2) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "—";
}

function formatCount(value) {
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
}

function scrLabel(mode) {
  return ({ none: "Disabled", "5mm": "5 mm", "8mm": "8 mm", both: "5 + 8 mm" })[mode] ?? mode;
}

function regressionLabel(mode) {
  if (mode === "none") return "—";
  return mode === "both" ? "Ridge" : "OLS";
}

function logLine(message, direction = "info") {
  const time = new Date().toLocaleTimeString([], { hour12: false });
  const prefix = direction === "tx" ? "→" : direction === "rx" ? "←" : "·";
  elements.deviceLog.textContent += `[${time}] ${prefix} ${message}\n`;
  const lines = elements.deviceLog.textContent.split("\n");
  if (lines.length > 260) elements.deviceLog.textContent = lines.slice(-230).join("\n");
  elements.deviceLog.scrollTop = elements.deviceLog.scrollHeight;
}

function saveConfig() {
  localStorage.setItem("noctinirs-ui-config", JSON.stringify(state.config));
  elements.lastConfigLabel.textContent = `DPF ${state.config.dpf735.toFixed(2)} / ${state.config.dpf850.toFixed(2)}`;
  elements.lastScrLabel.textContent = `Short separation ${scrLabel(state.config.scrMode).toLowerCase()}`;
}

function resolveCurrentDpf() {
  state.dpf = resolveDpf({
    dpfMode: state.config.dpfMode,
    dpf735: state.config.dpf735,
    dpf850: state.config.dpf850,
    ageYears: state.config.ageYears,
  });
  elements.sidebarDpf735.textContent = state.dpf.dpf735.toFixed(2);
  elements.sidebarDpf850.textContent = state.dpf.dpf850.toFixed(2);
  elements.sidebarScrMode.textContent = scrLabel(state.config.scrMode);
  elements.sidebarRegression.textContent = regressionLabel(state.config.scrMode);
}

/* Probe preview */
function makeProbeNode(label, type, x, y) {
  const node = document.createElement("span");
  node.className = `probe-node ${type}`;
  node.textContent = label;
  node.style.left = `${x}%`;
  node.style.top = `${y}%`;
  elements.probeNodes.append(node);
  return { x, y };
}

function makeProbeLine(a, b) {
  const line = document.createElement("span");
  line.className = "probe-line";
  const dx = b.x - a.x, dy = b.y - a.y;
  const width = Math.sqrt(dx * dx + dy * dy);
  line.style.left = `${a.x}%`;
  line.style.top = `${a.y}%`;
  line.style.width = `${width}%`;
  line.style.transform = `rotate(${Math.atan2(dy, dx) * 180 / Math.PI}deg)`;
  elements.probeNodes.append(line);
}

function buildProbePreview() {
  const leftPD3 = makeProbeNode("P3", "detector", 28, 40);
  const leftPD2 = makeProbeNode("P2", "detector", 32, 62);
  const rightPD0 = makeProbeNode("P0", "detector", 72, 40);
  const rightPD1 = makeProbeNode("P1", "detector", 68, 62);
  const leftPositions = [["L1", 24, 52], ["L2", 16, 31], ["L3", 13, 48], ["L4", 15, 68], ["L5", 23, 78]];
  const rightPositions = [["L6", 76, 52], ["L7", 84, 31], ["L8", 87, 48], ["L9", 85, 68], ["L10", 77, 78]];
  leftPositions.forEach(([label, x, y], index) => {
    const source = makeProbeNode(label, "source", x, y);
    if (index > 0) { makeProbeLine(source, leftPD3); makeProbeLine(source, leftPD2); }
  });
  rightPositions.forEach(([label, x, y], index) => {
    const source = makeProbeNode(label, "source", x, y);
    if (index > 0) { makeProbeLine(source, rightPD0); makeProbeLine(source, rightPD1); }
  });
}

/* ECG-style canvas renderer. During normal live updates, only the new
   segment and a narrow erase gap are touched. Full redraw occurs only
   after resize, window change, scale change, or explicit clear. */
class SweepPlot {
  constructor(card, channel) {
    this.card = card;
    this.channel = channel;
    this.canvas = card.querySelector("canvas");
    this.cursor = card.querySelector(".sweep-cursor");
    this.scaleTop = card.querySelector(".scale-top");
    this.scaleBottom = card.querySelector(".scale-bottom");
    this.ctx = this.canvas.getContext("2d");
    this.previous = null;
    this.width = 0;
    this.height = 0;
    this.ratio = 1;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(card.querySelector(".plot-stage"));
    this.resize();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(100, Math.floor(rect.width));
    const height = Math.max(50, Math.floor(rect.height));
    const ratio = window.devicePixelRatio || 1;
    if (width === this.width && height === this.height && ratio === this.ratio) return;
    this.width = width; this.height = height; this.ratio = ratio;
    this.canvas.width = Math.floor(width * ratio);
    this.canvas.height = Math.floor(height * ratio);
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.clear();
  }

  clear() {
    this.ctx.clearRect(0, 0, this.width, this.height);
    this.previous = null;
    this.cursor.style.left = "0px";
    this.updateScaleLabels();
  }

  scaleDefinition() {
    if (state.viewMode === "hb") {
      const value = Number(elements.scaleSelect.value || 5);
      return { min: -value, max: value, top: `+${value} µM`, bottom: `−${value}` };
    }
    if (state.viewMode === "od") return { min: -20, max: 20, top: "+20 mOD", bottom: "−20" };
    return { min: -5, max: 5, top: "+5 %", bottom: "−5" };
  }

  updateScaleLabels() {
    const scale = this.scaleDefinition();
    this.scaleTop.textContent = scale.top;
    this.scaleBottom.textContent = scale.bottom;
  }

  mapY(value, scale) {
    const normalized = (Math.max(scale.min, Math.min(scale.max, value)) - scale.min) / (scale.max - scale.min);
    return this.height - normalized * this.height;
  }

  drawSegment(previous, current, stroke, dashed = false) {
    if (!Number.isFinite(previous.value) || !Number.isFinite(current.value)) return;
    const scale = this.scaleDefinition();
    this.ctx.save();
    this.ctx.strokeStyle = stroke;
    this.ctx.lineWidth = 1.55;
    this.ctx.lineJoin = "round";
    this.ctx.lineCap = "round";
    this.ctx.setLineDash(dashed ? [4, 2.7] : []);
    this.ctx.beginPath();
    this.ctx.moveTo(previous.x, this.mapY(previous.value, scale));
    this.ctx.lineTo(current.x, this.mapY(current.value, scale));
    this.ctx.stroke();
    this.ctx.restore();
  }

  push(sampleIndex, firstValue, secondValue) {
    if (state.displayPaused || this.width <= 0) return;
    const sampleRate = Math.max(1, state.frameRateHz || 10);
    const pointCount = Math.max(20, Math.round(Number(elements.windowSelect.value) * sampleRate));
    const slot = sampleIndex % pointCount;
    const x = pointCount <= 1 ? 0 : (slot / (pointCount - 1)) * this.width;
    const wrapped = this.previous && (slot === 0 || x <= this.previous.x);
    if (wrapped) this.previous = null;

    const eraseWidth = Math.max(8, this.width / pointCount * 6);
    this.ctx.clearRect(x + 2, 0, eraseWidth, this.height);
    if (x + eraseWidth > this.width) this.ctx.clearRect(0, 0, x + eraseWidth - this.width, this.height);

    const currentFirst = { x, value: firstValue };
    const currentSecond = { x, value: secondValue };
    if (this.previous && Math.abs(x - this.previous.x) < this.width / 3) {
      this.drawSegment(this.previous.first, currentFirst, getComputedStyle(document.documentElement).getPropertyValue("--hbo").trim(), false);
      this.drawSegment(this.previous.second, currentSecond, getComputedStyle(document.documentElement).getPropertyValue("--hbr").trim(), true);
    }
    this.previous = { x, first: currentFirst, second: currentSecond };
    this.cursor.style.left = `${Math.min(this.width - 1, x)}px`;
  }
}

function createChannelCards() {
  for (const channel of LONG_CHANNELS) {
    const fragment = elements.channelCardTemplate.content.cloneNode(true);
    const card = fragment.querySelector(".channel-card");
    card.dataset.channel = channel.id;
    card.querySelector(".channel-name").textContent = channel.id;
    card.querySelector(".channel-geometry").textContent = `${channel.source}–${channel.detector} · ${channel.distanceMm.toFixed(1)} mm`;
    card.addEventListener("click", () => selectChannel(channel.id));
    card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") selectChannel(channel.id); });
    const target = channel.hemisphere === "left" ? elements.leftChannelGrid : elements.rightChannelGrid;
    target.append(fragment);
    const inserted = target.lastElementChild;
    state.cards.set(channel.id, inserted);
    state.plots.set(channel.id, new SweepPlot(inserted, channel));
    state.validSamples.set(channel.id, 0);
  }
  selectChannel("CH1");
}

function selectChannel(channelId) {
  state.selectedChannelId = channelId;
  for (const [id, card] of state.cards) card.classList.toggle("selected", id === channelId);
  const channel = LONG_CHANNELS.find((entry) => entry.id === channelId);
  elements.selectedChannelName.textContent = channel.id;
  elements.selectedHemisphere.textContent = channel.hemisphere.toUpperCase();
  elements.selectedGeometry.textContent = `${channel.source}–${channel.detector} · ${channel.distanceMm.toFixed(1)} mm`;
  elements.selectedI735Label.textContent = `${channel.id}_I735`;
  elements.selectedI850Label.textContent = `${channel.id}_I850`;
  elements.selectedOD735Label.textContent = `${channel.id}_OD735`;
  elements.selectedOD850Label.textContent = `${channel.id}_OD850`;
  updateSelectedDetails();
}

function clearAllSweeps(message = "Sweeps cleared") {
  state.sampleIndex = 0;
  state.plots.forEach((plot) => plot.clear());
  elements.displayNotice.textContent = message;
}

function resetRegressors() {
  state.regressors.clear();
  state.regressionModels.clear();
  if (state.config.scrMode === "none") return;
  for (const channel of LONG_CHANNELS) {
    for (const wavelength of [735, 850]) {
      state.regressors.set(`${channel.id}_${wavelength}`, new RollingShortSeparationRegressor({
        enabled: true,
        mode: state.config.scrMode,
        method: "auto",
        ridgeLambda: 1e-3,
        minimumSamples: 30,
        windowSamples: Math.max(60, Math.round((state.frameRateHz || 10) * 30)),
      }));
    }
  }
}

function resetBaseline(reason = "Collecting baseline") {
  const target = Math.max(3, Math.round(Number(state.config.baselineDuration) * Math.max(1, state.frameRateHz || 10)));
  state.baseline = { ready: false, samples: {}, references: {}, count: 0, target };
  state.latestSamples.clear();
  state.validSamples = new Map(LONG_CHANNELS.map((entry) => [entry.id, 0]));
  resetRegressors();
  clearAllSweeps(reason);
  elements.baselineStatusValue.textContent = `0 / ${state.config.baselineDuration}s`;
  if (state.running && !state.demo) setLiveState("baseline", "BASELINE");
}

function collectBaseline(named) {
  for (const [key, value] of Object.entries(named)) {
    if (!key.endsWith("735") && !key.endsWith("850")) continue;
    if (!Number.isFinite(value) || value <= 0) continue;
    if (!state.baseline.samples[key]) state.baseline.samples[key] = [];
    state.baseline.samples[key].push(value);
  }
  state.baseline.count += 1;
  const progressSeconds = state.baseline.count / Math.max(1, state.frameRateHz || 10);
  elements.baselineStatusValue.textContent = `${Math.min(state.config.baselineDuration, progressSeconds).toFixed(1)} / ${state.config.baselineDuration}s`;
  elements.displayNotice.textContent = `Baseline ${Math.min(100, Math.round(state.baseline.count / state.baseline.target * 100))}%`;
  if (state.baseline.count < state.baseline.target) return false;

  for (const [key, values] of Object.entries(state.baseline.samples)) {
    try { state.baseline.references[key] = estimateReference(values, state.config.baselineEstimator); }
    catch { state.baseline.references[key] = null; }
  }
  state.baseline.ready = true;
  elements.baselineStatusValue.textContent = "Ready";
  elements.displayNotice.textContent = "Baseline locked";
  setLiveState("live", "LIVE");
  resetRegressors();
  return true;
}

function safeOd(value, reference) {
  try {
    if (!Number.isFinite(value) || !Number.isFinite(reference) || value <= 0 || reference <= 0) return null;
    return opticalDensity(value, reference);
  } catch { return null; }
}

function shortIdsForHemisphere(hemisphere) {
  return hemisphere === "left" ? { short5: "SC1L", short8: "SC2L" } : { short5: "SC1R", short8: "SC2R" };
}

function createSampleFromLive(channel, named, shortOd) {
  const I735 = named[`${channel.id}_I735`];
  const I850 = named[`${channel.id}_I850`];
  const ref735 = state.baseline.references[`${channel.id}_I735`];
  const ref850 = state.baseline.references[`${channel.id}_I850`];
  const od735 = safeOd(I735, ref735);
  const od850 = safeOd(I850, ref850);
  if (!Number.isFinite(od735) || !Number.isFinite(od850)) return null;

  let raw;
  try {
    raw = mbllFromOpticalDensity({ od735, od850, distanceMm: channel.distanceMm, dpf735: state.dpf.dpf735, dpf850: state.dpf.dpf850 });
  } catch { return null; }

  let correctedOd735 = od735, correctedOd850 = od850;
  let beta735 = null, beta850 = null, regressorReady = state.config.scrMode === "none";
  if (state.config.scrMode !== "none") {
    const ids = shortIdsForHemisphere(channel.hemisphere);
    const reg735 = state.regressors.get(`${channel.id}_735`);
    const reg850 = state.regressors.get(`${channel.id}_850`);
    const result735 = reg735?.push({ long: od735, short5: shortOd[`${ids.short5}_735`], short8: shortOd[`${ids.short8}_735`] });
    const result850 = reg850?.push({ long: od850, short5: shortOd[`${ids.short5}_850`], short8: shortOd[`${ids.short8}_850`] });
    if (result735?.ready && result850?.ready && Number.isFinite(result735.corrected) && Number.isFinite(result850.corrected)) {
      correctedOd735 = result735.corrected;
      correctedOd850 = result850.corrected;
      regressorReady = true;
      beta735 = result735.model?.coefficients ?? null;
      beta850 = result850.model?.coefficients ?? null;
      state.regressionModels.set(channel.id, { result735, result850 });
    }
  }

  let corrected = raw;
  if (regressorReady) {
    try {
      corrected = mbllFromOpticalDensity({ od735: correctedOd735, od850: correctedOd850, distanceMm: channel.distanceMm, dpf735: state.dpf.dpf735, dpf850: state.dpf.dpf850 });
    } catch { corrected = raw; }
  }

  return {
    channelId: channel.id,
    I735, I850, od735, od850,
    intensity735Pct: Number.isFinite(ref735) ? (I735 / ref735 - 1) * 100 : 0,
    intensity850Pct: Number.isFinite(ref850) ? (I850 / ref850 - 1) * 100 : 0,
    raw, corrected,
    displayHb: state.config.scrMode === "none" || !regressorReady ? raw : corrected,
    regressorReady,
    beta735,
    beta850,
    valid: true,
  };
}

function processLiveFrame(message) {
  const named = extractNamedIntensities(message.data);
  if (!state.baseline.ready) {
    collectBaseline(named);
    return;
  }

  const shortOd = {};
  for (const short of SHORT_CHANNELS) {
    for (const wavelength of [735, 850]) {
      const key = `${short.id}_I${wavelength}`;
      shortOd[`${short.id}_${wavelength}`] = safeOd(named[key], state.baseline.references[key]);
    }
  }

  for (const channel of LONG_CHANNELS) {
    const sample = createSampleFromLive(channel, named, shortOd);
    if (!sample) {
      setChannelQuality(channel.id, "error", "INVALID");
      continue;
    }
    deliverSample(channel, sample);
  }
  state.sampleIndex += 1;
}

function demoSample(channel, time) {
  const n = channel.channelNumber;
  const phase = n * 0.34 + (channel.hemisphere === "right" ? 0.35 : 0);
  const slow = Math.sin(2 * Math.PI * 0.085 * time + phase);
  const verySlow = Math.sin(2 * Math.PI * 0.026 * time + phase * .4);
  const respiratory = Math.sin(2 * Math.PI * 0.22 * time + phase * 1.7);
  const pulse = Math.sin(2 * Math.PI * 1.05 * time + phase * 2.2);
  const drift = (n % 3 - 1) * 0.08 * Math.sin(2 * Math.PI * 0.012 * time);
  const HbO = 1.15 * slow + .38 * verySlow + .12 * respiratory + .035 * pulse + drift;
  const HbR = -.52 * slow + .18 * verySlow + .07 * respiratory - .02 * pulse - drift * .4;
  const ref735 = 135000 + n * 3100;
  const ref850 = 165000 + n * 2600;
  const od735 = (HbO * .0009 + HbR * .0018) * channel.distanceCm;
  const od850 = (HbO * .0017 + HbR * .0008) * channel.distanceCm;
  const I735 = ref735 * Math.exp(-od735);
  const I850 = ref850 * Math.exp(-od850);
  const nuisance = .22 * Math.sin(2 * Math.PI * .1 * time + phase * .25);
  const correctionFactor = state.config.scrMode === "none" ? 0 : state.config.scrMode === "both" ? .18 : .12;
  const correctedHbO = HbO - correctionFactor * nuisance;
  const correctedHbR = HbR + correctionFactor * nuisance * .35;
  return {
    channelId: channel.id,
    I735, I850, od735, od850,
    intensity735Pct: (I735 / ref735 - 1) * 100,
    intensity850Pct: (I850 / ref850 - 1) * 100,
    raw: { HbO, HbR, HbT: HbO + HbR },
    corrected: { HbO: correctedHbO, HbR: correctedHbR, HbT: correctedHbO + correctedHbR },
    displayHb: state.config.scrMode === "none" ? { HbO, HbR, HbT: HbO + HbR } : { HbO: correctedHbO, HbR: correctedHbR, HbT: correctedHbO + correctedHbR },
    regressorReady: true,
    beta735: state.config.scrMode === "none" ? null : state.config.scrMode === "both" ? [.31, .46] : [.42],
    beta850: state.config.scrMode === "none" ? null : state.config.scrMode === "both" ? [.27, .39] : [.36],
    valid: true,
  };
}

function valuesForView(sample) {
  if (state.viewMode === "od") return [sample.od735 * 1000, sample.od850 * 1000];
  if (state.viewMode === "intensity") return [sample.intensity735Pct, sample.intensity850Pct];
  return [sample.displayHb.HbO, sample.displayHb.HbR];
}

function deliverSample(channel, sample) {
  state.latestSamples.set(channel.id, sample);
  state.validSamples.set(channel.id, (state.validSamples.get(channel.id) ?? 0) + 1);
  const values = valuesForView(sample);
  state.plots.get(channel.id)?.push(state.sampleIndex, values[0], values[1]);
  updateChannelCard(channel, sample, values);
  if (state.selectedChannelId === channel.id) updateSelectedDetails();
}

function updateChannelCard(channel, sample, values) {
  const card = state.cards.get(channel.id);
  if (!card) return;
  const first = card.querySelector(".latest-hbo");
  const second = card.querySelector(".latest-hbr");
  if (state.viewMode === "hb") {
    first.textContent = `${values[0] >= 0 ? "+" : ""}${formatNumeric(values[0], 2)}`;
    second.textContent = `${values[1] >= 0 ? "+" : ""}${formatNumeric(values[1], 2)} µM`;
  } else if (state.viewMode === "od") {
    first.textContent = `${formatNumeric(values[0], 2)}`;
    second.textContent = `${formatNumeric(values[1], 2)} mOD`;
  } else {
    first.textContent = `${formatNumeric(values[0], 2)}`;
    second.textContent = `${formatNumeric(values[1], 2)} %`;
  }
  setChannelQuality(channel.id, sample.regressorReady || state.config.scrMode === "none" ? "good" : "warn", sample.regressorReady || state.config.scrMode === "none" ? "GOOD" : "WARM");
}

function setChannelQuality(channelId, kind, text) {
  const chip = state.cards.get(channelId)?.querySelector(".quality-chip");
  if (!chip) return;
  chip.classList.remove("good", "warn", "error");
  chip.classList.add(kind);
  chip.textContent = text;
}

function betaText(beta) {
  if (!Array.isArray(beta)) return "—";
  return beta.map((value) => formatNumeric(value, 3)).join(" / ");
}

function updateSelectedDetails() {
  const channelId = state.selectedChannelId;
  const sample = state.latestSamples.get(channelId);
  const valid = state.validSamples.get(channelId) ?? 0;
  elements.selectedValidSamples.textContent = valid.toLocaleString();
  elements.selectedScrMode.textContent = scrLabel(state.config.scrMode);
  const channel = LONG_CHANNELS.find((entry) => entry.id === channelId);
  const shortIds = shortIdsForHemisphere(channel.hemisphere);
  elements.selectedRegressor.textContent = state.config.scrMode === "none" ? "—" : state.config.scrMode === "5mm" ? shortIds.short5 : state.config.scrMode === "8mm" ? shortIds.short8 : `${shortIds.short5} + ${shortIds.short8}`;

  if (!sample) {
    [elements.selectedHbO, elements.selectedHbR, elements.selectedHbT, elements.selectedI735, elements.selectedI850, elements.selectedOD735, elements.selectedOD850, elements.selectedBeta735, elements.selectedBeta850].forEach((element) => { element.textContent = "—"; });
    elements.selectedQualityText.textContent = state.baseline.ready ? "Waiting" : "Baseline required";
    elements.qualityMeterFill.style.width = "0%";
    return;
  }
  elements.selectedHbO.textContent = formatNumeric(sample.displayHb.HbO, 3);
  elements.selectedHbR.textContent = formatNumeric(sample.displayHb.HbR, 3);
  elements.selectedHbT.textContent = formatNumeric(sample.displayHb.HbT, 3);
  elements.selectedI735.textContent = formatCount(sample.I735);
  elements.selectedI850.textContent = formatCount(sample.I850);
  elements.selectedOD735.textContent = formatNumeric(sample.od735, 5);
  elements.selectedOD850.textContent = formatNumeric(sample.od850, 5);
  elements.selectedBeta735.textContent = betaText(sample.beta735);
  elements.selectedBeta850.textContent = betaText(sample.beta850);
  const quality = sample.valid ? (sample.regressorReady || state.config.scrMode === "none" ? 94 : 72) : 15;
  elements.qualityMeterFill.style.width = `${quality}%`;
  elements.selectedQualityText.textContent = quality > 85 ? "Good" : quality > 50 ? "Regression warming" : "Invalid";
  elements.selectedQualityDot.className = `quality-dot ${quality > 85 ? "good" : quality > 50 ? "warn" : "error"}`;
}

function updateViewLabels() {
  const legendItems = $$(".trace-legend span");
  if (state.viewMode === "hb") {
    legendItems[0].lastChild.textContent = "HbO"; legendItems[1].lastChild.textContent = "HbR";
    elements.scaleSelect.disabled = false;
  } else if (state.viewMode === "od") {
    legendItems[0].lastChild.textContent = "735 mOD"; legendItems[1].lastChild.textContent = "850 mOD";
    elements.scaleSelect.disabled = true;
  } else {
    legendItems[0].lastChild.textContent = "I735 %"; legendItems[1].lastChild.textContent = "I850 %";
    elements.scaleSelect.disabled = true;
  }
  clearAllSweeps(`${state.viewMode.toUpperCase()} view`);
  state.plots.forEach((plot) => plot.updateScaleLabels());
}

/* Setup dialog */
const SETUP_STEPS = ["device", "mbll", "scr", "session"];
let setupStepIndex = 0;

function showSetupStep(index) {
  setupStepIndex = Math.max(0, Math.min(SETUP_STEPS.length - 1, index));
  const stepName = SETUP_STEPS[setupStepIndex];
  $$(".step").forEach((button, buttonIndex) => {
    button.classList.toggle("active", buttonIndex === setupStepIndex);
    button.classList.toggle("complete", buttonIndex < setupStepIndex);
  });
  $$(".setup-pane").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === stepName));
  elements.setupBackButton.disabled = setupStepIndex === 0;
  elements.setupNextButton.classList.toggle("hidden", setupStepIndex === SETUP_STEPS.length - 1);
  elements.launchLiveButton.classList.toggle("hidden", setupStepIndex !== SETUP_STEPS.length - 1);
}

function populateSetupFromConfig() {
  const c = state.config;
  elements.deviceIpInput.value = c.deviceIp;
  elements.modeSelect.value = c.mode;
  elements.current735Input.value = c.current735;
  elements.current850Input.value = c.current850;
  elements.baselineDurationInput.value = c.baselineDuration;
  elements.baselineEstimatorSelect.value = c.baselineEstimator;
  elements.dpf735Input.value = c.dpf735;
  elements.dpf850Input.value = c.dpf850;
  elements.ageYearsInput.value = c.ageYears;
  elements.sessionNameInput.value = c.sessionName;
  elements.subjectIdInput.value = c.subjectId;
  elements.protocolSelect.value = c.protocol;
  elements.operatorInput.value = c.operator;
  elements.sessionNotesInput.value = c.notes;
  const dpfRadio = document.querySelector(`input[name="dpfMode"][value="${c.dpfMode}"]`);
  if (dpfRadio) dpfRadio.checked = true;
  const scrRadio = document.querySelector(`input[name="scrMode"][value="${c.scrMode}"]`);
  if (scrRadio) scrRadio.checked = true;
  updateChoiceCards();
  updateAgeDpfPreview();
}

function readConfigFromSetup() {
  state.config = {
    ...state.config,
    deviceIp: elements.deviceIpInput.value.trim() || DEFAULT_CONFIG.deviceIp,
    mode: elements.modeSelect.value,
    current735: Math.max(0, Math.min(50, Number(elements.current735Input.value))),
    current850: Math.max(0, Math.min(50, Number(elements.current850Input.value))),
    baselineDuration: Math.max(3, Math.min(60, Number(elements.baselineDurationInput.value))),
    baselineEstimator: elements.baselineEstimatorSelect.value,
    dpfMode: document.querySelector('input[name="dpfMode"]:checked')?.value ?? "manual",
    dpf735: Math.max(.1, Number(elements.dpf735Input.value)),
    dpf850: Math.max(.1, Number(elements.dpf850Input.value)),
    ageYears: Math.max(0, Number(elements.ageYearsInput.value)),
    scrMode: document.querySelector('input[name="scrMode"]:checked')?.value ?? "none",
    sessionName: elements.sessionNameInput.value.trim() || DEFAULT_CONFIG.sessionName,
    subjectId: elements.subjectIdInput.value.trim(),
    protocol: elements.protocolSelect.value,
    operator: elements.operatorInput.value.trim(),
    notes: elements.sessionNotesInput.value.trim(),
  };
  resolveCurrentDpf();
  saveConfig();
}

function updateChoiceCards() {
  $$(".choice-card").forEach((card) => card.classList.toggle("selected", card.querySelector("input")?.checked));
  $$(".scr-option").forEach((card) => card.classList.toggle("selected", card.querySelector("input")?.checked));
  const mode = document.querySelector('input[name="dpfMode"]:checked')?.value ?? "manual";
  elements.manualDpfFields.classList.toggle("hidden", mode !== "manual");
  elements.ageDpfFields.classList.toggle("hidden", mode !== "age-based");
}

function updateAgeDpfPreview() {
  const age = Math.max(0, Number(elements.ageYearsInput.value || 0));
  elements.computedDpfText.textContent = `735: ${scholkmannWolfDpf(735, age).toFixed(2)} · 850: ${scholkmannWolfDpf(850, age).toFixed(2)}`;
}

function openSetup(step = 0) {
  populateSetupFromConfig();
  showSetupStep(step);
  elements.setupDialog.showModal();
}

function openLiveScreen({ demo = false } = {}) {
  readConfigFromSetup();
  state.demo = demo;
  state.running = false;
  state.displayPaused = false;
  state.frameCount = 0;
  state.sampleIndex = 0;
  state.sessionStartedAt = null;
  elements.sessionNameFooter.textContent = state.config.sessionName;
  elements.welcomeScreen.classList.add("hidden");
  elements.liveScreen.classList.remove("hidden");
  elements.setupDialog.close();
  elements.liveScrModeSelect.value = state.config.scrMode;
  elements.liveDpf735Input.value = state.dpf.dpf735.toFixed(2);
  elements.liveDpf850Input.value = state.dpf.dpf850.toFixed(2);
  elements.liveBaselineDurationInput.value = state.config.baselineDuration;
  clearAllSweeps("Display ready");
  resetRegressors();
  updateSidebarConnection();
  if (demo) startDemo();
  else {
    setLiveState("idle", "IDLE");
    elements.startPauseText.textContent = "Start acquisition";
    elements.startPauseIcon.textContent = "▶";
    elements.baselineStatusValue.textContent = "Not set";
  }
}

/* WebSocket bridge */
function webSocketUrl() {
  const rawHost = window.location.hostname || "localhost";
  const host = rawHost.includes(":") ? `[${rawHost}]` : rawHost;
  const requestedPort = new URLSearchParams(window.location.search).get("wsPort");
  const port = /^\d{1,5}$/.test(requestedPort ?? "") ? requestedPort : "8080";
  return `ws://${host}:${port}`;
}

function connectWebSocket() {
  clearTimeout(state.reconnectTimer);
  elements.welcomeWsText.textContent = "Bridge connecting";
  setDot(elements.welcomeWsDot, "pending");
  const socket = new WebSocket(webSocketUrl());
  state.socket = socket;
  socket.addEventListener("open", () => {
    state.bridgeConnected = true;
    state.reconnectAttempt = 0;
    elements.welcomeWsText.textContent = "Bridge connected";
    setDot(elements.welcomeWsDot, "online");
    elements.diagWsValue.textContent = "Connected";
    logLine(`WebSocket connected to ${webSocketUrl()}`);
    send({ type: "get_state" });
  });
  socket.addEventListener("message", (event) => {
    try { handleMessage(JSON.parse(event.data)); }
    catch (error) { logLine(`JSON decode error: ${error.message}`); }
  });
  socket.addEventListener("close", () => {
    state.bridgeConnected = false;
    state.deviceOnline = false;
    elements.welcomeWsText.textContent = "Bridge disconnected";
    setDot(elements.welcomeWsDot, "offline");
    elements.diagWsValue.textContent = "Disconnected";
    updateConnectionUi();
    const delay = Math.min(5000, 600 * (2 ** state.reconnectAttempt));
    state.reconnectAttempt += 1;
    state.reconnectTimer = setTimeout(connectWebSocket, delay);
  });
  socket.addEventListener("error", () => socket.close());
}

function send(message) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    logLine("WebSocket is not connected");
    return false;
  }
  state.socket.send(JSON.stringify(message));
  return true;
}

function sendCommand(command) {
  if (send({ type: "command", command })) logLine(command, "tx");
}

function sendCommandBatch(commands) {
  if (send({ type: "command_batch", commands })) commands.forEach((command) => logLine(command, "tx"));
}

function connectDevice() {
  if (elements.deviceIpInput.value.trim()) {
    state.config.deviceIp = elements.deviceIpInput.value.trim();
    send({ type: "set_device_ip", deviceIp: state.config.deviceIp });
  }
  sendCommandBatch(["HELLO", "INFO", "STATUS"]);
}

function handleMessage(message) {
  switch (message.type) {
    case "bridge_state": updateBridgeState(message); break;
    case "control_rx": logLine(message.message, "rx"); break;
    case "fnirs_frame": handleFnirsFrame(message); break;
    case "session": state.sessionId = String(message.data.sessionId ?? "—"); break;
    case "event": logLine(`Event code=0x${Number(message.data.code).toString(16).toUpperCase()} arg=${message.data.argument}`); break;
    case "decode_warning": logLine(`Decode warning: ${message.layer} ${message.error}`); break;
    case "bridge_notice": logLine(message.message); break;
    case "bridge_error": logLine(`Bridge error: ${message.message}`); break;
    default: break;
  }
}

function updateBridgeState(message) {
  state.deviceOnline = Boolean(message.device.online);
  state.deviceRunning = Number(message.device.status?.running ?? 0) === 1;
  state.frameRateHz = Number(message.stream.frameRateHz ?? state.frameRateHz ?? 10) || 10;
  state.packetLoss = Number(message.stream.packetLoss ?? 0);
  state.sessionId = String(message.device.status?.session ?? message.latestSession?.sessionId ?? state.sessionId);
  state.config.deviceIp = message.bridge.deviceIp ?? state.config.deviceIp;
  elements.deviceIpInput.value = state.config.deviceIp;
  elements.diagSessionValue.textContent = state.sessionId;
  elements.diagCrcValue.textContent = `${message.stream.udpCrcErrors ?? 0} / ${message.stream.streamCrcErrors ?? 0}`;
  updateConnectionUi();
}

function updateConnectionUi() {
  const status = state.deviceOnline ? "online" : "offline";
  setDot(elements.welcomeDeviceDot, status);
  setDot(elements.sidebarDeviceDot, status);
  elements.welcomeDeviceText.textContent = state.deviceOnline ? "Device online" : "Device offline";
  elements.welcomeDeviceStatus.textContent = state.deviceOnline ? "NoctiNIRS connected" : "Waiting for connection";
  elements.welcomeDeviceMeta.textContent = state.deviceOnline ? `${state.config.deviceIp} · Ready` : "ESP32-S3 · ADS131M04";
  elements.sidebarDeviceText.textContent = state.deviceOnline ? "Device online" : "Device offline";
  elements.setupDeviceBadge.textContent = state.deviceOnline ? "Online" : "Offline";
  elements.setupDeviceBadge.className = `status-badge ${state.deviceOnline ? "online" : "offline"}`;
  elements.diagDeviceValue.textContent = state.deviceOnline ? "Online" : "Offline";
  updateSidebarConnection();
}

function updateSidebarConnection() {
  if (state.demo) {
    setDot(elements.sidebarDeviceDot, "online");
    elements.sidebarDeviceText.textContent = "Demo source";
  } else {
    setDot(elements.sidebarDeviceDot, state.deviceOnline ? "online" : "offline");
    elements.sidebarDeviceText.textContent = state.deviceOnline ? "Device online" : "Device offline";
  }
}

function handleFnirsFrame(message) {
  state.lastFrameAt = Date.now();
  state.frameCount += 1;
  state.frameRateHz = Number(message.stream?.frameRateHz ?? state.frameRateHz) || state.frameRateHz;
  state.sessionId = String(message.udp?.sessionId ?? state.sessionId);
  state.lastFrameQuality = `${message.data.validSlotCount ?? "—"}/20 valid`;
  elements.diagFrameQualityValue.textContent = state.lastFrameQuality;
  elements.diagSessionValue.textContent = state.sessionId;
  if (!state.running || state.demo) return;
  processLiveFrame(message);
}

function beginDeviceAcquisition() {
  state.demo = false;
  state.running = true;
  state.sessionStartedAt = Date.now();
  state.frameCount = 0;
  resolveCurrentDpf();
  resetBaseline();
  elements.startPauseText.textContent = "Pause acquisition";
  elements.startPauseIcon.textContent = "Ⅱ";
  sendCommandBatch([
    `MODE ${state.config.mode}`,
    `CURRENT 735 ${state.config.current735}`,
    `CURRENT 850 ${state.config.current850}`,
    "START",
    "STATUS",
  ]);
}

function pauseDeviceAcquisition() {
  state.running = false;
  sendCommandBatch(["STOP", "STATUS"]);
  setLiveState("idle", "PAUSED");
  elements.startPauseText.textContent = "Resume acquisition";
  elements.startPauseIcon.textContent = "▶";
}

function startDemo() {
  clearInterval(state.demoTimer);
  state.demo = true;
  state.running = true;
  state.frameRateHz = 10;
  state.frameCount = 0;
  state.demoStart = performance.now();
  state.sessionStartedAt = Date.now();
  state.baseline.ready = true;
  elements.baselineStatusValue.textContent = "Demo reference";
  setLiveState("demo", "DEMO LIVE");
  elements.startPauseText.textContent = "Pause demo";
  elements.startPauseIcon.textContent = "Ⅱ";
  elements.displayNotice.textContent = "ECG sweep demo · 10 Hz";
  updateSidebarConnection();
  state.demoTimer = setInterval(() => {
    if (!state.running) return;
    const time = (performance.now() - state.demoStart) / 1000;
    for (const channel of LONG_CHANNELS) deliverSample(channel, demoSample(channel, time));
    state.sampleIndex += 1;
    state.frameCount += 1;
    state.lastFrameAt = Date.now();
  }, 100);
}

function toggleAcquisition() {
  if (state.demo) {
    state.running = !state.running;
    if (state.running) {
      setLiveState("demo", "DEMO LIVE");
      elements.startPauseText.textContent = "Pause demo";
      elements.startPauseIcon.textContent = "Ⅱ";
    } else {
      setLiveState("idle", "DEMO PAUSED");
      elements.startPauseText.textContent = "Resume demo";
      elements.startPauseIcon.textContent = "▶";
    }
    return;
  }
  if (state.running) pauseDeviceAcquisition();
  else beginDeviceAcquisition();
}

function returnHome() {
  if (state.running && !state.demo) sendCommandBatch(["STOP", "STATUS"]);
  clearInterval(state.demoTimer);
  state.demoTimer = null;
  state.running = false;
  state.demo = false;
  elements.liveScreen.classList.add("hidden");
  elements.welcomeScreen.classList.remove("hidden");
}

function applyLiveSettings() {
  state.config.scrMode = elements.liveScrModeSelect.value;
  state.config.dpfMode = "manual";
  state.config.dpf735 = Math.max(.1, Number(elements.liveDpf735Input.value));
  state.config.dpf850 = Math.max(.1, Number(elements.liveDpf850Input.value));
  state.config.baselineDuration = Math.max(3, Math.min(60, Number(elements.liveBaselineDurationInput.value)));
  resolveCurrentDpf();
  saveConfig();
  elements.settingsDialog.close();
  if (state.demo) {
    resetRegressors();
    clearAllSweeps("Processing settings updated");
  } else resetBaseline("Processing settings updated");
  updateSelectedDetails();
}

function addMarker(label) {
  const marker = { label, time: state.sessionStartedAt ? (Date.now() - state.sessionStartedAt) / 1000 : 0 };
  state.markers.push(marker);
  elements.displayNotice.textContent = `Marker: ${label} · ${formatClock(marker.time)}`;
  logLine(`Marker added: ${label} at ${marker.time.toFixed(2)} s`);
  elements.markerMenu.classList.add("hidden");
}

function updateRuntimeUi() {
  if (state.sessionStartedAt) elements.elapsedValue.textContent = formatClock((Date.now() - state.sessionStartedAt) / 1000);
  elements.frameRateValue.textContent = `${Number(state.frameRateHz || 0).toFixed(1)} Hz`;
  elements.frameCountValue.textContent = state.frameCount.toLocaleString();
  elements.packetLossValue.textContent = state.packetLoss.toLocaleString();
  if (state.lastFrameAt) elements.diagLastFrameValue.textContent = `${((Date.now() - state.lastFrameAt) / 1000).toFixed(1)} s ago`;
}

/* Events */
elements.newSessionButton.addEventListener("click", () => openSetup(0));
elements.useLastConfigButton.addEventListener("click", () => openSetup(3));
elements.quickMbllButton.addEventListener("click", () => openSetup(1));
elements.demoButton.addEventListener("click", () => {
  populateSetupFromConfig();
  openLiveScreen({ demo: true });
});
elements.welcomeConnectButton.addEventListener("click", connectDevice);
elements.setupConnectButton.addEventListener("click", connectDevice);
elements.setupBackButton.addEventListener("click", () => showSetupStep(setupStepIndex - 1));
elements.setupNextButton.addEventListener("click", () => showSetupStep(setupStepIndex + 1));
elements.launchLiveButton.addEventListener("click", () => openLiveScreen({ demo: false }));
$$(".step").forEach((button, index) => button.addEventListener("click", () => showSetupStep(index)));
$$('input[name="dpfMode"], input[name="scrMode"]').forEach((input) => input.addEventListener("change", updateChoiceCards));
elements.ageYearsInput.addEventListener("input", updateAgeDpfPreview);

elements.homeButton.addEventListener("click", returnHome);
elements.stopSessionButton.addEventListener("click", () => {
  if (confirm("Stop the current session and return to the welcome screen?")) returnHome();
});
elements.startPauseButton.addEventListener("click", toggleAcquisition);
elements.pauseDisplayButton.addEventListener("click", () => {
  state.displayPaused = !state.displayPaused;
  elements.pauseDisplayButton.textContent = state.displayPaused ? "Resume display" : "Pause display";
  elements.displayNotice.textContent = state.displayPaused ? "Display paused · recording continues" : "Display resumed";
});
elements.clearSweepsButton.addEventListener("click", () => clearAllSweeps());
elements.resetBaselineButton.addEventListener("click", () => {
  if (state.demo) clearAllSweeps("Demo sweeps reset");
  else resetBaseline();
});
elements.windowSelect.addEventListener("change", () => clearAllSweeps("Sweep window changed"));
elements.scaleSelect.addEventListener("change", () => clearAllSweeps("Vertical scale changed"));
$$('.segment[data-view]').forEach((button) => button.addEventListener("click", () => {
  $$('.segment[data-view]').forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.viewMode = button.dataset.view;
  updateViewLabels();
}));

elements.settingsButton.addEventListener("click", () => {
  elements.liveScrModeSelect.value = state.config.scrMode;
  elements.liveDpf735Input.value = state.dpf.dpf735.toFixed(2);
  elements.liveDpf850Input.value = state.dpf.dpf850.toFixed(2);
  elements.liveBaselineDurationInput.value = state.config.baselineDuration;
  elements.settingsDialog.showModal();
});
elements.applyLiveSettingsButton.addEventListener("click", applyLiveSettings);
elements.diagnosticsButton.addEventListener("click", () => elements.diagnosticsDialog.showModal());
elements.clearLogButton.addEventListener("click", () => { elements.deviceLog.textContent = ""; });
elements.requestStatusButton.addEventListener("click", () => sendCommand("STATUS"));

elements.markerButton.addEventListener("click", () => {
  const rect = elements.markerButton.getBoundingClientRect();
  elements.markerMenu.style.left = `${rect.left}px`;
  elements.markerMenu.style.top = `${rect.bottom + 6}px`;
  elements.markerMenu.classList.toggle("hidden");
});
$$('[data-marker]').forEach((button) => button.addEventListener("click", () => {
  let label = button.dataset.marker;
  if (label === "Custom") label = prompt("Marker label:", "Custom marker")?.trim();
  if (label) addMarker(label);
}));
document.addEventListener("click", (event) => {
  if (!elements.markerMenu.contains(event.target) && event.target !== elements.markerButton) elements.markerMenu.classList.add("hidden");
});
window.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "m" && !elements.liveScreen.classList.contains("hidden")) addMarker("Keyboard marker");
});

/* Init */
buildProbePreview();
createChannelCards();
populateSetupFromConfig();
resolveCurrentDpf();
saveConfig();
updateConnectionUi();
updateViewLabels();
connectWebSocket();
setInterval(updateRuntimeUi, 250);
