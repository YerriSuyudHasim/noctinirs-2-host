# Laporan perbaikan host D4 FIX4 (re-review FIX3: H-02b, H-02c, H-01b)

**Tanggal** 10 November 2026 (Asia/Seoul). **Kelas bukti** host/unit dan simulasi in-process (EngineeringFake). Tidak ada akses perangkat, build/flash firmware, push, merge, atau request GitHub.

- Worktree `C:\b5a-ads\host-d4fix`, branch `feat/20-host-d4-fix-offline`
- Base `8ef513f84057d188ed92c1b9dc3745f69f7a47b7` (laporan FIX3) di atas `66424282aff605e072560de53e7fc138c7a68173` (kode FIX3)
- Commit kode `86261080a1f424c26a1af2e377842d7fa7fd39ae` — `fix(20): host re-review FIX3 H-02b, H-02c, H-01b ... (offline)`
- Input: `IR_CODEX_HOST_D4_FIX3_2026-11-10.md` (SHA-256 `08445e65088af7a4f10b68bd773bc7faa2de19854a8d21f91058e1b66967a1b0`), verdict PASS WITH CHANGES 0C/0M/3m/0N

## Penutupan temuan

| ID | Perbaikan | File:line | Tes (gagal di `8ef513f`, lulus sesudah) |
|---|---|---|---|
| H-02b (Minor) | Pengodean dapat dibalik. Setiap kemunculan raw menjadi token sendiri `raw_integrity=<key>:<value>`, dan setiap issue menjadi `integrity_issues=<name>`. Ini berlaku di baris utama (inline) dan di event `ids` (jalur `prefix=None` di `_ids_lines`), tanpa join koma. Reader `raw_integrity_from_events()` mengembalikan tuple asli secara berurutan. | `control/profile.py:45-51` (aturan), `:516-517`, `:524-525`, `:545-547`, `:594-595`, `:605` | `test_h02b_the_two_colliding_codex_replies_give_different_sidecar_text_and_read_back_exactly`, `test_h02b_values_with_commas_and_colons_survive_the_overflow_follow_ons` |
| H-02c (Minor) | API baru `Recorder.event_group(events, incomplete=...)` memakai lock dan `RAW_EVENTS_LIMIT` (1024, tidak diubah) yang sama dengan `event()`, dan menyimpan grup secara all-or-nothing. Engineering menyimpan main, semua `*_ids`, dan `profile_codes` (bila ditulis) sebagai satu grup. Kalau grup tidak muat, tidak ada bagian grup yang ditulis, dan setiap event grup dihitung sebagai `event_drops`. Bila masih ada ≥ 1 slot, ditulis satu event `profile` berisi `phase=P incomplete=1 reason=event_limit needed=N available=M` tanpa penanda `in_profile_ids`. Bila tidak ada slot, penanda itu juga dihitung drop (+1). Log sesi mencatat `sidecar event limit reached; report group not stored`. `event()` untuk event tunggal (ACK SAFE_OFF dsb.) tidak berubah. | `record/writer.py:277`; `engineering.py:544-557` | `test_h02c_one_free_slot_stores_an_explicit_incomplete_marker_not_an_orphan_main_line`, `test_h02c_no_free_slot_stores_nothing_and_counts_the_group_and_the_marker_as_drops`, `test_h02c_the_recorder_group_api_is_all_or_nothing`; kontrol `test_h02c_with_room_the_whole_group_is_stored_contiguously` (lulus juga di kode lama) |
| H-01b (Minor) | `_reset_run_context()` (dipanggil dari `arm()`) juga mengosongkan `latest_raw`, yang menjadi sumber `plan_epoch`. Rekaman baru yang belum menerima RAW tidak lagi membawa epoch run sebelumnya. START tetap mengosongkannya lagi dan membuat `RawContinuity` baru, seperti sebelumnya. | `engineering.py:329-338` | `test_h01b_safe_off_on_a_new_unstarted_recording_has_no_plan_epoch_of_the_previous_run` (run 1 menulis `plan_epoch=1`; rekaman 2 tanpa START kini tidak punya `plan_epoch=`, tetap `basis=no_session` tanpa `candidate_session`) |

## Aturan pengodean sidecar (berubah dari FIX3)

- `raw_integrity=<key>:<value>`: satu token per kemunculan, urutan sesuai wire. `key` adalah nama INTEGRITY_KEYS, dan `value` adalah semua karakter setelah `:` pertama, sehingga koma, titik dua, dan `=` dalam nilai tetap utuh. Nilai wire tidak pernah memuat spasi (kontrak firmware; parser memisah di spasi). Nilai > 400 karakter tetap ditandai `~len=N` (FIX3).
- `integrity_issues=<name>`: satu token per issue.
- Penanda `raw_integrity=in_profile_ids` / `integrity_issues=in_profile_ids` tidak memuat `:` setelah nama key, jadi selalu bisa dibedakan dari token data.
- Token tidak pernah dipotong dan tidak pernah dibelah antar-event. Batas 500 karakter per event dan penomoran `ids=i/n` tetap.
- Akibatnya: karena token lebih panjang, nilai raw untuk satu duplikat di identitas biasa kini pindah ke `profile_ids`, tidak lagi inline. Kelengkapannya tidak berkurang.

Contoh tabrakan dari Codex (sekarang berbeda dan dapat dibaca balik):

```
A: ... integrity_issues=duplicate:stop_error raw_integrity=stop_error:x raw_integrity=stop_error:0,stop_error:1 raw_integrity=stop_error:2 ...
B: ... integrity_issues=duplicate:stop_error raw_integrity=stop_error:x raw_integrity=stop_error:0 raw_integrity=stop_error:1,stop_error:2 ...
```

## Tes yang diperbarui (format saja, kelengkapan tidak dilemahkan)

- `tests/test_host_d4_fix3.py`: helper `ids_values()` kini membaca satu token per nilai, tanpa split koma; penanda `in_profile_ids` dilewati. Pemeriksaan issue inline memakai helper yang sama. Semua assertion H-02a (dua hash 64-hex, sepuluh/enam belas kemunculan raw berurutan, ≤ 500, tanpa slice) tetap.
- `tests/test_host_d4_fix2.py::test_h02_a_duplicated_integrity_key_is_unverified_in_either_order`: sebelumnya menuntut nilai raw inline di baris utama. Sekarang tuple raw dibaca balik dari main + follow-on dan harus sama persis dengan `r.raw_integrity`, dan setiap event ≤ 500.

## Tes

Python 3.13.5 dari `venv-stageD-ui`, `PYTHONPATH=src;tests`, `QT_QPA_PLATFORM=offscreen`, `-p no:cacheprovider`.

| Run | Hasil |
|---|---|
| Suite penuh di `8626108` (worktree) | **884 lulus, 6 dilewati (golden), 0 gagal**, 393,3 s (877 + 7 tes baru) |
| `tests/test_host_d4_fix4.py` di ekspor bersih `8ef513f` (di luar worktree, `%USERPROFILE%\nn2-hostfix3-scratch\base8ef513f`) | **6 gagal, 1 lulus (kontrol)** |

Sebab kegagalan baseline: teks sidecar kedua input identik; `raw_integrity_from_events` belum ada; event terakhir adalah main yatim dengan `in_profile_ids` (bukan penanda incomplete); jumlah drop tidak memuat grup + penanda; `Recorder.event_group` belum ada; dan `latest_raw` lama masih ada setelah arm.

## Penyimpangan dari brief

- `unknown_keys=` tetap dijoin koma (maks 6 nama inline, semua nama di `ids` lewat prefiks). Nama ini diagnostik, bukan integritas. Tes lama `test_profile.py` (`unknown_keys=future_key,another`) dibiarkan. Nama key yang memuat koma tetap ambigu dan dicatat sebagai risiko.
- Event penanda incomplete sengaja tidak memuat `stop_integrity` agar tidak terbaca sebagai snapshot yang sah.
- Tidak ada counter metadata baru. Grup yang hilang terlihat dari event `incomplete=1` atau dari `raw.event_drops`.

## Risiko yang tersisa

- Sidecar masih bukan bukti kalibrasi per run: pembacaan akhir `attributed=0 basis=detached` sampai firmware punya identitas sesi STOP (mis. `stop_session=`).
- H-06 dan H-07 tetap ditunda. Tidak ada wizard, set/clear-profile otomatis, atau tombol CLEAR_FAULT baru.
- Jeda 1,0 s dan batas selesai 3,0 s setelah SAFE_OFF belum diukur di bench. Latensi thread dan pemrosesan balasan juga belum diukur.
- Bila slot event tinggal 0, snapshot hanya terlihat sebagai `event_drops`, tidak sebagai event.
- Jalur `unsupported` dan `profile_codes_event_text` masih memakai slice. Untuk input wire yang melewati parser, panjangnya terbatas (Codex: 220–271 karakter), jadi tidak ada hash atau raw yang terpotong.
- Pembaca sidecar lain (bila ada) harus mengikuti format token baru. Di repo ini tidak ada.
