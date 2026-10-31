# Study: historical live-view snapshots under `Reference/` (stage 1: study and recommendation only)

- Status: DRAFT for independent review. Not merged. No host source, dependency, or snapshot was changed.
- Author: Claude (Sonnet 5.5). Independent review must come from a different model family (AGENTS.md; hub section 5.1).
- Issue: #9
- Snapshots studied: PR #8, head `da7a9b1bad428e57e2d9cebf00fa2711b5511892` (`Reference/` does not exist on `main` `5eb05ff`).
- Contract basis: optical data contract v0.4.3, firmware pin `e42f2bc9b453b223bf4557516f63fcceadfddbc8` (`docs/drafts/OPTICAL_DATA_CONTRACT_DRAFT.md`, sha256 `4dd2b89b…` equals the host pin). The legacy RAW layout is not used as a basis for any recommendation.
- Evidence classes used: **[code]** code inspection, **[unit]** an existing or newly run unit test, **[sim]** synthetic numeric simulation with an independent comparator. None of them is hardware evidence.

## 0. Ringkasan untuk pemilik (Bahasa Indonesia)

1. **Ya, salah satu versi memang menghitung MBLL dan menampilkan HbO/HbR dari data live, dan itu terbukti dari kode, bukan asumsi.** Rantai live di `public/app.js` benar-benar tersambung: frame → intensitas → baseline (median) → OD (`ln`) → MBLL 2×2 → ΔHbO/ΔHbR (µM) → grafik. Mode demo memakai angka sintetis dan *tidak* melewati rantai ini, jadi grafik demo bukan bukti MBLL.
2. **Ketiga snapshot identik** untuk seluruh 22 file di dalam tiap folder `offline/` (9 di antaranya JavaScript; sha256 sama, termasuk `package-lock.json`). Dua `package-lock.json` tambahan ada di folder pembungkus `white-blue/` dan `white-blue/white-blue/`, di luar `offline/`. Jadi tidak ada versi yang "lebih lengkap"; kemampuan MBLL-nya sama.
3. **Matematika MBLL-nya benar** untuk konvensi yang dipakainya: dibandingkan dengan implementasi independen (numpy) pada 1000 kasus acak, selisih maksimum 6,7e-16 µM. Itu hanya membuktikan aljabar dan satuan, **bukan** kebenaran ilmiah.
4. **Yang tidak terbukti dan tidak boleh diambil dari aplikasi lama (belum terverifikasi):** panjang gelombang sebenarnya (735/850 nominal), tabel koefisien ekstingsi (Moaveni 1970 via OMLC, sumber sekunder), DPF (rumus Scholkmann & Wolf 2013 dipakai di 850 nm, di luar rentang fit 690–832 nm), jarak S–D (masih DRAFT sampai diukur kaliper).
5. **Bahaya terbesar versi lama: tidak ada quality gate.** Slot yang LED-nya tidak pernah menyala tetap dianggap "valid" dan menghasilkan angka ΔHb yang tampak seperti sinyal (simulasi dengan asumsi ambient ≈1800 hitungan dan derau σ=12: SD ≈0,2–0,35 µM untuk ΔHbO, tanpa bendera apa pun). Itu derau buatan, bukan fisiologi; angka persis dari simulasi penulis belum bisa direproduksi oleh reviewer, hanya mekanismenya. Host baru harus memakai gate kontrak v0.4.3.
6. **Temuan penting di host baru (saat ini):** gate terdiri dari *dua* bagian yang belum digabung (`science_mask` dan `recheck_frame`). Frame "lolos palsu" dari firmware masih lolos `science_mask` bila konsumen hanya memakai itu. Perlu satu fungsi gate gabungan sebelum MBLL ditulis (issue S1 di bawah). Catatan: contoh frame "normal" pada tabel uji ternyata juga berisi detektor sisi berlawanan dengan tanda lolos, yang menurut kontrak seharusnya ditandai kebocoran; jadi tabel itu hanya menunjukkan perilaku API sekarang, bukan hasil gate lengkap..
7. **Label detektor lama (PD0–PD3 = indeks AIN) berbeda dengan nama fotodioda firmware** (AIN0=PD3, AIN1=PD4, AIN2=PD1, AIN3=PD2). Semua 20 label berbeda. Jangan memakai label lama.
8. **Rekomendasi:** ambil *ide dan pola uji* (matematika MBLL sebagai inti Python tanpa UI, parameter ε/DPF/jarak diinjeksi dengan catatan sumber, baseline median dengan aturan eksplisit, regresi short-separation yang di-adaptasi, uji "maju-mundur" dengan pembanding independen). Jangan ambil parser protokol, bridge, perintah, konstanta keras, indikator "quality", dan label geometri.
9. **Langkah berikutnya untuk Anda:** reviu laporan ini oleh model non-Claude, lalu putuskan issue implementasi bertahap (S1–S8; S8 butuh perangkat keras dan otorisasi terpisah). Tidak ada perangkat yang dihubungkan dan tidak ada akuisisi yang dijalankan.

## 1. Scope, constraints, method

Read: `AGENTS.md`, `project.yaml`, `Reference/README.md`, active host `src/noctinirs_host/{wire/optical.py,gate.py,geometry.py}`, `config/optical_geometry_v1.json`, `config/reference_pins.json`, the pinned contract and golden vectors. Snapshot comparison was by content hash. Snapshots were not modified (worktree stayed clean). No sockets were opened, `bridge.js`/`simulator.js` were not run, `app.js` was not executed (needs a DOM), no device was contacted.

All verification scripts and outputs live outside the repository (session scratchpad). Test dependencies were installed only in a temporary venv outside the repo: Python 3.13.18, numpy 2.5.4, pytest 9.2.0, iniconfig 2.3.1, packaging 26.3, pluggy 1.7.0, Pygments 2.21.0. `pyproject.toml`, requirements and lockfiles are unchanged.

## 2. Snapshot comparison

Paths below are relative to `Reference/`.

| Snapshot | Files under `offline/` (22) | `package-lock.json` | Notes |
|---|---|---|---|
| `legacy/liveview/offline/` | baseline (22 tracked files, 9 JavaScript, including `package-lock.json`) | `package-lock.json` inside `offline/`, locks `ws 8.21.0` only | the working copy |
| `legacy/liveview/white-blue/offline/` | byte-identical (sha256) to baseline, including its lock | same lock as baseline | the wrapper folder `white-blue/` has its own extra empty-`packages` lock outside `offline/` |
| `legacy/liveview/white-blue/white-blue/offline/` | byte-identical (sha256) to baseline, including its lock | same lock as baseline | `white-blue/white-blue/` also has an extra wrapper lock (empty `"packages": {}`, named `…white-blue-ui`) outside `offline/`; both wrapper locks have identical hashes |

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
| F5 | DPF default 6.0/6.0 (manual); optional Scholkmann & Wolf 2013 (J Biomed Opt 18(10):105004, DOI 10.1117/1.JBO.18.10.105004), coefficients match the published formula. Per the paper: the fitted surface used ages 0–50 at four wavelengths (690, 744, 807, 832 nm) from frontal/frontotemporal data at d = 4.3 cm (homogeneous model); the authors recommend use for ages 0–70 and 690–832 nm after validation (d > 2.5 cm) and discuss extrapolation to 832–950 nm as likely safe but not validated. Legacy applies it at 850 nm (inside the authors' discussed extrapolation range, outside the recommended range) with no range warning. Coefficients and signs checked against Eq. 7 of the full text by the independent reviewer. Age 28 gives 6.255 (735 nm) and 5.177 (850 nm). Applicability to the device's S–D 18.5–30 mm and to non-frontal sites is unverified. `applyLiveSettings` forces `dpfMode="manual"`. | [code][sim]; **belum terverifikasi** |
| F6 | Sensitivity (illustrative ΔHbO=5, ΔHbR=−2 µM): S&W(28 y) vs 6/6 → HbO −11.6 %, HbR −6.8 %; +20 % DPF both → −16.7 %; ±1 mm at 30 mm → ∓3.2–3.4 %, at 18.5 mm → ∓5.1–5.7 %; ε_HbR(735) true value ±10 % away from the assumed table → HbR ±12.7 %, HbO ±3.8 % (first-order estimate, true-minus-assumed direction; the independent reviewer's exact finite perturbation in the reverse direction, assumed table ±10 % away from truth, gave HbR +14.5 %/−11.2 %, HbO +4.4 %/−3.4 %, so state the direction and denominator when quoting). Result scales as 1/distance and 1/DPF. | [sim] |
| F7 | Edge cases of the library: `opticalDensity` throws for 0, negative, NaN, Infinity, null; `estimateReference([])` throws; zeros enter the median; intensity above reference gives negative OD (allowed). **`mbllFromOpticalDensity` accepts `null`/`""` OD via `Number()` and silently treats them as 0** (returns numbers). `darkCorrect` below dark → null. | [sim][code] |
| F8 | Baseline: median over positive finite samples per channel/wavelength for `baselineDuration × frameRate` **frames** (not valid samples). Every start/resume/settings change (including Pause→Resume) resets the baseline. `frameRateHz` comes from the bridge's 3-s measured rate (default 10). | [code] |
| F9 | Short-separation: regression in the OD domain per wavelength, per hemisphere (not nearest channel), window max(60, fs×30), min 30 samples, OLS for one regressor, ridge for two. The ridge adds λ=1e-3 to the standardized XᵀX; with n=300 that is λ/n ≈ 3.3e-6, a genuine but very weak ridge for this design (coefficients differ 1e-3 relative from OLS at r=0.9988). The λ policy, normalization and selection of regressors are therefore a design decision, not an implementation bug. OLS/ridge match numpy. Assumption: the short channel contains only superficial signal. If it contains a fraction κ of the cerebral signal (superficial gain 2.5), residual error relative to cerebral RMS: κ=0.05 → 0.127, 0.10 → 0.252, 0.20 → 0.502, 0.40 → 1.0 (reviewer's independent orthogonal-sinusoid model: 0.125, 0.251, 0.501, 1.000). This covers cerebral leakage only; it does not cover correlation between cerebral and superficial signals, spatial mismatch of the short channel, or task-related confounds. The cited papers use different methods (Gagnon 2014 local superficial regressors; Yücel 2015 simultaneous HRF/short-channel GLM with the most correlated short channel and exclusions; Saager & Berger 2005 two-layer least-squares, which treats finite correlation and near-channel sensitivity to the lower layer as error sources); none validates this app's rolling OD subtraction or its same-hemisphere selection. | [sim] |
| F10 | UI shows RAW Hb until the regressor is ready (30 samples), then switches silently to CORRECTED (chip changes from "WARM" = not ready/raw to "GOOD" = ready/corrected); switch offset −0.0009 µM, max 0.0244 µM, RMS 0.0141 µM against raw excursion RMS 0.0056 µM (synthetic). | [sim] |
| F11 | `extractNamedIntensities` validity (`fnirs-geometry.js`): only `valid !== false && crcDropped !== true && finite` (`null`/empty intensity is coerced to numeric 0 and treated as valid at extraction, but `safeOd` later rejects 0 in the live path). `requireFinite` (`fnirs-mbll.js`) only coerces with `Number()` and checks finiteness; it is not a frame-validity check. `underK`, saturation (INT32_MAX), ON/OFF, quality are ignored. | [code][sim] |
| F12 | In-process decode chain simulation (legacy `buildStreamFrame`/`buildUdpPacket`/`parse*`; `app.js` sequence mirrored, `app.js` itself not run): imposed ΔHbO=+3, ΔHbR=−1 µM recovered within 6.7e-5 µM. | [sim] |
| F13 | Legacy tests (`tests/fnirs-processing.test.js`) pass in all three snapshots (Node v22.23.4) but only check forward/inverse algebra with the same ε plus mapping and regression recovery. `simulator.js` adds identical physiology to all wavelengths with `validMask` all ones, so it cannot validate MBLL. | [unit][code] |
| F14 | Display issues: x-axis is a frame counter modulo window, not time; gaps shorter than ~1/3 of the window (≈99 frames) are bridged by a straight line; the graph freezes without a time gap on disconnect, although the connection dots and text do show the state, and since no sample counter advances during a total disconnect the reconnect draws adjacent points regardless of elapsed time; `mapY` clamps silently to ±scale; the "quality" meter is hard-coded (94/72/15), not measured; ln-OD is labelled "mOD" (non-standard against decadic OD); the sweep legend says "HbO/HbR" while cards say ΔHbO/ΔHbR µM; the welcome label "MBLL ready · 735 / 850 nm · Moaveni 1970" implies validation. | [code] |

### 4.1 What cannot be proven from the snapshots

Actual LED wavelengths (contract D7 open); ε table choice and primary-source numbers; DPF values; S–D distances (firmware geometry is DRAFT until caliper measurement; the legacy distances equal the firmware DRAFT values and so are not independent confirmation); the legacy meaning of `averages` (ON versus ON−OFF); whether legacy firmware cleared `valid` when `underK` was set; physical correctness on a phantom or a subject. Wavelength, distance, DPF and ε must **not** be decided from the legacy app.

## 5. Geometry check

Against the pinned `config/optical_geometry_v1.json` (sha256 `035c00f1…`): 20 channels compared, 0 mismatches in (source, AIN, distance); slot mapping `source*2 + wavelength` 0 mismatches over 10 sources. **But** legacy detector labels `PD0–PD3` equal AIN indices, while the firmware names are AIN0=PD3, AIN1=PD4, AIN2=PD1, AIN3=PD2; all 20 labels differ (for example legacy `SC1L` = PD2 = AIN2 = firmware PD1). AIN→PD translation must be done only in `geometry.Geometry.pd_for_ain` (contract: one module, once).

## 6. Quality gate: where it sits in the active host, and where legacy skipped it

Active host (at `da7a9b1`), gate parameters = firmware `gate_config_id=1` (marked `provisional_unmeasured`; thresholds are to come from the dark-box bench, D5). Two separate pieces exist:

1. `wire/optical.py`: `Opt2Frame.is_science`, `science_mask()` / `science_sample()` (single choke point for values). Enforces: not ENGINEERING/STARTING_GATE/WARMUP, quality ∈ {1 (VALID_MINIMUM), 2 (VALID_TARGET)}, reason 0, host-invalid, verified mask, values fully present.
2. `gate.py`: `recheck_frame(frame, params)`; recomputes step_z/floor/stable/diff_negative, witness, saturation, `gate_config_id`. The recheck is one-directional (catches false passes, not false rejections; contract §10.8).

Probe results (synthetic frames from `tests/helpers.py`, `verify/gate_demo.py`):

| Scenario | `science_mask(slot 0)` | `recheck_frame` | Naive consumer (`science_mask` only) | Existing two APIs combined |
|---|---|---|---|---|
| A clean, quality 2 | 0xF | none | uses | passes both |
| B quality 1 | 0xF | none | uses | passes both |
| C ENGINEERING frame | 0x0 | n/a | refuses | refuses |
| D STARTING_GATE | 0x0 | n/a | refuses | refuses |
| E WARMUP | 0x0 | n/a | refuses | refuses |
| F quality 0, LED_ON_MISSING | 0x0 | n/a | refuses | refuses |
| G quality 2 but ON−OFF = 1 count | 0xF | `false_pass`, `floor_flag_differs`, `step_z_flag_differs`, `witness_fails_host_check` | **uses** | refused by `recheck_frame` |
| H quality 2 but stab_q=100 | 0xF | `false_pass` and flag mismatches | **uses** | refused by `recheck_frame` |
| I `gate_config_id` differs | 0xF | `gate_config_id_mismatch` (frame level, slot −1) | **uses** | refused by `recheck_frame` |

**Important limit of scenarios A and B.** They come from `tests/helpers.py`, where slot 0 uses source L1 (left) and the mask `0xF` also includes the opposite-side detectors AIN0/PD3 and AIN1/PD4 with passing STEP_Z flags. Contract §10 item 3 requires such contralateral detectors to be flagged LEAK_SUSPECT and the slot excluded. So A and B show only what the two **existing** APIs do; they are not positive fixtures for a composed gate, which does not exist yet. S1 must build ipsilateral-only fixtures and a separate leak fixture. (Reviewer-confirmed; reproduction note: the table was built from `tests/helpers.py` frames.)

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
| Zeff et al. 2007, PNAS 104(29):12169–12174, DOI 10.1073/pnas.0611266104 | bibliographic data verified; no algorithmic claim in this report is attributed to it; its processing subsection was not verified by the author or the reviewer |
| Saager & Berger 2005, DOI 10.1364/JOSAA.22.001874 | not retrieved by the author; the independent reviewer retrieved the author-provided full text (two-layer least-squares subtraction; finite correlation and near-channel sensitivity treated as separate error sources). A later revision can cite it directly |
| Moaveni 1970 / OMLC (Prahl) hemoglobin table; Takatani & Graham | OMLC tables (Moaveni, Takatani) were re-read by the independent reviewer in the actual table text and match the values above (Takatani interpolated at 850 nm: 1058/820; Prahl's separate summary compilation differs and must not be conflated). Original Moaveni 1970 thesis and original Takatani & Graham tables **not obtained: NOT VERIFIED**. Note: the indexed Takatani & Graham paper is 1979 (DOI 10.1109/TBME.1979.326455), not 1987 as OMLC states |
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
| S1 | Composed science gate in core: one function combining `science_mask`, `recheck_frame`, negative-control/LEAK_SUSPECT exclusion, wavelength-pair completeness, short-channel verification | none | thresholds are provisional (D5) | Fixtures: (a) a valid ipsilateral-only pair (735 and 850 of the same source/detector in the same frame, opposite-side STEP_Z clear) is accepted; (b) a contralateral STEP_Z pass yields LEAK_SUSPECT and the slot is excluded; (c) an incomplete wavelength pair is rejected; (d) short-channel regression input without verified short channels is rejected; (e) scenarios C–I of section 6 are rejected, each with a specific reason code; all applicable golden vectors pass with zero skips (the suite count is a historical datum, not a criterion). "No value reachable" is scoped to the public core API: values are returned only by one function in the core, and a test fails if another public path exposes unverified values |
| S2 | OD module: `I_clean` and `−ln(I_clean/I0)`; refuse non-positive, non-finite, None | S1 | I0 definition not yet decided | Values equal an independent comparator within 1e-12 over a stated domain (intensities 1…2³¹−1, I0 > 0); zero, negative, NaN, None and "" inputs raise or yield an explicit "no value" and are never coerced to 0 |
| S3 | Baseline policy: median over gated samples, minimum count, reset triggers, provenance record | S1, S2 | baseline with LEDs off (section 6) | The minimum number of gated samples and the list of reset triggers (start, resume, parameter change, gate-configuration change, reconnection) are stated in the issue before implementation; a baseline built from non-gated frames is impossible by construction; reset events are logged with a reason |
| S4 | MBLL core: injected ε/DPF/distance with citation fields and status `unverified`; range warnings; ΔHb in µM | S2, S3, geometry module | parameters unverified (wavelength, distance, DPF, ε) | Random-case comparator (1000 cases, max abs diff < 1e-12 µM); known-Δ recovery; ln vs log10 mismatch test; output labelled change-only; parameter set with status `unverified` is stamped in the output |
| S5 | Geometry naming: single module for AIN→PD and channel names; remove ambiguity with legacy labels | none | DRAFT geometry until caliper | Test fails if any other module translates AIN→PD; names match `optical_geometry_v1.json` |
| S6 | Short-separation core: causal regression, per-wavelength, objective with a stated normalization and λ-selection policy, diagnostics, explicit raw/corrected state | S4 | cerebral leakage, cerebral–superficial correlation and spatial mismatch (F9); needs bench evidence | OLS/ridge equal an independent comparator within a stated tolerance (1e-9 relative); a documented synthetic leakage fixture (inputs stored with the test) reproduces the κ trend within a stated tolerance; state flag present in every output |
| S7 | UI consumption: UI reads core outputs only; real time axis, visible gaps, clamp indicator, Δ labels, measured (not hard-coded) quality | S4, and S6 when correction is shown | UI coupling | Core tests run without UI; UI contains no MBLL constants; disconnect shows a gap |
| S8 | Wavelength/geometry/ε/DPF verification plan (separate from software) | caliper, spectrometer, primary sources | needs hardware | Evidence record per quantity (measured value, instrument, uncertainty, date, operator, artifact hash) for D7 wavelengths and caliper distances; ε and DPF cited from primary sources with ranges; needs separate owner authorization for any hardware work |

## 11. Process notes

- Branch/issue created after the owner linked GitHub; the first push attempts had failed with 403 (access, not content). This is a docs-only change; it must not be merged by an agent.
- Numeric statements in sections 4 and 6 come from the author's scratch scripts, which are not in the repository. The independent reviewer (GPT/Codex, head `474998c`) reproduced the mechanisms and MBLL algebra with its own comparator (2000 random cases, max abs diff 1.07e-14 µM; 36 known-Δ cases max 5.3e-15 µM) but **could not reproduce the exact author statistics** (bypass SDs 0.183/0.340 µM, the −56.6/−51.0 µM transition, the F10 switch offsets, the 6.7e-5 µM chain error). Reviewer's own bypass model (ambient 1800, σ=12, 100-frame median baseline, 10 000 frames): CH1 ΔHbO SD 0.218 / max 0.831 µM, ΔHbR SD 0.144; CH6 ΔHbO SD 0.353 / max 1.382, ΔHbR SD 0.235; +1 % drift CH1 −0.135/−0.122 µM, CH6 −0.219/−0.197 µM; off 1800 → on 180 000 transition CH1 −62.4/−56.2 µM. Treat the author's exact figures as **unverified**; the safe conclusion is only that ungated positive intensity yields finite ΔHb-looking output. Noise-driven values are not physiology. Author script hashes (sha256, first 16 hex): `gen_cases.py` 339ff0c3d9e6435d, `js_run.mjs` 3a23db9042cf05ec, `compare.py` 8ce5a907fdc79c29, `gate_demo.py` bd2305b7082ed238; they are handed to the owner as a separate file bundle and can be added to the repository only if the owner approves.
- Scope limits (reviewer coverage notes): the hub policy revision and the role-assignment record were not separately identified; architecture reading is limited because no architecture ADR exists in the reviewed tree (ADR-0001 is future/TBD); the `darkCorrectIntensity` helper is used by other library helpers and is unused only by `app.js`; the baseline estimator is configurable (median default, mean selectable); `app.js` was inspected statically and never run in a DOM.
