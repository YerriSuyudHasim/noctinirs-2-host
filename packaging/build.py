"""Build the standalone UI as a PyInstaller onedir bundle and write a report.

Usage (from the repository root, with ``pip install -e ".[ui,build]"``):

    python packaging/build.py

Outputs:
    dist/NoctiNIRS-Host/                  the application folder (shipped)
    dist/NoctiNIRS-Host/THIRD_PARTY_NOTICES.txt   inventory + license texts
    dist/build-report.md                  size, size breakdown, inventory (not shipped)

Only the Python standard library is used besides PyInstaller itself.
"""

from __future__ import annotations

import pathlib
import platform
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


def dist_info(name: str) -> dict:
    d = metadata.distribution(name)
    m = d.metadata
    lic = m.get("License-Expression") or m.get("License") or ""
    if not lic or len(lic) > 120:  # long free-text License fields: fall back to classifiers
        cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License")]
        lic = ", ".join(cls) or (lic[:120] + "...") or "UNKNOWN"
    texts = []
    for f in d.files or []:
        low = f.name.lower()
        if any(h in low for h in LICENSE_FILE_HINTS) and f.suffix.lower() in ("", ".txt", ".md", ".rst"):
            try:
                texts.append((str(f), pathlib.Path(f.locate()).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return {"name": m["Name"], "version": m["Version"], "license": lic, "texts": texts}


def write_notices(infos: list[dict]) -> pathlib.Path:
    out = APP_DIR / "THIRD_PARTY_NOTICES.txt"
    lines = [
        "NoctiNIRS 2 Host - third-party notices",
        "This application bundles the components below. Qt (via PySide6) is used",
        "unmodified as separate shared libraries under the LGPL-3.0; you may replace them.",
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
        "(see 'Licenses Used in Qt' in the Qt documentation). They are not listed here yet.",
        "",
    ]
    for i in infos:
        lines += ["=" * 72, f"{i['name']} {i['version']}  -  license: {i['license']}", "=" * 72]
        if not i["texts"]:
            lines.append("(no license file in the package; the license text is given above)" if i["name"].lower().startswith(("pyside6", "shiboken6")) else "(no license file found in the package metadata; see the upstream project)")
        for path, text in i["texts"]:
            lines += [f"--- {path} ---", text.strip(), ""]
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def folder_size(p: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def make_zip() -> pathlib.Path:
    """Zip the application folder: this is what a user would download."""
    import shutil
    base = DIST / f"{APP_NAME}-{sys.platform}"
    return pathlib.Path(shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir=APP_NAME))


def write_report(infos: list[dict], build_only: list[dict], zip_path: pathlib.Path) -> pathlib.Path:
    total = folder_size(APP_DIR)
    zipped = zip_path.stat().st_size
    entries = sorted(
        ((folder_size(c) if c.is_dir() else c.stat().st_size, c.relative_to(APP_DIR)) for c in APP_DIR.rglob("*")
         if c.parent == APP_DIR or c.parent.parent == APP_DIR and c.parent.name == "_internal"),
        reverse=True,
    )[:15]
    def verdict(n: int) -> str:
        return "within" if n / 1e6 <= BUDGET_MB else "OVER"
    lines = [
        "# S0 build report",
        "",
        f"- Platform: {platform.platform()} / Python {platform.python_version()}",
        f"- Download (`{zip_path.name}`): **{mb(zipped)}** - {verdict(zipped)} the {BUDGET_MB} MB budget",
        f"- Installed (folder `{APP_DIR.name}`, uncompressed): **{mb(total)}** - {verdict(total)} the {BUDGET_MB} MB budget (informational)",
        "",
        "## Largest items in the bundle",
        "",
        "| Item | Size |",
        "|---|---|",
    ]
    lines += [f"| `{p}` | {mb(s)} |" for s, p in entries]
    lines += ["", "## Bundled components", "", "| Component | Version | License |", "|---|---|---|"]
    lines += [f"| {i['name']} | {i['version']} | {i['license']} |" for i in infos]
    lines += ["", "## Build-only tools (not shipped)", "", "| Tool | Version | License |", "|---|---|---|"]
    lines += [f"| {i['name']} | {i['version']} | {i['license']} |" for i in build_only]
    lines += [
        "",
        "Not covered by this inventory: the Python runtime and any native libraries that",
        "PyInstaller copies from the build machine (for example the C runtime). Review them",
        "in the release-time license check (ADR-0001 rule 7).",
    ]
    out = DIST / "build-report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    run_pyinstaller()
    infos = [dist_info(n) for n in BUNDLED]
    build_only = [dist_info(n) for n in BUILD_ONLY if _installed(n)]
    write_notices(infos)
    zip_path = make_zip()
    report = write_report(infos, build_only, zip_path)
    print(report.read_text(encoding="utf-8"))
    return 0


def _installed(name: str) -> bool:
    try:
        metadata.distribution(name)
        return True
    except metadata.PackageNotFoundError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
