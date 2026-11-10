# Host D4 fix + kompatibilitas firmware Calib Stage-1 FIX1 — laporan

**Role** Implementer (Claude). Bukan reviewer; Codex akan me-review ulang.
**Kelas bukti** host/unit + simulasi + render Qt offscreen. Tidak ada perangkat, firmware, jaringan, push, PR, merge, atau rebase.
**Worktree** `C:\b5a-ads\host-d4fix`, branch `feat/20-host-d4-fix-offline`, dibuat dari `de9f1b4` (tidak ada rewrite; hash lama `d5cbd48`/`de9f1b4` utuh).
**Input diverifikasi** `pr-d4-codex-review\review.md` SHA-256 `d3b9bb56eb05b8e8ef23e5576fc00353a2522f1cd7266c40a419990ca70b54f8` (cocok). Patch firmware 0001/0002/0003 SHA-256 cocok dengan laporan FIX1. Clone dengan 6 file dirty tidak disentuh.

## Commit

Lihat bagian "Hash commit" di bawah (diisi setelah commit). Diff stat terhadap `de9f1b4` ada di akhir dokumen.

## Bagian A — D4-01..10

Node regresi (59 di `test_d4_fix.py` + 3 varian `test_exactly_three_rows` + `test_click_opens...`) dijalankan terhadap kode D4 lama (`de9f1b4`, satu proses per node): 55 gagal, 9 lulus. Yang lulus di kode lama adalah kontrol, bukan bukti (lihat catatan).

| ID | Perubahan | Lokasi | Tes / bukti (gagal sebelum → lulus sesudah) |
|---|---|---|---|
| D4-01 | `SessionWorker` tidak lagi anak Qt dari jendela, jadi penghapusan jendela tidak menghapus worker. `main()` memegang referensi worker sendiri dan memakai `shutdown_worker()`. `main()` menerima `run_factory/settings/documents/on_show` agar bisa dites. | `src/noctinirs_host_ui/engineering.py:121`, `:987`, `:995` | `test_d4_01_the_worker_survives_the_deletion_of_its_window`, `test_d4_01_main_returns_zero_after_closing_a_recording_session` (exit code 0, rekaman difinalisasi) |
| D4-02 | Live tidak lagi dibungkus scroll area; action bar dipin di atas splitter. Banner panel memakai `WrapLabel` (membungkus, tidak terpotong). Tombol panel diperkecil. Pesan feedback/error pindah ke baris navigasi (hemat satu baris), dibatasi 2 baris, teks penuh di tooltip. | `engineering.py:415` (action_bar), `:168` (messages), `:36`; `preview_panel.py` (banner, collapse, jump); `widgets.py:54` | `test_d4_02_action_bar_and_recovery_are_never_clipped_and_nothing_overflows` (3 ukuran × sidebar × normal/lost-START: fraksi terlihat 1.0 untuk START/STOP/Disconnect/Check START/SAFE_OFF di seluruh rantai ancestor), `..._work_from_the_keyboard` |
| D4-03 | Tinggi baris = (viewport − divider) / 3, lantai `ROW_MIN_H=48`, `DIVIDER_H=20`. Baris pendek menyembunyikan meta+legend (legend jejak pindah ke legend panel); status satu baris (daftar peringatan diringkas, lengkap di tooltip). Roda mouse berhenti di tepi baris. **Tinggi minimum jendela 560 → 600** (anggaran dibuktikan tes). | `preview_panel.py:186-189`, `:809`, `:856`; `widgets.py:351`; `engineering.py:32` | `test_d4_03_three_complete_rows_are_visible_in_every_supported_state` (12 kondisi, tanpa toleransi, di setiap posisi berhenti), `..._wheel_moves_one_row_at_a_time...`, `..._a_short_row_keeps_name_and_status...`; `test_exactly_three_rows...` diperketat (tanpa pengecualian MIN_WINDOW) |
| D4-04 | `peak_decimate` ditulis ulang di summarizer plot saja: tiap run finit didecimasi sendiri, satu pemisah NaN per gap, min/max menurut urutan waktu dan timestamp aslinya. Mapping channel dan pemrosesan tidak diubah. | `preview_panel.py:216` | NaN tunggal pada 12 sampel/budget 6; urutan max-sebelum-min; 12 seri acak (gap, ekstrem, titik hanya dari data asli, urutan waktu); gap tunggal terlihat di kurva sebenarnya |
| D4-05 | Satu sumber range bersama (`_shared_limits`) untuk Auto, ±skala, dan Full range, dengan histeresis sekali untuk semua baris. `_reset_ranges()` dipanggil saat lock, unlock, skala, mode, dan clear. Full range tidak lagi menjepit/menandai. | `preview_panel.py:664`, `:897` | `test_d4_05_*` (lock di tiga kebijakan skala, unlock kembali ke range sendiri, ganti mode, clear) |
| D4-06 | Satu animasi per kontrol dan dipakai ulang (chip, section, scroll, fade, sidebar). Reduce Motion menghentikan animasi berjalan dan menyelesaikannya ke keadaan akhir; efek opasitas dibuang. | `widgets.py` (`_motion_changed`, `_settle`), `engineering.py:505` | `test_d4_06_repeated_interactions_do_not_accumulate_animation_objects` (jumlah objek animasi tetap setelah 16 iterasi), `..._reduce_motion_settles_every_running_animation_immediately` |
| D4-07 | Margin footer diambil dari posisi plot sebenarnya (event filter pada plot master). Footer memakai `_ExactXViewBox` yang menyalin range X persis, karena `ViewBox.screenGeometry` bawaan pyqtgraph menambah offset widget di parent dan menggeser t=0 sebesar 8 px. | `preview_panel.py:268`, `:826` | `test_d4_07_*`: t=0, tengah, akhir pada 3 lebar × 2 window, baris CH1/CH7/SC2R, toleransi 1 px |
| D4-08 | `status_key` di-reset saat clear/epoch. | `preview_panel.py:691` | `test_d4_08_status_text_is_rebuilt_after_a_clear` |
| D4-09 | Legend: swatch berwarna + teks warna teks utama. `marker_warning` tema terang `#E69F00` → `#9C5F00`. | `preview_panel.py:604`, `design/tokens.py:64` | `test_d4_09_*` kedua tema; tabel di bawah |
| D4-10 | Assertion D3 dikembalikan: CH1 harus menampilkan ▲ (prioritas peringatan); keadaan tanpa koreksi diuji terpisah (◆ pada kanal tanpa peringatan); tooltip diuji lewat event Qt `QHelpEvent`, bukan `tip_provider` langsung. | `tests/ui/test_engineering.py` (`test_click_opens_channel_detail...`) | tes lulus; ini pemulihan kekuatan tes, perilaku sudah benar sebelumnya (lulus juga di kode lama) |

Kontras terukur (offscreen, `evidence/measurements.json`):

| Pasangan | Terang | Gelap | Target |
|---|---|---|---|
| Teks legend vs permukaan / baris terpilih | 13.72 / 12.15 | 13.56 / 11.91 | ≥ 4.5 |
| Swatch HbO vs permukaan / baris terpilih | 3.87 / 3.42 | 8.90 / 7.82 | ≥ 3 |
| Segitiga peringatan vs permukaan / baris terpilih / latar | 5.18 / 4.59 / 4.86 | 8.93 / 7.84 | teks ≥ 4.5, ikon ≥ 3 |

Layout terukur pada 900×600 (sidebar terbuka, kasus terburuk): normal viewport 237 px, baris 72 px; lost-START viewport 177 px, baris 52 px; tiga baris penuh dan semua tombol terlihat 100%. Screenshot offscreen ada di `evidence/` (9 PNG).

Catatan kejujuran bukti:
- Lulus di kode lama: `d4_02 ..._keyboard` (kontrol), `d4_05` untuk skala Auto dan ±1.0 (bug hanya di Full range dan reset), `d4_05 mode change`, tiga ukuran besar `test_exactly_three_rows`, dan `test_click_opens...` (D4-10, lihat di atas).
- Tes pemulihan lost-START yang diwarisi (race teks sementara yang dicatat review) gagal satu kali di salah satu proses suite kami. Assertion diganti dengan catatan log permanen; lulus 3/3 percobaan.
- **Tinggi minimum jendela naik dari 560 ke 600 px.** Review mengizinkan ("set minimum size consistently with that budget"). Tes lama mensyaratkan 1024×600 tetap didukung, jadi 600 adalah batas atas. Keputusan ini perlu persetujuan Owner.
- Perilaku `fully_visible_rows()` milik panel masih memakai toleransi 1 px; tes baru memakai `fully_visible_rows` strict sendiri.
- CPU 10 Hz (18%) dan stress 100 Hz (62%) tidak diukur ulang di sini (tidak ada replay native); dicatat untuk dipantau, bukan blocker.

## Bagian B — kompatibilitas firmware Calib Stage-1 FIX1

Sumber: patch 0001/0002/0003 dan `CALIB_STAGE1_FIX1_REPORT.md`, `FW_CALIB_STAGE1_IREF_PROFILE.md` (dirakit dari patch). Repo/worktree firmware tidak dibaca atau diubah.
**Penunjuk protokol** (`control/profile.py` docstring): base firmware `519a96a26ede6517595d4f8a87cd64ab6b7a6fa5` + patch 0001 `b3f98fdd…9c97`, 0002 `1c57ebe3…4ef8`, 0003 `437e73fe…cc4c`; ADR-0004 "local amendment (Stage 1 calibration FIX1) 2026-11-10" (Proposed). Seri patch belum menjadi commit, jadi tidak ada SHA commit.

### Tabel kompatibilitas per perintah

| Perintah | Berubah di firmware baru? | Penanganan host |
|---|---|---|
| INFO 0x0002 | Ya: token policy bertambah (`legacy_current=RECORDED_ONLY`, `iref_*`) dan V4 menambah `wire_minor=1;profile_api=IREF1;profile_state;profile_source;applied_profile_id;applied_valid;start_result` | Parser `key=value` lama mengabaikan key tak dikenal. Tes dengan teks INFO FIX1 lengkap. Tidak ada perubahan kode. |
| STATUS 0x0003 | Tidak berubah. **Tetapi** START yang berakhir setelah ACK membuat balasan STATUS ber-session-id 0 | **Bug host ditemukan dan diperbaiki** (B-01 di bawah) |
| get-config 0x0004 | Byte-identik (42 B) | Tidak berubah |
| START 0x0010 | ACK mendahului apply IREF. Hasil bisa `PROFILE_NOT_APPLIED` (kembali STOPPED, tanpa fault), `START_FAILED` (FAULTED sticky), `START_CANCELLED` (STOPPED) | Setelah perbaikan B-01 sesi yang berakhir terdeteksi lewat alur lama ("Owned session ended"); profil dibaca dan dicatat sebelum rekaman difinalisasi |
| STOP 0x0011 | STOP normal menunda RESET# hingga ≤ 1 s (bukti IREF dibaca dulu) | Host menunggu 5 s (`STOP_WAIT_S`) → cukup. Tidak ada perubahan. |
| SAFE_OFF, CLEAR_FAULT, heartbeat, get-time, set-fs, set-mode | Tidak berubah | Tidak berubah |
| set-led-current 0x0101 | Nilai hanya dicatat, tidak diterapkan ke LED | Engineering UI tidak memakainya. **`control_panel.py` (panel kontrol lama) masih punya kontrol arus LED yang akan tampak berfungsi padahal tidak berefek.** Tidak diubah di sini; lihat risiko. |
| get-battery 0x0033 | Tidak berubah | Tidak berubah |
| get-profile 0x0034 | Baru | Parser baru, read-only (di bawah) |
| set-profile 0x0103 / clear-profile 0x0104 | Baru | Hanya encoder `args_set_profile` + opcode (`MUTATING_OPS`). Tidak ada kode host yang memanggilnya (dijaga tes statik). Tidak ada wizard, tidak ada pengiriman otomatis. |
| Balasan opcode tak dikenal berisi data | — | Tetap fail-closed seperti sebelumnya (diuji) |

**B-01 (perbaikan host).** `SessionController._accept` membuang balasan OK yang session-id-nya berbeda dari milik host. Perangkat FIX1 yang mengakhiri START setelah ACK membalas STATUS dengan session id 0 dan state STOPPED/FAULTED; host lama menghitungnya `wrong_session`, STATUS habis waktu, dan `EngineeringRun` memutus link ("Control poll timed out"). Sekarang balasan dengan session 0 dan state yang bukan state-kepemilikan diterima (`session.py`, `session_gone`). Tes: `test_a_status_reply_after_a_refused_start_is_not_dropped_as_a_wrong_session` dan tiga skenario START gagal di `test_profile.py` gagal tanpa perbaikan (diverifikasi dengan menonaktifkan perbaikan) dan lulus dengan perbaikan.

### Format get-profile yang dipakai

FIX1: `v4profile=1 profile_state profile_armed profile_id profile_hash [profile_code] last_profile_reject reject_slot default_code profile_source start_result mismatch_slot applied_profile_id applied_profile_hash requested_code applied_code applied_read applied_valid requested_ua applied_ua applied_at_stop stop_evidence stop_integrity stop_match stop_slot stop_error legacy_requested_ma iref_ua_per_lsb iref_code_default iref_code_max`; `stop_read` dihapus; worst case 1404 / 1412 B (+28 B prefix = 1432 ≤ `MAX_PAYLOAD` 1440). Juga diterima tanpa error: `v4profile=0 reason=unsupported|overflow`, UNKNOWN_COMMAND (firmware lama), balasan kosong/tak dikenal, versi lebih baru, format pra-FIX1 dengan `stop_read` (diabaikan), key tak dikenal (disimpan apa adanya), nilai enum baru (peringatan), nilai rusak (None + peringatan).

Aturan integritas: host menulis `stop_integrity` dari perangkat hanya bila cocok dengan kelas bukti yang didokumentasikan firmware (VALID_MATCH→OK, VALID_MISMATCH/TRANSPORT_ERR→BLOCKED, lainnya UNVERIFIED). Bukti hilang, tidak dikenal, atau kontradiktif menjadi UNVERIFIED; host tidak pernah menyimpulkan OK. `PREEMPTED` bukan nilai wire FIX1 (hasil internal capture yang dipetakan firmware ke SKIPPED_EMERGENCY/TIMEOUT); host menerimanya secara defensif sebagai UNVERIFIED.

Kapan dibaca (hanya saat tidak mengganggu heartbeat/transisi): `connect`, `armed`, `pre_start`, `running` (satu tick setelah STREAMING agar heartbeat pemilik lebih dulu), `ended` (STOP normal, START gagal, fault, SAFE_OFF; sebelum rekaman difinalisasi), `diagnostics`. Ringkasan tampil di panel "Sesi dan status" ("LED profile: ...") dan log sesi.

### Contoh event sidecar

```
kind=profile       phase=running boot=0x5151 session=101 plan_epoch=1 supported=1 state=APPLIED armed=1 id=0xA1B2C3D4 hash=1011…2e2f source=PROFILE start_result=OK mismatch_slot=-1 applied_id=0xA1B2C3D4 applied_hash=1011…2e2f applied_valid=1 stop_evidence=NONE stop_integrity=UNVERIFIED stop_slot=-1 stop_error=0
kind=profile       phase=ended   … state=ARMED … applied_valid=0 stop_evidence=VALID_MATCH stop_integrity=OK stop_slot=-1 stop_error=0
kind=profile       phase=ended DATA_INTEGRITY_BLOCKED … stop_evidence=VALID_MISMATCH stop_integrity=BLOCKED stop_slot=6
kind=profile       phase=ended … start_result=START_FAILED … applied_valid=0 stop_evidence=SKIPPED_EMERGENCY stop_integrity=UNVERIFIED
kind=profile_codes codes requested=133,… applied=133,… at_stop=133,…
```
(hash dipendekkan di sini; di sidecar penuh 64 hex; semua event ≤ 500 karakter, batas sidecar 512.) Firmware tidak mengirim "arm epoch" di wire; sebagai penanda epoch host mencatat `boot`, `session`, dan `plan_epoch` RAW.

### nn2ctl
`tools/nn2ctl.py` ada di repo firmware dan tidak dibuka. Berdasarkan laporan FIX1 (butir risiko 11) dan daftar file patch, patch tidak mengubah `nn2ctl.py`: ia belum punya sub-perintah get/set/clear-profile, dan menolak panjang `get-config` selain 42 B (tidak berubah). Yang perlu dilakukan di repo firmware (oleh pemiliknya): tambah decoder get-profile yang mengabaikan key tak dikenal dengan set key FIX1 (tanpa `stop_read`). Belum diverifikasi terhadap isi file.

## Tes

| Kumpulan | Hasil |
|---|---|
| Baseline D4 `de9f1b4` (ekspor bersih, lingkungan ini) | 672 lulus, 1 gagal, 6 dilewati. Kegagalan: `PermissionError` pada `rec.json` di `test_idle_recorder_refreshes_m0` (berkas temp Windows; tidak terkait perubahan, tidak muncul di tiga run Codex dan tidak di run akhir kami). |
| Baseline menurut review Codex | 673 lulus, 0 gagal, 6 dilewati (3 run) |
| **Akhir, commit ini (run penuh terakhir)** | **776 lulus, 0 gagal, 6 dilewati** (347 s) |
| Run penuh sebelumnya pada pohon yang sama sebelum dua perbaikan kecil | 775 lulus + 1 gagal (anggaran 1 px, lalu diperbaiki), lalu 1 gagal (race pemulihan, lalu diperbaiki) |

Tes baru: 59 node di `test_d4_fix.py`, 42 di `test_profile.py`, 2 di `test_profile_ui.py` (+103). Total node 679 → 782 (776 lulus + 6 skip); tidak ada node D4 yang dihapus atau diganti nama, tiga tes lama diperketat/diperkuat (`test_exactly_three_rows...`, `test_click_opens...`, assertion race pemulihan).
Kegagalan bawaan yang tidak berubah: 6 skip `test_golden.py` (`NN2_GOLDEN_DIR` tidak diset), identik dengan review.

## Menjalankan host dari worktree baru

```bash
cd C:\b5a-ads\host-d4fix
$env:PYTHONPATH='src'
C:\Users\doeki\Documents\CODEX\2026-11-07\kerjakan-pr-a-pada-repo-yerrisuyudhasim\venv-stageD-ui\Scripts\python.exe -m noctinirs_host_ui.engineering
```
(Memakai fake lokal "Local RAW fake"; tidak menyentuh perangkat sampai Owner memilih endpoint UDP.) Tes: `$env:PYTHONPATH='src;tests'; python -m pytest -q`.

## Risiko terbuka

1. Bukti layout hanya offscreen; belum diperiksa di Windows native (font Segoe UI berbeda dari font fallback offscreen). Anggaran 3 baris pada 900×600 pada kasus terburuk hanya memiliki ~9 px cadangan.
2. Tinggi minimum 600 px (naik dari 560) perlu persetujuan Owner.
3. Bukan bukti perangkat: format get-profile diambil dari sumber patch dan laporan, bukan dari balasan firmware sungguhan. Tes memakai model (`EngineeringFake(profile_model=True)`) yang ditulis dari patch.
4. START yang gagal dengan FAULT sticky (`START_FAILED`, keputusan Owner Q-IR-B) membuat perangkat FAULTED; Engineering UI tidak punya kontrol CLEAR_FAULT (hanya SAFE_OFF). Tidak ditambahkan karena di luar lingkup.
5. `control_panel.py` masih menawarkan arus LED yang pada firmware baru hanya dicatat.
6. Penambahan trafik baca (get-profile) tiap fase: satu permintaan 0.4 s tanpa retry; pada perangkat yang menjawab OK dengan isi kosong dicatat sebagai "tidak tersedia".
7. Footer memakai view box yang menyalin range persis; bergantung pada tata letak footer = plot (dijaga tes), bukan pada perilaku internal pyqtgraph.
8. Satu node uji pemulihan lost-START sebelumnya rawan race; assertion sudah diperkuat, tetapi test lain berbasis `wait_for` waktu nyata tetap sensitif beban mesin.
