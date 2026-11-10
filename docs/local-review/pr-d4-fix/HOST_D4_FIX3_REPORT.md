# Laporan perbaikan host D4 FIX3 (re-review H-02a, H-01a, H-05a, H-08a)

**Tanggal** 10 November 2026 (Asia/Seoul). **Kelas bukti** host/unit dan simulasi in-process (EngineeringFake). Tidak ada akses perangkat, build firmware, push, atau request GitHub.

- Worktree `C:\b5a-ads\host-d4fix`, branch `feat/20-host-d4-fix-offline`
- Base `780031914aca02c0a1de2bbadbd28f5eedc19e51` (laporan FIX2) di atas `281a118` (kode FIX2)
- Commit kode `6642428` — `fix(20): host re-review H-02a, H-01a, H-05a, H-08a ... (offline)`
- Input: `IR_CODEX_HOST_D4_FIX2_2026-11-10.md` (SHA-256 `8b43e2984394618465a2139b4671d1c3336edc09daa217434619a4dc2e298c76`), verdict FAIL 0C/1M/2m/1N
- Firmware hanya dibaca: `C:\b5a-ads\calib1` @ `9990008`, `components/nn2_app/src/nn2_app_comm.c:559-576`

## Penutupan temuan

| ID | Perbaikan | File:line (setelah `6642428`) | Tes (gagal di `7800319`, lulus sesudah) |
|---|---|---|---|
| H-02a (Major) | `profile_event_lines()` membangun baris utama dan event lanjutan `*_ids` sebagai satu hasil terstruktur (`ProfileEvent(main, ids)`). Engineering selalu menulis `main` lalu setiap `ids` tanpa memindai teks. Baris utama tidak pernah dipotong: field dipindah utuh ke `ids` dengan penanda (`hashes=`, `raw_integrity=`, `integrity_issues=`, `unknown_keys=in_profile_ids`), lalu bila masih panjang field identitas satu per satu (`fields=in_profile_ids`, urutan `_MOVABLE`). Identitas sesi/boot, `DATA_INTEGRITY_BLOCKED`, dan kelima field `stop_*` selalu tetap di baris utama. Event `ids` diberi nomor `ids=i/n`, masing-masing ≤ 500 karakter, token tidak pernah dibelah; daftar yang berlanjut mengulang prefiksnya. Parser menyimpan **semua** kemunculan kunci integritas (dibatasi MAX_TEXT 1412) dan nilai utuh sampai 400 karakter; nilai lebih panjang ditandai eksplisit `~len=N`. Nilai enum yang lebih dari 32 karakter (malformed) ditampilkan sebagai stub `~len=N` di baris utama. | `control/profile.py:98`, `:212`, `:381`, `:392`, `:439`, `:566`, `:579`; `engineering.py:538-545` | `test_h02a_the_recorder_path_always_writes_the_follow_on_events_with_both_hashes_and_all_raw_values` (jalur recorder, probe Codex 1173 B; di kode lama baris utama terpotong 500 karakter berakhiran `.`), `test_h02a_the_codex_probe_keeps_markers_both_hashes_and_all_ten_raw_values`, `test_h02a_a_long_ids_payload_is_split_into_bounded_events_and_never_cuts_a_hash_or_value` (16 kemunculan, ≥ 2 event ids), `test_h02a_a_raw_value_above_the_limit_is_shortened_with_an_explicit_marker_not_dropped`, `test_h02a_raw_integrity_values_longer_than_24_characters_are_kept_whole`, `test_h02a_the_widest_identity_keeps_the_stop_fields_and_markers_on_an_unsliced_main_line` ×8 |
| H-01a (Minor) | Sentinel `_FROM_RUN`: `candidate` yang tidak diberikan berarti id run; `None` eksplisit tidak pernah diganti `_session_id`. `arm()` memanggil `_reset_run_context()` yang mengosongkan `_session_id`, `_await_end/_await_running`, dan deferred read lama sebelum rekaman baru. | `engineering.py:40`, `:308`, `:329`, `:523` | `test_h01a_safe_off_on_a_new_unstarted_recording_never_names_the_previous_run` (run 1 selesai, rekaman 2 di-arm tanpa START, SAFE_OFF: `record_2.json` kini `basis=no_session` tanpa `candidate_session`) |
| H-05a (Minor) | Setiap ACK SAFE_OFF tetap dicatat; ACK berikutnya ditandai `repeated ACK: the first ACK's deferred get-profile schedule is kept` dan tidak menggeser `due`, `limit`, atau kandidat. Batas 3,0 s kini batas **selesai** (fail-closed): read hanya dimulai bila `now + PROFILE_TIMEOUT_S (0,4 s) <= limit`; jika tidak, event `phase=after_safe_off not read: could not finish within 3 s of the first SAFE_OFF ACK`. Tetap satu send, `retries=0`, guard heartbeat `query_profile` tidak diubah. | `engineering.py:31-39`, `:431-470`, `:563` | `test_h05a_a_second_safe_off_ack_keeps_the_first_due_time_limit_and_candidate`, `test_h05a_a_read_that_could_not_finish_by_the_limit_is_not_started`; kontrol `test_h05a_a_read_that_can_finish_by_the_limit_is_still_sent_once` (lulus juga di kode lama) |
| H-08a (Nit) | Docstring lease mengikuti firmware: guard hanya menolak session nonzero yang basi; GET_PROFILE dijawab ke semua peer; refresh terjadi bila request sukses **dan** dari endpoint owner (`c.owner == peer`), jadi session 0 dari endpoint owner bisa me-refresh lease, sedangkan peer asing dengan id aktif tidak. Host tetap tidak memajukan jadwal heartbeat-nya karena GET_PROFILE. Sitasi amandemen FIX2 2026-11-10 / patch 0004 tetap. | `control/profile.py:17-24`; `control/session.py:739-745` | dokumentasi saja |

## Perubahan format sidecar

- Event `profile_ids` / `profile_device_ids` sekarang `phase=P ids=i/n <token...>` (sebelumnya `phase=P hash=... applied_hash=... raw_integrity=...` satu baris). Bisa lebih dari satu event; pembaca menggabungkan nilai kunci yang sama sesuai urutan `ids`.
- Penanda baru `integrity_issues=in_profile_ids`, `unknown_keys=in_profile_ids`, `fields=in_profile_ids`. Penanda lama `hashes=` dan `raw_integrity=in_profile_ids` tetap.
- `raw_integrity` memuat semua kemunculan (sebelumnya maks 8 di sidecar, 16 di parser, nilai dipotong 24 karakter).
- Teks ACK SAFE_OFF kedua dan alasan "not read" untuk batas selesai berubah seperti di tabel.

Contoh probe Codex (identitas lebar penuh, dua hash berbeda, kelima kunci duplikat):

```
[profile 488]     phase=after_safe_off attributed=0 basis=detached reply_session=0 reply_state=SAFE_OFF candidate_session=4294967295 boot=0xFFFFFFFFFFFFFFFF plan_epoch=4294967295 supported=1 state=NOT_APPLIED armed=1 id=0xFFFFFFFF source=PROFILE start_result=PROFILE_NOT_APPLIED applied_id=0xFFFFFFFE stop_evidence=VALID_MATCH stop_integrity=UNVERIFIED stop_match=1 stop_slot=19 stop_error=-2147483648 integrity_issues=in_profile_ids raw_integrity=in_profile_ids hashes=in_profile_ids fields=in_profile_ids
[profile_ids 484] phase=after_safe_off ids=1/2 mismatch_slot=19 applied_valid=0 warnings=11 hash=abab…(64) applied_hash=cdcd…(64) integrity_issues=duplicate:stop_evidence,…,valid_match_with_stop_error raw_integrity=stop_evidence:VALID_MATCH,…,stop_error:-2147483648
[profile_ids 135] phase=after_safe_off ids=2/2 raw_integrity=stop_evidence:VALID_MATCH,stop_integrity:OK,stop_match:1,stop_slot:19,stop_error:-2147483648
```

## Tes

Python 3.13.5 dari `venv-stageD-ui`, `PYTHONPATH=src;tests`, `QT_QPA_PLATFORM=offscreen`, `-p no:cacheprovider`.

| Run | Hasil |
|---|---|
| Suite penuh di `6642428` (worktree) | **877 lulus, 6 dilewati (golden), 0 gagal**, 361,7 s (860 + 17 tes baru) |
| `test_host_d4_fix3.py` + `test_host_d4_fix2.py` + `ui/test_d4_fix2_scroll.py` + `test_profile.py` | 143 lulus |
| `test_host_d4_fix3.py` di ekspor bersih `7800319` (di luar worktree, `%USERPROFILE%\nn2-hostfix3-scratch\base7800319`) | **16 gagal, 1 lulus (kontrol)** |

Dari 16 kegagalan baseline, 6 gagal karena perilaku lama (baris utama terpotong, nilai mentah terpotong 24 karakter, `_session_id` lama setelah arm, jadwal bergeser, read dimulai terlalu dekat batas) dan 10 karena API baru (`profile_event_lines`, `RAW_VALUE_LIMIT`) belum ada di kode lama; perilaku yang diukurnya tetap salah di kode lama (dibuktikan tes jalur recorder).

## Risiko yang tersisa

- Pembacaan akhir tetap `attributed=0 basis=detached`; sidecar belum bukti kalibrasi per run sampai firmware punya identitas sesi STOP (mis. `stop_session=`).
- Jeda 1,0 s dan batas 3,0 s setelah SAFE_OFF belum diukur di bench.
- Batas selesai 3,0 s mengandalkan timeout transport 0,4 s; latensi thread/pemrosesan balasan sesudahnya tidak diukur.
- Nilai enum malformed > 32 karakter hanya muncul sebagai stub di sidecar (teks lengkap tetap di laporan parser / status area); nilai integritas > 400 karakter ditandai `~len=N`.
- Pembaca sidecar lain (bila ada) harus mengikuti format `ids=i/n` baru. H-06 dan H-07 tetap ditunda; tidak ada wizard, set/clear-profile otomatis, atau tombol CLEAR_FAULT baru.
