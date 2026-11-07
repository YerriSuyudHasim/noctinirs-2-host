# noctinirs-2-host

PC host application for the NoctiNIRS 2 fNIRS device.

Status: **step 1** (geometry loader, wire parsers, gate recheck) the **S0 UI spike** (window, packaging test) and the **S1 simulated control** (connect, start/stop, Safe Off, Fs, LED current). No device I/O: the app talks only to a built-in simulator and shows demo data. Governance rules for contributors and AI agents are in [AGENTS.md](AGENTS.md); the project record is [project.yaml](project.yaml).

- Firmware and the device protocol: [YerriSuyudHasim/noctinirs-2](https://github.com/YerriSuyudHasim/noctinirs-2)
- Language: Python. Reference version pinned to **3.12.7** (CI and `.python-version`); the code is also tested locally on 3.13. `requires-python = ">=3.11"` is the lowest allowed version. UI framework: PySide6 (desktop), see [ADR-0001](docs/adr/0001-ui-framework.md); no UI code yet.
- Non-clinical research prototype.

## Evidence status

Host/unit class. The parsers and the gate check pass the shared golden vectors of the firmware repository (61 frame vectors, 26 gate vectors, pinned by sha256 in [config/reference_pins.json](config/reference_pins.json)). The C code itself is **not run** here, and nothing is tested on a device. The vectors are not copied into this repository: CI downloads them from the pinned firmware commit. To run them locally set `NN2_GOLDEN_DIR` to a folder with the four files; without it those tests are skipped.

## Run on Windows (PowerShell)

```powershell
git clone https://github.com/YerriSuyudHasim/noctinirs-2-host.git
cd noctinirs-2-host
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -m noctinirs_host check-geometry config\optical_geometry_v1.json
```

`config/optical_geometry_v1.json` is a byte-identical copy of the official firmware file `config/optical_geometry_v1.json` (sha256 `035c00f1...39ff`, pinned in `config/reference_pins.json`). Its status is still DRAFT until the Owner has checked the distances with calipers. The older draft geometry (`ddc2a094...`) is not used anymore.

## Offline stage B: core UDP/session API

The core now supports an explicit IPv4 UDP endpoint (port 3333 by default).
Wi-Fi connection is manual. The desktop still uses only the synthetic simulator;
real-device UI integration is outside this change. Control targets firmware
`bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, `firmware/v4-idf61`, separately from the
unchanged optical/golden pins. See [local review notes](docs/local-review/pr-b/status.md).

API outline below uses a **localhost fake peer**, not a device. Do not execute
against hardware without a separately approved bench procedure.

```python
from noctinirs_host.control.session import SessionController
from noctinirs_host.control.udp import UDPTransport

controller = SessionController(UDPTransport("127.0.0.1", 3333))
try:
    result = controller.connect()  # INFO/GET_CONFIG only; never starts a scan
    if result.ok:
        allowed = controller.settings.fs_allowed  # actual INFO list, no range fallback
        result = controller.set_mode("ENGINEERING")  # STOPPED only
        # START is a separate explicit controller.start() call.
        # Schedule controller.tick() regularly to receive streams, poll state
        # and send owner heartbeats. STARTING/STOPPING ACKs are not completion.
        # controller.raw_sink = callable_accepting_original_datagram_bytes
finally:
    controller.disconnect()  # closes socket; no implicit START or STOP
```

Keep commands/tick on one calling thread. Missing or invalid `fs_allowed` disables
Fs changes. The host supports OPT2 from simulation/replay. Tests use fake peers
on loopback.

## Offline stage C: RAW capture and replay

The core recorder can opt into RAW layout 3. It preserves the original
datagrams and host receive times in a binary archive and exports a separate
sample CSV for inspection. RAW is always ENGINEERING/non-science. See the
[format, integration and replay API](docs/raw-recording-format.md).

## Desktop UI spike (S0, issue #22)

The UI is a separate package, `noctinirs_host_ui`, built with PySide6 and pyqtgraph (decision: [ADR-0001](docs/adr/0001-ui-framework.md)). The core never imports it. The `ui` dependency group needs **Python 3.12 or newer** (exact pins are in `pyproject.toml`).

```powershell
python -m pip install -e ".[dev,ui]"
python -m noctinirs_host_ui
```

The window shows a banner, a control panel for a **built-in simulated device** (Connect, Start, Stop, SAFE OFF, sampling rate, LED current per channel, event log), a demo signal (not device data) and an About dialog with third-party credits. A real device cannot be connected in this version.

Build a standalone Windows folder app (PyInstaller, onedir) and a size and license report:

```powershell
python -m pip install -e ".[ui,build]"
python packaging\build.py
```

Result: `dist\NoctiNIRS-Host\NoctiNIRS-Host.exe` (plus `THIRD_PARTY_NOTICES.txt` in the same folder), the download zip, and `dist\build-report.md`. The GitHub Actions workflow `build-ui-windows` does the same on a Windows runner and uploads the zip as an artifact.
