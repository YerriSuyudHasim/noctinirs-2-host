"""Build the standalone UI as a PyInstaller onedir bundle and write a report.

Usage (from the repository root, with ``pip install -e ".[ui,build]"``):

    python packaging/build.py

Outputs:
    dist/NoctiNIRS-Host/                  the application folder (shipped)
    dist/NoctiNIRS-Host/THIRD_PARTY_NOTICES.txt   notices + license texts (shipped)
    dist/NoctiNIRS-Host-<platform>.zip    the download
    dist/build-report.md                  size, origin breakdown, inventory (not shipped)
    dist/bundle-manifest.txt              every file in the bundle with its size (not shipped)

The build FAILS if the bundle contains a Qt Addons module or WebEngine file
(ADR-0001 licensing rule 1). Only the Python standard library is used besides
PyInstaller itself.
"""

from __future__ import annotations

import pathlib
import platform
import re
import subprocess
import sys
from importlib import metadata

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "NoctiNIRS-Host"
APP_DIR = DIST / APP_NAME
BUDGET_MB = 200

# Distributions that end up inside the bundle (runtime) vs. build-only tools.
BUNDLED = ("PySide6-Essentials", "shiboken6", "pyqtgraph", "numpy", "colorama")
BUILD_ONLY = ("pyinstaller", "pyinstaller-hooks-contrib")
LICENSE_FILE_HINTS = ("license", "licence", "copying", "notice", "authors")
# The PySide6/shiboken6 wheels carry a short notice about the *commercial* Qt
# license option. We do not use that option, so it is not reproduced.
SKIP_LICENSE_FILES = ("licenseref-qt-commercial",)

# Qt Addons modules and WebEngine: must not be bundled (ADR-0001 rule 1).
FORBIDDEN_QT_PARTS = (
    "3danimation", "3dcore", "3dextras", "3dinput", "3dlogic", "3drender",
    "axcontainer", "bluetooth", "canvaspainter", "charts", "datavisualization",
    "graphs", "httpserver", "location", "multimedia", "networkauth", "nfc",
    "positioning", "quick3d", "remoteobjects", "scxml", "sensors", "serialbus",
    "serialport", "spatialaudio", "statemachine", "texttospeech",
    "virtualkeyboard", "webchannel", "webengine", "websockets", "webview",
)

# Hints for native files that do not belong to a pip package. They are hints
# only (unverified); anything not matched is reported as UNCLASSIFIED and must
# be reviewed in the release-time license check (ADR-0001 rule 7).
NATIVE_HINTS = (
    ("python3", "Python runtime", "PSF-2.0"),
    ("libpython", "Python runtime", "PSF-2.0"),
    ("libqt6", "Qt 6 library (from PySide6)", "LGPL-3.0 (used under LGPL-3.0)"),
    ("qt6", "Qt 6 library (from PySide6)", "LGPL-3.0 (used under LGPL-3.0)"),
    ("libscipy_openblas", "OpenBLAS (shipped with NumPy)", "BSD-3-Clause"),
    ("base_library.zip", "Python standard library (frozen)", "PSF-2.0"),
    ("libcrypto", "OpenSSL", "Apache-2.0 for OpenSSL 3.x"),
    ("libssl", "OpenSSL", "Apache-2.0 for OpenSSL 3.x"),
    ("libffi", "libffi", "MIT"),
    ("ucrtbase", "Microsoft Universal C Runtime", "Microsoft redistributable terms"),
    ("api-ms-win", "Microsoft Universal C Runtime (API set)", "Microsoft redistributable terms"),
    ("vcruntime", "Microsoft Visual C++ runtime", "Microsoft redistributable terms"),
    ("msvcp", "Microsoft Visual C++ runtime", "Microsoft redistributable terms"),
)
PYTHON_EXTENSION_SUFFIXES = (".pyd", ".so")  # standard-library extension modules


def run_pyinstaller() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),
        str(ROOT / "packaging" / "entry_ui.py"),
    ]
    print("RUN:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


# ---------- pure helpers (unit-tested in tests/test_packaging_files.py) ----------

def find_forbidden(rel_paths: list[str]) -> list[str]:
    """Return bundle paths that look like a Qt Addons module or WebEngine file."""
    bad = []
    for p in rel_paths:
        base = re.split(r"[\\/]", p)[-1].lower()
        if "webengine" in base:
            bad.append(p)
            continue
        m = re.match(r"(?:lib)?qt6?(.*)", base)
        if m and any(m.group(1).startswith(part) for part in FORBIDDEN_QT_PARTS):
            bad.append(p)
    return bad


def classify_origin(rel_path: str) -> str:
    """Which component a bundle file belongs to (by location)."""
    parts = re.split(r"[\\/]", rel_path)
    if parts[0] != "_internal":
        return "application launcher and notices"
    inner = parts[1:]
    if len(inner) == 1:
        return "native/runtime file (no package)"
    t = inner[0].lower()
    for key, label in (("pyside6", "PySide6 (Qt)"), ("shiboken6", "shiboken6"), ("numpy", "NumPy"),
                       ("pyqtgraph", "pyqtgraph"), ("colorama", "colorama")):
        if t.startswith(key):
            return label
    return "other package or data: " + inner[0]


def native_hint(filename: str) -> tuple[str, str]:
    """(what it is, license hint) for a native/runtime file outside any package."""
    low = filename.lower()
    for prefix, what, lic in NATIVE_HINTS:
        if low.startswith(prefix):
            return what, lic + " (unverified hint)"
    if low.endswith(PYTHON_EXTENSION_SUFFIXES) and not low.startswith(("lib", "qt")):
        return "Python standard-library extension module", "PSF-2.0 (unverified hint)"
    return "UNCLASSIFIED", "UNCLASSIFIED - review needed"


# ---------- inventory ----------

def dist_info(name: str) -> dict:
    d = metadata.distribution(name)
    m = d.metadata
    lic = m.get("License-Expression") or m.get("License") or ""
    if not lic or len(lic) > 200:  # very long free-text License fields: use classifiers
        cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License")]
        lic = ", ".join(cls) or (lic[:120] + "...") or "UNKNOWN"
    texts = []
    for f in d.files or []:
        low = f.name.lower()
        if any(s in low for s in SKIP_LICENSE_FILES):
            continue
        if any(h in low for h in LICENSE_FILE_HINTS) and f.suffix.lower() in ("", ".txt", ".md", ".rst"):
            try:
                texts.append((str(f), pathlib.Path(f.locate()).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return {"name": m["Name"], "version": m["Version"], "license": lic, "texts": texts}


def list_bundle() -> list[tuple[str, int]]:
    files = [f for f in APP_DIR.rglob("*") if f.is_file()]
    return sorted((str(f.relative_to(APP_DIR)), f.stat().st_size) for f in files)


def write_manifest(files: list[tuple[str, int]]) -> pathlib.Path:
    out = DIST / "bundle-manifest.txt"
    out.write_text("\n".join(f"{size:>12}  {path}" for path, size in files) + "\n", encoding="utf-8")
    return out


def write_notices(infos: list[dict]) -> pathlib.Path:
    out = APP_DIR / "THIRD_PARTY_NOTICES.txt"
    lines = [
        "NoctiNIRS 2 Host - third-party notices",
        "",
        "PySide6 / Qt is offered by its publisher under LGPL-3.0, GPL-2.0, GPL-3.0 or a commercial",
        "license. This application uses it under the LGPL-3.0 (only). Qt is used unmodified, as",
        "separate shared libraries; you may replace them. The notice about the commercial license",
        "option that accompanies the PySide6 packages does not apply to this application.",
        "Qt source: https://download.qt.io/  PySide6 source: https://code.qt.io/cgit/pyside/pyside-setup.git/",
        "",
    ]
    for lic in ("LGPL-3.0-only", "GPL-3.0-only"):
        text = (ROOT / "packaging" / "licenses" / f"{lic}.txt").read_text(encoding="utf-8")
        lines += [
            "=" * 72,
            f"License text for Qt / PySide6 / shiboken6: {lic}"
            + ("  (the LGPL incorporates the GPL-3.0 terms)" if lic == "GPL-3.0-only" else ""),
            "=" * 72,
            text.strip(),
            "",
        ]
    lines += [
        "Note: Qt bundles further third-party components with their own licenses",
        "(see 'Licenses Used in Qt' in the Qt documentation), and this application also contains",
        "the Python runtime and native libraries. They are listed in the build report but their",
        "license texts are not reproduced here yet.",
        "",
    ]
    for i in infos:
        lines += ["=" * 72, f"{i['name']} {i['version']}  -  license: {i['license']}", "=" * 72]
        if not i["texts"]:
            lines.append("(no license file in the package; see the license text above)"
                         if i["name"].lower().startswith(("pyside6", "shiboken6"))
                         else "(no license file found in the package metadata; see the upstream project)")
        for path, text in i["texts"]:
            lines += [f"--- {path} ---", text.strip(), ""]
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def make_zip() -> pathlib.Path:
    """Zip the application folder: this is what a user would download."""
    import shutil
    base = DIST / f"{APP_NAME}-{sys.platform}"
    return pathlib.Path(shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir=APP_NAME))


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def write_report(infos, build_only, zip_path, files, forbidden) -> pathlib.Path:
    total = sum(s for _, s in files)
    zipped = zip_path.stat().st_size

    def verdict(n: int) -> str:
        return "within" if n / 1e6 <= BUDGET_MB else "OVER"

    by_origin: dict[str, list[int]] = {}
    natives = []
    for path, size in files:
        origin = classify_origin(path)
        by_origin.setdefault(origin, [0, 0])
        by_origin[origin][0] += 1
        by_origin[origin][1] += size
        if origin == "native/runtime file (no package)":
            natives.append((path.split("\\")[-1].split("/")[-1], size))

    lines = [
        "# S0 build report",
        "",
        f"- Platform: {platform.platform()} / Python {platform.python_version()}",
        f"- Download (`{zip_path.name}`): **{mb(zipped)}** - {verdict(zipped)} the {BUDGET_MB} MB budget",
        f"- Installed (folder `{APP_DIR.name}`, uncompressed): **{mb(total)}** - {verdict(total)} the {BUDGET_MB} MB budget (informational)",
        f"- Files in bundle: {len(files)} (full list: `bundle-manifest.txt`)",
        f"- Qt Addons / WebEngine files in the bundle: **{'NONE' if not forbidden else 'FOUND: ' + ', '.join(forbidden[:10])}**",
        "",
        "## Bundle contents by origin",
        "",
        "| Origin | Files | Size |",
        "|---|---|---|",
    ]
    for origin, (n, s) in sorted(by_origin.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"| {origin} | {n} | {mb(s)} |")
    lines += [
        "",
        "## Native and runtime files outside any pip package",
        "",
        "License hints are unverified and must be confirmed in the release-time check (ADR-0001 rule 7).",
        "",
        "| File | Size | What | License hint |",
        "|---|---|---|---|",
    ]
    unclassified = 0
    for name, size in sorted(natives, key=lambda t: -t[1]):
        what, lic = native_hint(name)
        unclassified += what == "UNCLASSIFIED"
        lines.append(f"| `{name}` | {mb(size)} | {what} | {lic} |")
    lines += ["", f"UNCLASSIFIED native/runtime files: **{unclassified}**", ""]
    lines += ["## Bundled pip components", "", "| Component | Version | License |", "|---|---|---|"]
    lines += [f"| {i['name']} | {i['version']} | {i['license']} |" for i in infos]
    lines += ["", "## Build-only tools (not shipped)", "", "| Tool | Version | License |", "|---|---|---|"]
    lines += [f"| {i['name']} | {i['version']} | {i['license']} |" for i in build_only]
    lines += [
        "",
        "Qt's own bundled third-party components are not itemised yet (see 'Licenses Used in Qt');",
        "this remains part of the release-time license check.",
    ]
    out = DIST / "build-report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    run_pyinstaller()
    files = list_bundle()
    forbidden = find_forbidden([p for p, _ in files])
    write_manifest(files)
    infos = [dist_info(n) for n in BUNDLED]
    build_only = [dist_info(n) for n in BUILD_ONLY if _installed(n)]
    write_notices(infos)
    zip_path = make_zip()
    report = write_report(infos, build_only, zip_path, files, forbidden)
    print(report.read_text(encoding="utf-8"))
    if forbidden:
        print("ERROR: forbidden Qt Addons/WebEngine files in the bundle (ADR-0001 rule 1):", forbidden[:20])
        return 2
    return 0


def _installed(name: str) -> bool:
    try:
        metadata.distribution(name)
        return True
    except metadata.PackageNotFoundError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
