"""Packaging inputs and helpers that the build depends on."""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIC = ROOT / "packaging" / "licenses"


def _load_build():
    spec = importlib.util.spec_from_file_location("nn2_build", ROOT / "packaging" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # imports only the standard library
    return mod


def test_lgpl_and_gpl_texts_present() -> None:
    lgpl = (LIC / "LGPL-3.0-only.txt").read_text(encoding="utf-8")
    gpl = (LIC / "GPL-3.0-only.txt").read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl and "Version 3" in lgpl
    assert "GNU GENERAL PUBLIC LICENSE" in gpl and "Version 3" in gpl


def test_forbidden_qt_parts_are_detected() -> None:
    b = _load_build()
    bad = [
        "_internal/PySide6/Qt/lib/libQt6WebEngineCore.so.6",
        "_internal\\PySide6\\plugins\\designer\\qwebengineview.dll",
        "_internal\\PySide6\\Qt6Charts.dll",
        "_internal/PySide6/QtDataVisualization.abi3.so",
        "_internal/PySide6/Qt/lib/libQt63DCore.so.6",
        "_internal/PySide6/Qt/lib/libQt6Quick3D.so.6",
        "_internal\\PySide6\\QtMultimedia.pyd",
    ]
    assert b.find_forbidden(bad) == bad


def test_allowed_qt_parts_are_not_flagged() -> None:
    b = _load_build()
    ok = [
        "_internal/PySide6/QtCore.abi3.so",
        "_internal\\PySide6\\Qt6Widgets.dll",
        "_internal/PySide6/Qt/lib/libQt6Gui.so.6",
        "_internal/PySide6/QtNetwork.abi3.so",
        "_internal/PySide6/Qt/lib/libQt6Network.so.6",
        "_internal/PySide6/QtOpenGLWidgets.abi3.so",
        "_internal/PySide6/Qt/plugins/networkinformation/libqglib.so",
        "_internal/numpy/_core/_multiarray_umath.pyd",
    ]
    assert b.find_forbidden(ok) == []


def test_origin_and_native_classification() -> None:
    b = _load_build()
    assert b.classify_origin("NoctiNIRS-Host.exe") == "application launcher and notices"
    assert b.classify_origin("_internal\\PySide6\\Qt6Core.dll") == "PySide6 (Qt)"
    assert b.classify_origin("_internal/numpy.libs/libscipy_openblas.dll") == "NumPy"
    assert b.classify_origin("_internal/python312.dll") == "native/runtime file (no package)"
    assert b.native_hint("python312.dll")[0] == "Python runtime"
    assert b.native_hint("libcrypto-3-x64.dll")[0] == "OpenSSL"
    assert b.native_hint("unicodedata.pyd")[0] == "Python standard-library extension module"
    assert b.native_hint("some_unknown_library.dll")[0] == "UNCLASSIFIED"
