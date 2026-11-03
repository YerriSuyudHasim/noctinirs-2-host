# ADR-0001: Desktop UI framework — PySide6

- Status: **Accepted by the Owner** (decision stated in chat on 2026-11-03); takes effect when this PR is merged.
- Deciders: Owner (YerriSuyudHasim). Drafted by Claude (full-stack/UI developer role, hub AGENTS.md section 5.1).
- Evidence class of this document: static (documentation only). Nothing here was run on a device. License facts were read from the package pages listed under "Sources" on 2026-11-03 and must be re-checked when versions are pinned. This is not legal advice.

## Context

The host is meant to be a standalone application that students and other users open on their own laptop, connect to the device, configure it, control it, record data, and process it. Constraints stated by the Owner:

- Standalone; no Python installation needed by the end user.
- Distributed size: at most about 100-200 MB. Start-up time, CPU and RAM are not priorities.
- Free to use, open source, and distributable to the public (including a possible beta release by a future startup). Attribution to developers and libraries is acceptable.
- Avoid paid features and complicated licensing; avoid legal problems as far as reasonably possible.

`AGENTS.md` already requires a UI-independent core (codec, UDP transport, session control, recording, signal processing). This ADR chooses only the UI layer.

## Decision

Use **PySide6** (Qt for Python) for the desktop UI, with **pyqtgraph** for real-time plots. Package the application with **PyInstaller in folder (onedir) mode**, preferably wrapped in an installer. The packaging choice is intended, not final; the first release PR confirms it by measurement.

The UI is a separate layer that depends on the core. The core never imports the UI and keeps zero runtime dependencies.

## Licensing rules for this decision

These rules are part of the decision. They limit which Qt parts may be used.

1. Depend on **PySide6-Essentials only**. Do not install the `PySide6` meta-package or **PySide6-Addons**, and do not use QtWebEngine. Reason: Essentials covers what the host needs (QtCore, QtGui, QtWidgets, and related modules). The Addons package holds many extra modules (for example Qt Charts and Qt Data Visualization), and its PyPI page does not make clear which licensing terms apply per module. Avoiding it removes that doubt.
2. Plots use pyqtgraph (MIT), not Qt Charts.
3. PySide6 is offered by its publisher under "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only" (or a commercial license). This project uses it under **LGPLv3**: Qt is used unmodified and as separate shared libraries, which is why folder (onedir) packaging is preferred over a single-file executable. These measures are intended to support LGPL compliance; they do not guarantee it. Compliance is checked on the actual release artifact (see rule 7).
4. Every release ships a `THIRD_PARTY_NOTICES` file with the name, version, license and license text of each bundled component, and the application's About dialog credits Qt/PySide6 and pyqtgraph. The notice states that Qt is unmodified and where its source can be obtained, and that users may replace the Qt libraries.
5. Every new dependency still needs purpose, license and version pin in its PR (see `AGENTS.md`). Dependencies with GPL/AGPL-only or commercial-only terms are not accepted without a new ADR.
6. PyInstaller may be used for building: its license page states that the executables it produces can carry any license, subject to the licenses of the dependencies.
7. Before each public release, the release PR records a license check of the built artifact: the actual list of bundled components (Qt libraries, Python runtime, NumPy and any other packages PyInstaller collected), each component's license, and whether the LGPL obligations for Qt are met for that exact bundle. The PyInstaller exception is conditional on the dependency licenses, so a component with incompatible terms blocks the release until resolved.

## Consequences

- Positive: a mature instrument-grade UI toolkit, one standalone application, and a license path that is free for public distribution when the rules above are followed.
- Negative: large package (Qt bundled). Target is at most about 100-200 MB; the first release PR must measure the real size, and heavy scientific libraries (for example SciPy) must not be added without checking the budget.
- Negative: single-file executables and antivirus false positives. Prefer folder packaging, publish SHA-256 checksums with releases.
- pyqtgraph's own sources disagree on version requirements (read 2026-11-03): the PyPI metadata of release 0.14.0 states Python >= 3.10; the development branch `pyproject.toml` states Python >= 3.12 and NumPy >= 2.2.0; its README text still says NumPy 2.0+. Treat the development-branch figures as the stricter, forward-looking policy and the PyPI metadata as what the published release declares. This repository currently allows Python 3.11 (`requires-python = ">=3.11"`) while pinning 3.12.7 for CI. The first UI PR must pin an exact pyqtgraph release, verify its declared requirements against it, and, if needed, raise the minimum Python version.
- A web UI (for example FastAPI with a browser front end) remains possible later because the core is UI-independent; it would need its own ADR.

## Not decided here

- The license of this repository's own code. There is no `LICENSE` file yet; the Owner decides this before any public release.
- The supported operating systems for the first release (Windows is assumed for testing; other systems are not decided).
- The UI layout, screen design and package name of the UI layer.
- Signal-processing dependencies (NumPy and others), which need their own PR with license and size check.
- Whether to sign executables.

## Sources (read 2026-11-03)

- PySide6 on PyPI: https://pypi.org/project/PySide6/
- PySide6-Essentials on PyPI: https://pypi.org/project/PySide6-Essentials/
- PySide6-Addons on PyPI: https://pypi.org/project/PySide6-Addons/
- pyqtgraph on PyPI (license MIT) and GitHub (README and `pyproject.toml`): https://pypi.org/project/pyqtgraph/ , https://github.com/pyqtgraph/pyqtgraph
- PyInstaller license page: https://pyinstaller.org/en/stable/license.html
