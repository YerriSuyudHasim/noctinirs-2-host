# Host D4 fix 2 — penutupan temuan review Codex (H-01..H-05, H-08) — laporan

**Role** Implementer. Bukan reviewer; Codex akan me-review ulang.
**Kelas bukti** host/unit + simulasi (fake firmware di host) + Qt offscreen. Tidak ada perangkat, flash, jaringan ke device, push, PR, merge, rebase, atau amend.
**Worktree** `C:\b5a-ads\host-d4fix`, branch `feat/20-host-d4-fix-offline`.
**Base** `1565124` (bersih). **Commit kode** `281a118` (di atas `1565124`). Laporan ini ada di commit berikutnya (`docs(20): ...`), jadi hash commit laporan tidak bisa ditulis di dalam laporan itu sendiri.
**Tidak disentuh** clone `...\CODEX\2026-11-07\...\noctinirs-2-host` (6 file dirty tetap apa adanya), repo/worktree firmware (`calib1`, `fixed`, `bat`, `calib-stage1`). Sumber firmware (`calib-stage1\patches`, `calib1`) hanya dibaca.
**Acuan firmware** `nn2_app_comm.c` versi FIX2 (`be27c49`, patch 0004, amandemen 2026-11-10).

## Ringkasan

- **H-01**: setiap event `profile` sekarang menulis provenance secara eksplisit: `attributed`, `basis`, `reply_session`, `reply_state`, `boot`, dan `candidate_session` bila relevan. `session=` hanya ditulis kalau laporan benar-benar milik run ini (`basis=owner`). Balasan dengan session lain ditulis sebagai `profile_device` (diagnostik perangkat), bukan bukti run.
- **H-02**: kunci integritas yang duplikat atau saling bertentangan membuat integritas menjadi `UNVERIFIED`, disertai peringatan. `OK` hanya diberikan untuk `VALID_MATCH` dengan `stop_integrity=OK`, `stop_match=1`, dan `stop_error=0`. Nilai mentah dari perangkat tetap disimpan.
- **H-03**: scroll juga snap ke batas baris saat slider dilepas dan saat tombol/klik/keyboard memakai step atau page, termasuk saat melewati divider hemisfer.
- **H-04**: tes penolakan sekarang meniru perilaku firmware FIX2 (`INVALID_STATE`, session 0). Pengecualian B-01 dipersempit: hanya STATUS/INFO/GET_CONFIG dengan session 0 dan state terminal.
- **H-05**: ACK SAFE_OFF dicatat sebagai event tersendiri. Get-profile dibaca sekali saja, setelah jeda 1 s (batas 3 s). Kalau START ACK tidak pernah diterima, ditulis event `phase=end_missing`.
- **H-08**: dokumen lease/refresh diperbaiki dan mengutip amandemen FIX2 2026-11-10 (patch 0004).
- **H-06/H-07**: ditunda (lihat bagian di bawah).

## Tabel penutupan

Lokasi mengacu ke baris di commit `281a118`. "Gagal sebelum" artinya tes dijalankan terhadap ekspor bersih `1565124` di luar worktree (`%TEMP%\nn2-host-fix2\base`) dengan file tes baru disalin ke sana.

| ID | Perubahan | Lokasi | Tes (gagal sebelum → lulus sesudah) |
|---|---|---|---|
| H-01 (Major) | `profile_attribution()` menentukan basis atribusi: `owner` (session yang dikirim = session di balasan ≠ 0, `attributed=1`); `foreign` (balasan menyebut session lain); `detached` (session 0 setelah run selesai; id run hanya dicatat sebagai `candidate_session`); `no_session` (kasus lain). `_profile_query` menghitung session yang dikirim dari state yang dimiliki dan memakai boot id dari balasan. Balasan `foreign` ditulis ke event `profile_device` tanpa `profile_codes`. `start()`/`connect()` mengosongkan `_session_id` lama. | `control/profile.py:343` (`profile_attribution`), `:369` (`profile_event_text`); `engineering.py:341`, `:476`, `:489`, `:509`, `:818` (`_profile_state_view`) | `test_h01_a_new_owner_between_terminal_status_and_get_profile_is_not_attributed_to_the_run` (owner baru, session 202, muncul di antara STATUS terminal dan GET_PROFILE), `test_h01_a_detached_end_read_is_unattributed_and_names_the_run_only_as_a_candidate`, `test_h01_the_next_runs_pre_start_read_never_carries_the_previous_session_id`, `test_h01_attribution_rules` ×8, `test_h01_the_worst_case_event_keeps_identity_integrity_and_both_hashes_inside_the_limit` — 12/12 gagal sebelum |
| H-02 (Major) | Parser mencatat setiap kemunculan `stop_evidence/stop_integrity/stop_match/stop_error/stop_slot` (`raw_integrity`). Kunci duplikat (urutan apa pun, termasuk nilai yang sama) menghasilkan issue `duplicate:<key>`, peringatan, dan `UNVERIFIED`. `VALID_MATCH` tanpa `stop_match=1` atau dengan `stop_error≠0` juga `UNVERIFIED`. Event menulis `stop_match`, `integrity_issues`, dan `raw_integrity`. Kalau baris terlalu panjang, hash dan nilai mentah pindah utuh ke event `profile_ids`; tidak ada yang terpotong. | `control/profile.py:64`, `:131`, `:135` (`stop_integrity_effective`), `:208`, `:255`, `:439`, `:448`, `:491` | `test_h02_a_duplicated_integrity_key_is_unverified_in_either_order` ×15 (5 kunci × positif-dulu/negatif-dulu/identik), `test_h02_a_duplicate_at_full_identity_is_never_cut_and_keeps_hashes_and_raw_values` ×5, `test_h02_valid_match_needs_ok_match_1_and_error_0_as_fix2_emits` ×8, `test_h02_the_fix2_valid_match_reply_is_still_ok_and_a_mismatch_still_blocked` — 29/29 gagal sebelum. KONTROL (lulus juga sebelum): `test_h02_contradictory_evidence_and_integrity_never_give_ok` ×5 |
| H-03 (Minor) | `SmoothScrollArea`: `actionTriggered` → `_bar_action` (single step = stop berikut/sebelumnya; page step = stop terjauh di dalam halaman yang diusulkan, minimal stop berikutnya; memakai `setSliderPosition` sebelum Qt mengubah nilai). `sliderReleased` → `snap_to_nearest`. `snap_stops()` = stop baris yang dijepit ke rentang bar + min/max. | `noctinirs_host_ui/widgets.py:322`, `:323`, `:334`, `:346`, `:373` | Di 900×600 (MIN_WINDOW), normal dan lost-START: `test_h03_single_steps_visit_every_row_stop_and_cross_the_divider` ×2, `test_h03_page_steps_settle_on_stops` ×2, `test_h03_keyboard_on_the_scroll_bar_and_the_area_settles_on_stops` ×4 (Down/PageDown pada bar dan area), `test_h03_a_slider_drag_ends_on_the_nearest_stop_including_across_the_divider` ×2 — 10/10 gagal sebelum (contoh di base: berhenti di 48 px, di antara CH2 dan CH3). KONTROL: `test_h03_without_stops_the_scroll_bar_keeps_its_plain_steps` |
| H-04 (Minor) | Pengecualian B-01 (`session_gone`) dipersempit menjadi: balasan session 0, state terminal (STOPPED/FAULTED/SAFE_OFF), dan opcode STATUS/INFO/GET_CONFIG (`SESSION_GONE_OPS`). Balasan OK lain yang tidak cocok dengan session yang dipegang ditolak. Fake di tes meniru FIX2: request dengan session basi dijawab `INVALID_STATE` dengan session 0. | `control/session.py:46-47`, `:329` | `test_h04_fix2_refusal_in_the_engineering_workflow_records_one_end_read_and_keeps_the_link`, `test_h04_ok_replies_that_cannot_belong_to_the_held_session_are_refused` untuk RECOVERING/BOOT session 0, HEARTBEAT, GET_TIME (4 dari 7) — 5 gagal sebelum. KONTROL: 3 sisanya (session asing 202; STREAMING/STOPPING session 0), `test_h04_fix2_stale_session_status_after_a_refused_start_is_invalid_state_with_session_0`, `test_h04_the_compatibility_terminal_ok_path_stays_for_observation_commands` ×9, `test_h04_no_reply_is_a_timeout_and_keeps_the_held_session` |
| H-05 (Minor) | `safe_off()` menulis event `safe_off` (ACK: `reply_session`, `reply_state`, `boot`) lalu menjadwalkan satu get-profile setelah `SAFE_OFF_PROFILE_DELAY_S=1.0` s (batas `SAFE_OFF_PROFILE_LIMIT_S=3.0` s). `_deferred_follow()` dipanggil dari `tick()`. Paling banyak satu kirim; kalau tidak terbaca, ditulis `phase=after_safe_off not read: <alasan>` dan rekaman tetap difinalisasi. Kalau link ditutup lebih dulu, ditulis `link closed first`. Kalau START ACK tidak pernah diterima, ditulis `phase=end_missing ... end_evidence=MISSING`. | `engineering.py:36`, `:355`, `:418`/`:437` (`safe_off`), `:527` (`_deferred_follow`), `:675`, `:772` | `test_h05_safe_off_ack_is_recorded_and_the_one_end_read_is_deferred_until_after_cleanup` (fake menulis bukti 0,5 s setelah ACK; host membaca tepat 1× setelah ≥1 s dan mendapat `SKIPPED_EMERGENCY`), `test_h05_a_silent_deferred_read_is_one_bounded_send_then_the_recording_finalizes`, `test_h05_disconnect_before_the_deferred_read_records_that_it_was_not_read`, `test_h05_a_lost_start_ack_records_an_explicit_missing_end_event` — 4/4 gagal sebelum |
| H-08 (Nit) | Header `profile.py` mengutip "local amendment (Stage 1 calibration FIX2) 2026-11-10" (patch 0004, status tetap Proposed), plus paragraf Lease/identitas dan Integritas. Docstring `query_profile`: GET_PROFILE owner yang sukses ikut me-refresh lease di firmware (session 0 tidak), host tidak menganggapnya heartbeat, dan session di balasan bersifat device-wide sehingga tidak pernah diadopsi. Komentar `session_gone` menjelaskan `INVALID_STATE` FIX2. | `control/profile.py:14-17`; `control/session.py:329`, `:736` | dokumentasi; dicek dengan membaca `nn2_app_comm.c` FIX2 (baris 559-562: `INVALID_STATE` untuk session basi; 573-576: refresh anchor oleh owner) |

Catatan fail-before: beberapa tes gagal di base karena fungsi atau atribut baru belum ada, bukan karena perilaku yang diuji:
- unit `profile_attribution`;
- `test_h02_the_fix2_valid_match_reply_is_still_ok...` (atribut `integrity_issues`);
- `test_h04_fix2_refusal_in_the_engineering_workflow...` (format H-01);
- varian duplikat negatif-dulu (base sudah `UNVERIFIED`; yang baru adalah `integrity_issues`/`raw_integrity`).

Tes H-03 memakai helper `stops()` lokal di file tes, jadi kegagalannya di base memang perilaku (posisi berhenti), bukan atribut yang hilang.

## Ditunda: H-06 dan H-07

Sesuai brief, H-06 dan H-07 ditunda. Tidak ada kode yang diubah untuknya, dan tidak ada tes yang ditambahkan. Teks review Codex untuk H-06/H-07 tidak ada di worktree ini, jadi isinya tidak dirangkum di sini. Keduanya tetap terbuka untuk putaran berikutnya.

Batas scope yang juga dijaga: tidak ada wizard, tidak ada set-profile/clear-profile otomatis, tidak ada tombol CLEAR_FAULT, dan `control_panel.py` tidak dirombak.

## Perubahan format event sidecar

Urutan kepala baris `profile`: `phase`, `attributed`, `basis`, `reply_session`, `reply_state`, `candidate_session` (hanya untuk detached), `boot`, `session` (hanya kalau attributed), `plan_epoch`. Setelah itu identitas profil dan integritas: `supported`, `state`, `armed`, `id`, `source`, `start_result`, `mismatch_slot`, `applied_id`, `applied_valid`, `stop_evidence`, `stop_integrity` (nilai efektif versi host), `stop_match` (baru), `stop_slot`, `stop_error`. Lalu `integrity_issues` dan `raw_integrity` (hanya kalau ada masalah). Hash ditaruh paling akhir.

Format lama `session=<id run>` di setiap baris (termasuk pre_start dan ended) tidak dipakai lagi. Pembaca sidecar harus memakai `attributed`/`basis`.

Contoh berikut dihasilkan oleh kode ini (fake firmware, offscreen):

Running, milik run (`basis=owner`):
```
phase=running attributed=1 basis=owner reply_session=1 reply_state=STREAMING boot=0x51510001 session=1 supported=1 state=NONE armed=0 id=0x00000000 source=DEFAULT start_result=OK mismatch_slot=-1 applied_id=0x00000000 applied_valid=1 stop_evidence=NONE stop_integrity=UNVERIFIED stop_match=0 stop_slot=-1 stop_error=0 hash=none applied_hash=none
```

Pembacaan akhir setelah STOP (session 0, tidak diatribusikan; id run hanya kandidat):
```
phase=ended attributed=0 basis=detached reply_session=0 reply_state=STOPPED candidate_session=1 boot=0x51510001 supported=1 ... stop_evidence=VALID_MATCH stop_integrity=OK stop_match=1 stop_slot=-1 stop_error=0 hash=none applied_hash=none
```

Owner baru muncul sebelum pembacaan akhir (event `profile_device`, bukan `profile`; tanpa `session=`, tanpa `profile_codes`):
```
phase=ended attributed=0 basis=foreign reply_session=202 reply_state=STREAMING boot=0x51510001 supported=1 ... stop_evidence=VALID_MATCH stop_integrity=OK stop_match=1 ...
```

Pre-start (tidak membawa session run sebelumnya):
```
phase=pre_start attributed=0 basis=no_session reply_session=0 reply_state=STOPPED boot=0x51510001 supported=1 ...
```

SAFE_OFF (event `safe_off` baru, lalu satu pembacaan tertunda):
```
[safe_off] SAFE_OFF ACK reply_session=0 reply_state=SAFE_OFF boot=0x51510001; STOP evidence is recorded by the firmware after its cleanup; one get-profile read deferred 1 s
[profile]  phase=after_safe_off attributed=0 basis=detached reply_session=0 reply_state=SAFE_OFF candidate_session=1 boot=0x51510001 supported=1 ... stop_evidence=SKIPPED_EMERGENCY stop_integrity=UNVERIFIED stop_match=0 ...
```

Kalau pembacaan tertunda tidak berhasil:
- `phase=after_safe_off not read: get-profile TIMEOUT: no usable reply`
- `phase=after_safe_off not read: link closed first`
- `phase=after_safe_off not read: no live link`
- `phase=after_safe_off not read: not allowed within 3 s of SAFE_OFF`

START ACK hilang:
```
phase=end_missing attributed=0 basis=start_unresolved end_evidence=MISSING reason=START ownership was never established (no START ACK); no end get-profile read belongs to this recording
```

Integritas duplikat. Contoh ini dari tes: identitas 64-bit penuh dan hash penuh, sehingga hash dan nilai mentah pindah ke `profile_ids`:
```
[profile]     phase=ended attributed=0 basis=detached reply_session=0 reply_state=STOPPED candidate_session=1 boot=0x1A2B supported=1 state=APPLIED armed=1 id=0xA1B2C3D4 ... stop_evidence=VALID_MATCH stop_integrity=UNVERIFIED stop_match=1 stop_slot=-1 stop_error=0 integrity_issues=duplicate:stop_integrity raw_integrity=in_profile_ids hashes=in_profile_ids warnings=2
[profile_ids] phase=ended hash=<64 hex> applied_hash=<64 hex> raw_integrity=stop_evidence:VALID_MATCH,stop_integrity:OK,stop_match:1,stop_slot:-1,stop_error:0,stop_integrity:BLOCKED
```
Kalau baris masih muat, `integrity_issues=...` dan `raw_integrity=key:value,...` langsung ada di baris `profile`.

Fallback panjang (batas 500 karakter; hash tidak pernah dipotong):
1. `hash=<hex> applied_hash=<hex>`
2. `hash=<hex> applied_hash=same` (kalau sama)
3. `hashes=in_profile_ids` + event `profile_ids`
4. `raw_integrity=in_profile_ids` juga dipindah

Kind event `<kind>_ids` mengikuti kind barisnya (`profile_ids` atau `profile_device_ids`).

Ringkasan di status area (UI) menambah satu kalimat atribusi per basis, mis. "Device-wide report read after session 1 ended (session 0): not bound to that run."

## Hasil tes

Semua di Windows, venv `venv-stageD-ui`, `PYTHONPATH=src;tests`, `QT_QPA_PLATFORM=offscreen`, `-p no:cacheprovider`.

| Run | Hasil |
|---|---|
| Tes baru (`tests/test_host_d4_fix2.py` 69 + `tests/ui/test_d4_fix2_scroll.py` 11) pada `281a118` | 80 passed |
| Tes baru yang sama pada ekspor bersih `1565124` (di luar worktree) | 60 failed, 20 passed (20 = KONTROL yang disebut di tabel) |
| Suite penuh pada `281a118` | 860 passed, 6 skipped, 0 failed (382,9 s) |
| Baseline suite penuh pada `1565124` (brief) | 780 passed, 6 skipped |

780 + 80 = 860, sesuai jumlah passed. 6 skipped adalah tes golden yang sama dengan baseline. Hasil yang sama (860 passed, 6 skipped) juga didapat di mesin Linux terpisah dengan blob file yang identik dengan commit `281a118`.

## Risiko yang tersisa

1. **Tidak ada yang diverifikasi di perangkat.** Semua perilaku firmware (INVALID_STATE untuk session basi, session balasan = `c.session`, bukti STOP yang ditulis async setelah SAFE_OFF) dimodelkan di fake berdasarkan bacaan sumber FIX2, bukan diukur.
2. **Jeda 1,0 s setelah SAFE_OFF adalah asumsi.** Nilainya diambil dari `CONTROL_STOP_EVIDENCE_DEADLINE` (1 s) plus quiesce 100 ms. Kalau di bench worker butuh lebih lama, pembacaan bisa masih melihat bukti lama. Perlu diukur di bench; konstanta ada di `engineering.py:36`.
3. **Bukti akhir selalu tidak terikat ke session.** Firmware tidak mengirim kunci yang mengikat laporan get-profile ke session yang berakhir (setelah STOP, balasan memakai session 0). Karena itu pembacaan `ended`/`after_safe_off` selalu `attributed=0 basis=detached`, dan host tidak bisa membuktikan bahwa bukti itu milik run ini, bukan run owner lain yang singkat di antaranya. Saran untuk firmware: tambahkan kunci seperti `stop_session=<id>` (dan/atau `stop_boot`) di profile_text.
4. **Pembatasan B-01 mengubah perilaku.** Balasan OK dengan session 0 untuk opcode selain STATUS/INFO/GET_CONFIG (mis. HEARTBEAT) atau di state non-terminal sekarang ditolak. Akibatnya bisa timeout atau link hilang (fail-closed), padahal sebelumnya diterima. Firmware FIX2 seharusnya tidak pernah mengirim balasan seperti itu, tetapi firmware lama (pra-FIX2) belum dicek ulang di perangkat.
5. **Snap scroll hanya diuji offscreen** pada 900×600 (normal dan lost-START). Perilaku di platform/DPI nyata, wheel touchpad, dan drag dengan mouse fisik belum diamati.
6. **Pembaca sidecar.** Siapa pun yang mem-parsing `session=` dari event `profile` lama harus pindah ke `attributed`/`basis`, dan juga membaca `profile_device`/`profile_ids`.
