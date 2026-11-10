"""Offscreen layout / contrast evidence for the D4 fix (simulation, no device).

Run from the repository root:
    $env:PYTHONPATH='src;tests'; $env:QT_QPA_PLATFORM='offscreen'
    python docs/local-review/pr-d4-fix/evidence_d4_fix.py <output folder>

Writes screens-<WxH>-<theme>-<state>.png and measurements.json. The screenshots use synthetic frames
(sine waves) and a synthetic lost-START state; they are not device data.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

from PySide6 import QtCore, QtTest, QtWidgets

from noctinirs_host.geometry import load_geometry
from noctinirs_host_ui.design import tokens as T
from noctinirs_host_ui.engineering import MIN_WINDOW, EngineeringWindow
from test_d4_fix import GEOMETRY, LONG, fully_visible_rows, frame, visible_fraction


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyle("Fusion")
    tmp = Path(tempfile.mkdtemp())
    result = {"measured_in": "offscreen Qt (not a device, not native Windows rendering)", "layout": [],
              "contrast": {}}
    geo = load_geometry(GEOMETRY)
    for theme in ("light", "dark"):
        settings = QtCore.QSettings(str(tmp / f"{theme}.ini"), QtCore.QSettings.Format.IniFormat)
        w = EngineeringWindow(geo, documents=tmp / "Documents", settings=settings)
        w.theme.set_reduce_motion(True)
        w.worker.updated.disconnect(w._update)
        w.set_theme(theme)
        w.show()
        w._go(5)
        w.panel.push([frame(geo, i) for i in range(300)])
        for size in (MIN_WINDOW, (1024, 600), (1366, 768)):
            w.resize(*size)
            for uncertain in (False, True):
                w.state = {"connected": True, "start_uncertain": uncertain, "start_wait": 0.0, "stopped": True,
                           "device_state": "STOPPED", "notice": LONG if uncertain else "",
                           "error": LONG if uncertain else ""}
                w._render()
                QtTest.QTest.qWait(250)
                w.panel.refresh()
                p = w.panel
                buttons = {"START": w.start_button, "STOP": w.stop_button, "Disconnect": w.panel_disconnect}
                if uncertain:
                    check, safe = w._recovery_buttons[-1]
                    buttons.update({"Check START outcome": check, "SAFE_OFF": safe})
                entry = {"theme": theme, "window": list(size), "state": "lost-START" if uncertain else "ordinary",
                         "sidebar_open": w.sidebar_open, "viewport_h": p.scroll.viewport().height(),
                         "row_h": p.row_height(), "row_floor": 48, "full_rows_at_top": fully_visible_rows(p)[:4],
                         "visible_fraction": {k: round(visible_fraction(b), 3) for k, b in buttons.items()}}
                result["layout"].append(entry)
                if theme == "light" or uncertain:
                    w.grab().save(str(out / f"screens-{size[0]}x{size[1]}-{theme}-{entry['state']}.png"), "PNG")
        t = T.THEMES[theme]
        legend = re.findall(r"color:(#[0-9A-Fa-f]{6})", w.panel.rows["CH1"].legend.text())
        surfaces = {"surface": t.surface, "surface_raised(selected row)": t.surface_raised,
                    "plot_background": t.plot_background}
        result["contrast"][theme] = {
            "legend_swatch_vs": {k: round(T.contrast(legend[0], v), 2) for k, v in surfaces.items()},
            "legend_text_vs": {k: round(T.contrast(legend[1], v), 2) for k, v in surfaces.items()},
            "warning_marker_vs": {k: round(T.contrast(t.marker_warning, v), 2) for k, v in surfaces.items()},
            "hbo_curve_vs_plot": round(T.contrast(t.hbo, t.plot_background), 2),
        }
        worker = w.worker
        worker.submit("shutdown")
        worker.wait()
    (out / "measurements.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {len(result['layout'])} layout rows and the contrast table to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
