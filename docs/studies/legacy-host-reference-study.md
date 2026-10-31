# Study: historical live-view snapshots under `Reference/` (stage 1: study and recommendation only)

- Status: DRAFT for independent review. Not merged. No host source, dependency, or snapshot was changed.
- Author: Claude (Sonnet 5.5). Independent review must come from a different model family (AGENTS.md; hub section 5.1).
- Issue: TBD (could not be created from the authoring session; see "Process notes").
- Snapshots studied: PR #8, head `da7a9b1bad428e57e2d9cebf00fa2711b5511892` (`Reference/` does not exist on `main` `5eb05ff`).
- Contract basis: optical data contract v0.4.3, firmware pin `e42f2bc9b453b223bf4557516f63fcceadfddbc8` (`docs/drafts/OPTICAL_DATA_CONTRACT_DRAFT.md`, sha256 `4dd2b89b…` equals the host pin). The legacy RAW layout is not used as a basis for any recommendation.
- Evidence classes used: **[code]** code inspection, **[unit]** an existing or newly run unit test, **[sim]** synthetic numeric simulation with an independent comparator. None of them is hardware evidence.

## 0. Ringkasan untuk pemilik (Bahasa Indonesia)

1. **Ya, salah satu versi memang menghitung MBLL dan menampilkan HbO/HbR dari data live, dan itu terbukti dari kode, bukan asumsi.** Rantai live di `public/app.js` benar-benar tersambung: frame → intensitas → baseline (median) → OD (`ln`) → MBLL 2×2 → ΔHbO/ΔHbR (µM) → grafik. Mode demo memakai angka sintetis dan *tidak* melewati rantai ini, jadi grafik demo bukan bukti MBLL.
2. **Ketiga snapshot identik** untuk seluruh 22 file kode (sha256 sama). Yang beda hanya `package-lock.json` pembungkus. Jadi tidak ada versi yang "lebih lengkap"; kemampuan MBLL-nya sama.
3. **Matematika MBLL-nya benar** untuk konvensi yang dipakainya: dibandingkan dengan implementasi independen (numpy) pada 1000 kasus acak, selisih maksimum 6,7e-16 µM. Itu hanya membuktikan aljabar dan satuan, **bukan** kebenaran ilmiah.
4. **Yang tidak terbukti dan tidak boleh diambil dari aplikasi lama (belum terverifikasi):** panjang gelombang sebenarnya (735/850 nominal), tabel koefisien ekstingsi (Moaveni 1970 via OMLC, sumber sekunder), DPF (rumus Scholkmann & Wolf 2013 dipakai di 850 nm, di luar rentang fit 690–832 nm), jarak S–D (masih DRAFT sampai diukur kaliper).
5. **Bahaya terbesar versi lama: tidak ada quality gate.** Slot yang LED-nya tidak pernah menyala tetap dianggap "valid" dan menghasilkan ΔHb sebesar nilai fisiologis (simulasi: SD 0,18–0,34 µM untuk ΔHbO, tanpa bendera apa pun). Host baru harus memakai gate kontrak v0.4.3.
6. **Temuan penting di host baru (saat ini):** gate terdiri dari *dua* bagian yang belum digabung (`science_mask` dan `recheck_frame`). Frame "lolos palsu" dari firmware masih lolos `science_mask` bila konsumen hanya memakai itu. Perlu satu fungsi gate gabungan sebelum MBLL ditulis (issue S1 di bawah).
7. **Label detektor lama (PD0–PD3 = indeks AIN) berbeda dengan nama fotodioda firmware** (AIN0=PD3, AIN1=PD4, AIN2=PD1, AIN3=PD2). Semua 20 label berbeda. Jangan memakai label lama.
8. **Rekomendasi:** ambil *ide dan pola uji* (matematika MBLL sebagai inti Python tanpa UI, parameter ε/DPF/jarak diinjeksi dengan catatan sumber, baseline median dengan aturan eksplisit, regresi short-separation yang di-adaptasi, uji "maju-mundur" dengan pembanding independen). Jangan ambil parser protokol, bridge, perintah, konstanta keras, indikator "quality", dan label geometri.
9. **Langkah berikutnya untuk Anda:** reviu laporan ini oleh model non-Claude, lalu putuskan issue implementasi bertahap (S1–S7). Tidak ada perangkat yang dihubungkan dan tidak ada akuisisi yang dijalankan.

## 1. Scope, constraints, method

Read: `AGENTS.md`, `project.yaml`, `Reference/README.md`, active host `src/noctinirs_host/{wire/optical.py,gate.py,geometry.py}`, `config/optical_geometry_v1.json`, `config/reference_pins.json`, the pinned contract and golden vectors. Snapshot comparison was by content hash. Snapshots were not modified (worktree stayed clean). No sockets were opened, `bridge.js`/`simulator.js` were not run, `app.js` was not executed (needs a DOM), no device was contacted.

All verification scripts and outputs live outside the repository (session scratchpad). Test dependencies were installed only in a temporary venv outside the repo: Python 3.13.18, numpy 2.5.4, pytest 9.2.0, iniconfig 2.3.1, packaging 26.3, pluggy 1.7.0, Pygments 2.21.0. `pyproject.toml`, requirements and lockfiles are unchanged.

## 2. Snapshot comparison

Paths below are relative to `Reference/`.

| Snapshot | Code files (22) | `package-lock.json` | Notes |
|---|---|---|---|
| `legacy/liveview/offline/` | baseline | full lock, `ws 8.21.0` only dependency | the working copy |
| `legacy/liveview/white-blue/offline/` | byte-identical (sha256) | differs only at wrapper level | |
| `legacy/liveview/white-blue/white-blue/offline/` | byte-identical (sha256) | wrapper lock with empty `"packages": {}` named `…white-blue-ui` | |

Conclusion: three copies of one codebase. No snapshot is more complete; all contain the MBLL/short-separation library and the same UI wiring. Comparison table of capabilities therefore collapses to a single column (section 3).

## 3. Data-flow map (legacy; wired vs not wired)

| Stage | Location | Status |
|---|---|---|
| UDP receive (3334), HTTP 8000, WS 8080, control UDP 3333 | `bridge.js` | wired; text commands HELLO/INFO/STATUS/MODE/CURRENT/START/STOP are legacy, not the current contract |
| UDP header 28 B (magic `0x4e4f4354`), CRC16-CCITT-FALSE, stream header 12 B, `REC_FNIRS_FRAME=7` payload 356 B (16 B header + 20 slots × 4 PD × int32 `averages` + 20 `nUsed`) | `protocol.js` | wired; legacy layout, not v0.4.3 |
| Browser frame handling | `public/app.js`: `handleFnirsFrame → processLiveFrame → createSampleFromLive` | wired |
| Channel/wavelength mapping | `public/lib/fnirs-geometry.js`: `slotIndexFor(source, wl) = source*2 + (850?1:0)`, `extractNamedIntensities`, `SHORT_CHANNELS`, CH1–CH16 | wired |
| Baseline | `app.js: collectBaseline` / `resetBaseline`; `fnirs-mbll.js: estimateReference` (median) | wired |
| Optical density | `fnirs-mbll.js: opticalDensity(I, ref) = Math.log(ref/I)` | wired |
| Short-separation regression | `fnirs-short-separation.js: RollingShortSeparationRegressor` | wired, optional |
| MBLL | `fnirs-mbll.js: mbllFromOpticalDensity` | wired |
| Display | `app.js: SweepPlot`, value cards, `displayHb` | wired |
| Dark-current correction | `darkCorrectIntensity` in lib | library only; **unused** by `app.js` |
| CSV recording | README mentions it | **not present** in current `app.js` (docs stale) |
| Demo mode | `app.js: demoSample` | prototype; bypasses the chain with other constants |
| `PROCESSING_LIBRARY.md` | claims the library is not yet connected to the UI | **stale**; contradicted by `app.js` and `UI_PROTOTYPE.md` |

## 4. MBLL / HbO / HbR findings

Evidence tags in brackets. Everything marked "belum terverifikasi" must not be treated as device fact.

| # | Finding | Evidence |
|---|---|---|
| F1 | OD = natural log, `ln(I0/I)`. Contract §10.7 uses `−ln(I_clean/I0)`; the same sign convention. A log10 OD fed into the ln-based code underestimates by exactly 1/ln10 (0.4343). | [code][sim] |
| F2 | ε is decadic (M⁻¹cm⁻¹) and multiplied by ln10 to Napierian; S–D mm→cm; 2×2 solved by Cramer's rule; result ×1e6 → µM. Output is **change relative to baseline** (ΔHbO, ΔHbR, ΔHbT), not absolute concentration. | [code] |
| F3 | Independent recompute (decadic ε + log10 OD, converted afterwards) vs legacy: 1000 random cases, max abs diff 6.7e-16 µM; 36 known-Δ cases recovered within 3.6e-15 µM; extinction matrix condition number 3.00. Proves algebra and unit chain only. | [sim] |
| F4 | ε values 735 nm: HbO 420 / HbR 1510 (interpolated 730/740 nm); 850 nm: HbO 1060 / HbR 800. They agree with the OMLC Moaveni table (730: 400/1500; 740: 440/1520; 850: 1060/800). Takatani & Graham at 735 nm: 412/1464 (≈2–3 % different; effect on result −0.7 % HbO, −2.2 % HbR). OMLC is a secondary compilation; primary-source check pending. Bandwidth of the real LEDs would require spectrally weighted ε. | [sim]; primary source **belum terverifikasi** |
| F5 | DPF default 6.0/6.0 (manual); optional Scholkmann & Wolf 2013 (J Biomed Opt 18(10):105004, DOI 10.1117/1.JBO.18.10.105004), coefficients match the published formula. Fitted on frontal data, d = 4.3 cm, 690–832 nm, ages 0–70, homogeneous model. Legacy applies it at 850 nm with no range warning. Age 28 gives 6.255 (735 nm) and 5.177 (850 nm). Applicability to the device's S–D 18.5–30 mm and to non-frontal sites is unverified. `applyLiveSettings` forces `dpfMode="manual"`. | [code][sim]; **belum terverifikasi** |
| F6 | Sensitivity (illustrative ΔHbO=5, ΔHbR=−2 µM): S&W(28 y) vs 6/6 → HbO −11.6 %, HbR −6.8 %; +20 % DPF both → −16.7 %; ±1 mm at 30 mm → ∓3.2–3.4 %, at 18.5 mm → ∓5.1–5.7 %; ε_HbR(735) ±10 % → HbR ±12.7 %, HbO ±3.8 %. Result scales as 1/distance and 1/DPF. | [sim] |
| F7 | Edge cases of the library: `opticalDensity` throws for 0, negative, NaN, Infinity, null; `estimateReference([])` throws; zeros enter the median; intensity above reference gives negative OD (allowed). **`mbllFromOpticalDensity` accepts `null`/`""` OD via `Number()` and silently treats them as 0** (returns numbers). `darkCorrect` below dark → null. | [sim][code] |
| F8 | Baseline: median over positive finite samples per channel/wavelength for `baselineDuration × frameRate` **frames** (not valid samples). Every start/resume/settings change (including Pause→Resume) resets the baseline. `frameRateHz` comes from the bridge's 3-s measured rate (default 10). | [code] |
| F9 | Short-separation: regression in the OD domain per wavelength, per hemisphere (not nearest channel), window max(60, fs×30), min 30 samples, OLS for one regressor, "ridge" for two. The ridge λ=1e-3 on standardized sum of squares with n=300 is ≈3.3e-6 effective shrinkage: coefficients differ 1e-3 relative from OLS (r=0.9988), i.e. effectively OLS. OLS/ridge match numpy. Assumption: the short channel contains only superficial signal. If it contains a fraction κ of the cerebral signal (superficial gain 2.5), residual error relative to cerebral RMS: κ=0.05 → 0.127, 0.10 → 0.252, 0.20 → 0.502, 0.40 → 1.0. | [sim] |
| F10 | UI shows RAW Hb until the regressor is ready (30 samples), then switches silently to CORRECTED (chip "WARM"); switch offset −0.0009 µM, max 0.0244 µM, RMS 0.0141 µM against raw excursion RMS 0.0056 µM (synthetic). | [sim] |
| F11 | `requireFinite` and `extractNamedIntensities` validity: only `valid !== false && crcDropped !== true && finite`. `underK`, saturation (INT32_MAX), ON/OFF, quality are ignored. | [code][sim] |
| F12 | In-process decode chain simulation (legacy `buildStreamFrame`/`buildUdpPacket`/`parse*`; `app.js` sequence mirrored, `app.js` itself not run): imposed ΔHbO=+3, ΔHbR=−1 µM recovered within 6.7e-5 µM. | [sim] |
| F13 | Legacy tests (`tests/fnirs-processing.test.js`) pass in all three snapshots (Node v22.23.4) but only check forward/inverse algebra with the same ε plus mapping and regression recovery. `simulator.js` adds identical physiology to all wavelengths with `validMask` all ones, so it cannot validate MBLL. | [unit][code] |
| F14 | Display issues: x-axis is a frame counter modulo window, not time; gaps shorter than ~1/3 of the window (≈99 frames) are bridged by a straight line; disconnects are invisible; `mapY` clamps silently to ±scale; the "quality" meter is hard-coded (94/72/15), not measured; ln-OD is labelled "mOD" (non-standard against decadic OD); the sweep legend says "HbO/HbR" while cards say ΔHbO/ΔHbR µM; the welcome label "MBLL ready · 735 / 850 nm · Moaveni 1970" implies validation. | [code] |

### 4.1 What cannot be proven from the snapshots

Actual LED wavelengths (contract D7 open); ε table choice and primary-source numbers; DPF values; S–D distances (firmware geometry is DRAFT until caliper measurement; the legacy distances equal the firmware DRAFT values and so are not independent confirmation); the legacy meaning of `averages` (ON versus ON−OFF); whether legacy firmware cleared `valid` when `underK` was set; physical correctness on a phantom or a subject. Wavelength, distance, DPF and ε must **not** be decided from the legacy app.

## 5. Geometry check

Against the pinned `config/optical_geometry_v1.json` (sha256 `035c00f1…`): 20 channels compared, 0 mismatches in (source, AIN, distance); slot mapping `source*2 + wavelength` 0 mismatches over 10 sources. **But** legacy detector labels `PD0–PD3` equal AIN indices, while the firmware names are AIN0=PD3, AIN1=PD4, AIN2=PD1, AIN3=PD2; all 20 labels differ (for example legacy `SC1L` = PD2 = AIN2 = firmware PD1). AIN→PD translation must be done only in `geometry.Geometry.pd_for_ain` (contract: one module, once).

## 6. Quality gate: where it sits in the active host, and where legacy skipped it

Active host (at `da7a9b1`), gate parameters = firmware `gate_config_id=1` (marked `provisional_unmeasured`; thresholds are to come from the dark-box bench, D5). Two separate pieces exist:

1. `wire/optical.py`: `Opt2Frame.is_science`, `science_mask()` / `science_sample()` (single choke point for values). Enforces: not ENGINEERING/STARTING_GATE/WARMUP, quality ∈ {1 (VALID_MINIMUM), 2 (VALID_TARGET)}, reason 0, host-invalid, verified mask, values fully present.
2. `gate.py`: `recheck_frame(frame, params)`; recomputes step_z/floor/stable/diff_negative, witness, saturation, `gate_config_id`. The recheck is one-directional (catches false passes, not false rejections; contract §10.8).

Probe results (synthetic frames from `tests/helpers.py`, `verify/gate_demo.py`):

| Scenario | `science_mask(slot 0)` | `recheck_frame` | Naive consumer | Full gate |
|---|---|---|---|---|
| A clean, quality 2 | 0xF | none | uses | uses |
| B quality 1 | 0xF | none | uses | uses |
| C ENGINEERING frame | 0x0 | n/a | refuses | refuses |
| D STARTING_GATE | 0x0 | n/a | refuses | refuses |
| E WARMUP | 0x0 | n/a | refuses | refuses |
| F quality 0, LED_ON_MISSING | 0x0 | n/a | refuses | refuses |
| G quality 2 but ON−OFF = 1 count | 0xF | `false_pass`, `floor_flag_differs`, `step_z_flag_differs`, `witness_fails_host_check` | **uses** | refuses |
| H quality 2 but stab_q=100 | 0xF | `false_pass` and flag mismatches | **uses** | refuses |
| I `gate_config_id` differs | 0xF | `gate_config_id_mismatch` (frame level, slot −1) | **uses** | refuses |

Conclusion: a future MBLL entry point must require **both** the `science_mask` bit **and** a clean `recheck_frame`, plus negative-control exclusion (LEAK_SUSPECT, §10.3), both wavelength slots verified for the same source/detector in the same frame, and verified short channels when regression is used. Today no single function composes these (S1 below). Note also `ScienceSample.quality_vs_k = "not_checked"` and the `gate.py` docstring still cites "v0.4.1 section 6.1" while pins say v0.4.3 (documentation drift).

Legacy skipped the gate entirely at `extractNamedIntensities` (`fnirs-geometry.js`) and in `collectBaseline`/`processLiveFrame` (`app.js`). Simulation (assumed ambient ≈1800 counts, σ=12, LED never on; all 20 slots decoded "valid"): CH1 (30 mm) ΔHbO SD 0.183 µM (max 0.71), ΔHbR SD 0.120; CH6 (18.5 mm) ΔHbO SD 0.340 (max 1.18), ΔHbR SD 0.231. A +1 % ambient drift yields ≈ −0.10 to −0.23 µM. A baseline taken with LEDs off and then LEDs on gives CH1 ΔHbO −56.6 µM, ΔHbR −51.0 µM with no flag. [sim]

## 7. Decision table

| Item | Decision | Reason and limits |
|---|---|---|
| MBLL 2×2 inversion and unit chain | **Adapt** (re-implement in Python core, UI-independent) | Algebra verified [sim]; parameters must be injected with provenance; reject non-finite/None input (F7) |
| OD formula `ln` with sign `−ln(I/I0)` | **Adopt** (convention) | Matches contract §10.7; I_clean = mean_ON − mean_OFF, not raw averages |
| Median baseline | **Adapt** | Require a minimum count of *gated* samples, explicit reset policy, record baseline provenance |
| ε table (Moaveni via OMLC) | **Do not use as authoritative** | Secondary; wavelengths unverified; keep as test comparator only |
| DPF default 6/6; S&W formula | **Do not use as authoritative** | Range/site mismatch (F5); injected parameter with citation and range warning |
| Hard-coded 735/850, S–D distances | **Do not use** | DRAFT geometry; take from `optical_geometry_v1.json` |
| Absolute vs change | **Adopt** change semantics, label Δ | Without a measured I0 absolute values are not defined |
| Short-separation regression | **Adapt** | Per-detector validity, true ridge scaling, diagnostics (R², β), nearest/same-hemisphere policy TBD, warn on cerebral leakage assumption; needs bench evidence |
| Silent raw→corrected switch | **Do not use** | Show state explicitly |
| `extractNamedIntensities` validity | **Do not use** | Replace by composed gate (section 6) |
| Legacy protocol parser, bridge, text commands | **Do not use** | Not contract v0.4.3 |
| Detector labels PD0–3 | **Do not use** | Conflict with firmware names (section 5) |
| Hard-coded quality meter | **Do not use** | Not measured |
| Demo mode as evidence | **Do not use** | Synthetic constants |
| `PROCESSING_LIBRARY.md` claims | **Do not use** | Stale |
| Channel naming (`CH1_I735`, `_HbO_RAW/_CORR`) | **Adapt** | Only after geometry module owns naming |
| Test pattern: forward model + independent comparator | **Adopt** | Better than same-ε round trip; legacy tests alone are not scientific proof |
| Sweep plot UX | **Adapt** | Real time axis, visible gaps/disconnects, clamp indicator, Δ labels, no hard-coded quality |
| Dark-current correction | **Do not use** | Superseded by ON/OFF differencing in the contract |

## 8. Primary sources and verification status

| Source | Status |
|---|---|
| Scholkmann & Wolf 2013, J Biomed Opt 18(10):105004, DOI 10.1117/1.JBO.18.10.105004 | coefficients and range checked via publisher/abstract pages; reviewer should confirm signs in full text |
| Gagnon et al. 2014, NeuroImage 85(1):127–135, DOI 10.1016/j.neuroimage.2013.01.073 | bibliographic data verified |
| Yücel et al. 2015, Neurophotonics 2(3):035005, DOI 10.1117/1.NPh.2.3.035005 | bibliographic data verified |
| Zeff et al. 2007, PNAS 104(29):12169–12174, DOI 10.1073/pnas.0611266104 | bibliographic data verified |
| Saager & Berger 2005 (short-separation) | not retrieved; unverified |
| Moaveni 1970 / OMLC (Prahl) hemoglobin table; Takatani & Graham | OMLC values read through a summarizing fetch tool; reviewer must re-read the tables; primary Moaveni thesis not retrieved |
| PMC / dblp pages | blocked by bot protection; not used |

## 9. Checks run (commit SHAs and commands)

| Check | Result | Class |
|---|---|---|
| `node tests/fnirs-processing.test.js` in each of the three snapshots at `da7a9b1` (Node v22.23.4; PR used v24.12.0) | exit 0 | [unit] |
| `node --check` on `bridge.js`, `protocol.js`, `simulator.js`, `public/app.js`, `public/lib/*.js`, tests | OK | [code] |
| `NN2_GOLDEN_DIR=<pinned firmware vectors> python -m pytest -q -rs -p no:cacheprovider` at `da7a9b1` (Python 3.13.18) | 253 passed, 0 skipped, 0.35 s | [unit] |
| `gen_cases.py` (numpy seed 20261031) → `js_run.mjs` (unmodified legacy lib) → `compare.py` | see section 4 | [sim] |
| `gate_demo.py <repo_root>` | section 6 table | [sim] |
| sha256 of pinned firmware files (contract, frame vectors, gate vectors, gate configs, geometry) vs `config/reference_pins.json` | all equal | [code] |

Not run: bridge/simulator sockets, `app.js`, any real device, any acquisition. The scripts live outside the repo and can be added to a follow-up issue if the reviewer wants them stored (they use only numpy in a temporary venv).

## 10. Proposed staged implementation issues

All issues are follow-ups after review; each needs firmware-pin recording (AGENTS.md) and must not add runtime dependencies without owner approval.

| ID | Issue | Depends on | Risks | Testable acceptance criteria |
|---|---|---|---|---|
| S1 | Composed science gate in core: one function combining `science_mask`, `recheck_frame`, negative-control/LEAK_SUSPECT exclusion, wavelength-pair completeness, short-channel verification | none | thresholds are provisional (D5) | Scenarios A–I of section 6 produce: A, B accepted; C–I rejected with a specific reason code; golden vectors still 253 passing; no value reachable without passing the function |
| S2 | OD module: `I_clean` and `−ln(I_clean/I0)`; refuse non-positive, non-finite, None | S1 | I0 definition not yet decided | Values equal an independent comparator within 1e-12; invalid inputs raise or yield an explicit "no value" |
| S3 | Baseline policy: median over gated samples, minimum count, reset triggers, provenance record | S1, S2 | baseline with LEDs off (section 6) | A baseline built from non-gated frames is impossible by construction; reset events logged |
| S4 | MBLL core: injected ε/DPF/distance with citation fields and status `unverified`; range warnings; ΔHb in µM | S2, S3, geometry module | parameters unverified (wavelength, distance, DPF, ε) | Random-case comparator (1000 cases, max abs diff < 1e-12 µM); known-Δ recovery; ln vs log10 mismatch test; output labelled change-only; parameter set with status `unverified` is stamped in the output |
| S5 | Geometry naming: single module for AIN→PD and channel names; remove ambiguity with legacy labels | none | DRAFT geometry until caliper | Test fails if any other module translates AIN→PD; names match `optical_geometry_v1.json` |
| S6 | Short-separation core: causal regression, per-wavelength, true ridge, diagnostics, explicit raw/corrected state | S4 | cerebral leakage assumption (F9); needs bench evidence | OLS/ridge equal numpy; leakage test reproduces the κ table; state flag present in every output |
| S7 | UI consumption: UI reads core outputs only; real time axis, visible gaps, clamp indicator, Δ labels, measured (not hard-coded) quality | S4 | UI coupling | Core tests run without UI; UI contains no MBLL constants; disconnect shows a gap |
| S8 | Wavelength/geometry/ε/DPF verification plan (separate from software) | caliper, spectrometer, primary sources | needs hardware | Evidence records for D7; ε and DPF cited from primary sources with ranges |

## 11. Process notes

- The authoring session could not push or create issues/PRs for this repository (the GitHub App for the organization lacked access; remedy: install the Claude GitHub App at `https://github.com/apps/claude/installations/select_target` or reconnect GitHub in claude.ai connectors). The branch name should be changed to `docs/<issue-id>-legacy-host-reference-study` once the issue exists. This is a docs-only change; it must not be merged by an agent.
- Numeric statements in sections 4 and 6 come from scratch scripts outside the repo; an independent reviewer should re-run them or confirm against their own comparator.
