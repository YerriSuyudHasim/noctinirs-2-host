# noctinirs-2-host

PC host application for the NoctiNIRS 2 fNIRS device.

Status: **step 1** (geometry loader, wire parsers, gate recheck). No device I/O, no UI. Governance rules for contributors and AI agents are in [AGENTS.md](AGENTS.md); the project record is [project.yaml](project.yaml).

- Firmware and the device protocol: [YerriSuyudHasim/noctinirs-2](https://github.com/YerriSuyudHasim/noctinirs-2)
- Language: Python. Reference version pinned to **3.12.7** (CI and `.python-version`); the code is also tested locally on 3.13. `requires-python = ">=3.11"` is the lowest allowed version. UI framework to be decided in host ADR-0001.
- Non-clinical research prototype.

## Evidence status

All code is tested only with self-made data (simulation/unit class). It is **not verified against the firmware C implementation**; the shared golden vectors do not exist yet. Reference pins and hashes: [config/reference_pins.json](config/reference_pins.json).

## Run on Windows (PowerShell)

```powershell
git clone https://github.com/YerriSuyudHasim/noctinirs-2-host.git
cd noctinirs-2-host
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -m noctinirs_host check-geometry config\optical_geometry_v1.draft.json
```

`config/optical_geometry_v1.draft.json` is a byte-identical copy of the firmware draft geometry (sha256 in the pins file). It is replaced when the official geometry file is merged in the firmware repository.
