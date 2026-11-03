"""The core must stay importable and usable without any UI or Qt module (ADR-0001)."""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
CORE = SRC / "noctinirs_host"
FORBIDDEN = ("PySide6", "shiboken6", "pyqtgraph", "PyQt5", "PyQt6", "noctinirs_host_ui")


def test_core_sources_do_not_import_ui_or_qt() -> None:
    offenders = []
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] in FORBIDDEN:
                    offenders.append(f"{path.relative_to(SRC)}: {n}")
    assert not offenders, offenders


def test_importing_all_core_modules_loads_no_qt() -> None:
    code = (
        "import sys, importlib, pkgutil\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import noctinirs_host\n"
        "for m in pkgutil.walk_packages(noctinirs_host.__path__, 'noctinirs_host.'):\n"
        "    importlib.import_module(m.name)\n"
        f"bad = [m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r}]\n"
        "assert not bad, bad\n"
        "print('CORE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "CORE_OK" in result.stdout
