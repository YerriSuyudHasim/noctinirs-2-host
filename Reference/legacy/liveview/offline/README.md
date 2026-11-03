# NoctiNIRS fNIRS Validation Live View


## New white/blue UI prototype

The dashboard now includes a welcome/setup workflow and a 16-channel bilateral ECG-style sweep display. See `UI_PROTOTYPE.md` for the current interface and processing behavior.

Dashboard ini dibuat khusus untuk firmware `app_main_fnirs_test.c`.

## Arsitektur

```text
ESP32-S3 NoctiNIRS
  UDP control 192.168.4.1:3333
  UDP stream  -> laptop:3334
              |
              v
bridge.js (Node.js)
  UDP 3334 <-> WebSocket 8080
  optional static HTTP 8000
              |
              v
public/index.html + app.js
```

Browser tidak dapat membuka UDP socket secara langsung. Karena itu `bridge.js` wajib tetap berjalan walaupun halaman dibuka menggunakan VS Code Live Server.

## Persyaratan

- Node.js 18 atau lebih baru
- Laptop tersambung ke Wi-Fi NoctiNIRS:
  - SSID: `NOCTINIRS`
  - Password: `<DEVICE_WIFI_PASSWORD>`
- Firmware fNIRS test sudah berjalan pada ESP32-S3

## Menjalankan dengan server bawaan

```bash
npm install
npm start
```

Buka:

```text
http://localhost:8000
```

## Menjalankan dengan VS Code Live Server

1. Jalankan bridge:

```bash
npm install
npm start
```

2. Di VS Code, buka folder `public`.
3. Klik kanan `index.html` lalu pilih **Open with Live Server**.
4. Halaman Live Server tetap terhubung ke `ws://localhost:8080`.

## Urutan validasi yang disarankan

1. Hubungkan laptop ke SSID `NOCTINIRS`.
2. Buka dashboard dan pastikan WebSocket **Connected**.
3. Tekan **Connect device** sampai status Device menjadi **Online**.
4. Mulai dari mode `DARK`, arus 10 mA, lalu tekan **Start**.
5. Periksa noise semua PD dan quality flag.
6. Tekan **Stop**.
7. Pilih `SINGLE`, source dan wavelength, kemudian **Start**.
8. Setelah mapping individual valid, gunakan `SCAN`.

Perubahan mode, slot, dan current hanya dapat dilakukan saat acquisition berhenti.

## Command firmware yang digunakan

```text
HELLO
INFO
STATUS
REGS
MODE DARK
MODE SINGLE
MODE SCAN
SLOT 0..19
CURRENT 735 0..20
CURRENT 850 0..20
START
STOP
```

## Pengujian tanpa device

Terminal 1:

```bash
npm run start:local-device
```

Terminal 2:

```bash
npm run simulate
```

Kemudian buka `http://localhost:8000`. Simulator mendukung command, mode DARK/SINGLE/SCAN, dan stream 10 Hz sehingga seluruh pipeline dapat diuji sebelum memakai hardware.

## Tampilan dan data

- Grafik live empat photodetector untuk slot terpilih
- Raw counts atau delta terhadap reference
- Tabel 20 slot dengan `n_used`, valid, under-K, dan CRC-drop
- Frame rate, packet loss, outer UDP CRC, dan stream CRC
- Session, frame ID, current configuration, dan control log
- Perekaman CSV untuk slot yang sedang dipilih

CSV merekam satu baris per frame untuk slot terpilih dan memiliki batas keselamatan 100.000 baris.

## Port default

| Fungsi | Port |
|---|---:|
| Device UDP control | 3333 |
| Laptop UDP stream/reply | 3334 |
| WebSocket | 8080 |
| HTTP static server | 8000 |

## Opsi bridge

```bash
node bridge.js --device 192.168.4.1 --control-port 3333 --stream-port 3334 --ws-port 8080 --http-port 8000
```

Device IP juga dapat diubah langsung dari dashboard.

## Processing library

Geometry, MBLL, and selectable 5-mm/8-mm/both short-separation modules are documented in `PROCESSING_LIBRARY.md`.
