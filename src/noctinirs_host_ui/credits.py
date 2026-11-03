"""Third-party credits shown in the About dialog (pure Python, no Qt import).

Keep this list in step with the components that are really bundled; the build
script writes the authoritative inventory and license texts into the release
folder (ADR-0001 rules 4 and 7).
"""

from __future__ import annotations

from dataclasses import dataclass

APP_NAME = "NoctiNIRS 2 Host"
BANNER_TEXT = "NoctiNIRS 2 Host - research prototype, non-clinical"
SYNTHETIC_NOTICE = "SYNTHETIC DATA - no device connected"
NON_CLINICAL_NOTICE = (
    "Research prototype. Not a medical device. Not for diagnosis, monitoring "
    "or any clinical decision."
)


@dataclass(frozen=True)
class Credit:
    name: str
    license_name: str
    note: str = ""


CREDITS: tuple[Credit, ...] = (
    Credit(
        "Qt 6 / PySide6 (Qt for Python)",
        "LGPL-3.0",
        "Used unmodified, as separate shared libraries that you may replace. "
        "Source: https://download.qt.io/ and https://code.qt.io/cgit/pyside/pyside-setup.git/",
    ),
    Credit("pyqtgraph", "MIT"),
    Credit("NumPy", "BSD-3-Clause"),
    Credit("colorama", "BSD-3-Clause"),
    Credit("Python runtime", "PSF License"),
)


def about_text(app_version: str, qt_version: str, pyside_version: str) -> str:
    """Plain-text body of the About dialog."""
    lines = [
        f"{APP_NAME} {app_version}",
        NON_CLINICAL_NOTICE,
        "",
        f"Qt {qt_version}, PySide6 {pyside_version}",
        "",
        "Built with free and open-source software:",
    ]
    for c in CREDITS:
        entry = f"- {c.name} ({c.license_name})"
        if c.note:
            entry += f". {c.note}"
        lines.append(entry)
    lines += [
        "",
        "Full license texts: THIRD_PARTY_NOTICES.txt in the application folder.",
    ]
    return "\n".join(lines)
