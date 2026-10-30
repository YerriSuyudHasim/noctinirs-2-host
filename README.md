# noctinirs-2-host

PC host application for the NoctiNIRS 2 fNIRS device.

Status: **step 1** (geometry loader, wire parsers, gate recheck). No device I/O, no UI. Governance rules for contributors and AI agents are in [AGENTS.md](AGENTS.md); the project record is [project.yaml](project.yaml).

- Firmware and the device protocol: [YerriSuyudHasim/noctinirs-2](https://github.com/YerriSuyudHasim/noctinirs-2)
- Language: Python. Reference version pinned to **3.12.7** (CI and `.python-version`); the code is also tested locally on 3.13. `requires-python = ">=3.11"` is the lowest allowed version. UI framework to be decided in host ADR-0001.
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
