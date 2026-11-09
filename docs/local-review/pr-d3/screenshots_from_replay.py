"""Live-panel screenshots from a replay of a recorded RAW archive (read-only; no device).

    set QT_QPA_PLATFORM=windows   (a real platform, so fonts render)
    python screenshots_from_replay.py <repo>/src <record.raw.bin> <output dir>

The window is the normal desktop window connected to the local fake. The preview frames come from
replaying the archive through the preview pipeline and are pushed to the graph panel directly; no
acquisition runs. Host/unit evidence only.
"""

import sys
import tempfile
import time
from pathlib import Path

src, raw_path, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, src)
sys.dont_write_bytecode = True
out.mkdir(parents=True, exist_ok=True)

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

from noctinirs_host.geometry import load_geometry  # noqa: E402
from noctinirs_host.preview.params import PreviewConfig  # noqa: E402
from noctinirs_host.preview.pipeline import PreviewPipeline  # noqa: E402
from noctinirs_host.record.raw_archive import RawReplay  # noqa: E402
from noctinirs_host.wire.envelope import parse_envelope  # noqa: E402
from noctinirs_host.wire.optical import parse_raw  # noqa: E402
from noctinirs_host_ui.engineering import EngineeringWindow  # noqa: E402

geometry = load_geometry(Path(src).parent / "config" / "optical_geometry_v1.json")
pipe = PreviewPipeline(geometry, PreviewConfig(baseline_s=10.0, ssr_mode="5mm"), 10.0)
frames = []
for packet in RawReplay(raw_path):
    env = parse_envelope(packet.datagram())
    frames += pipe.feed(parse_raw(env.payload), packet.host_rx_us, env.msg_sequence)
frames += pipe.flush()
print("frames", len(frames), "loss", pipe.loss_stats())

app = QtWidgets.QApplication([])


def wait(cond, seconds=20):
    end = time.monotonic() + seconds
    while not cond():
        if time.monotonic() > end:
            return False
        QtTest.QTest.qWait(20)
    return True


w = EngineeringWindow(geometry, documents=Path(tempfile.mkdtemp()))
w.setWindowTitle("NoctiNIRS 2 Host — replay of a recorded RAW archive (read-only, no device)")
w.show()
w.next.click()
w.connect_button.click()
wait(lambda: w.state.get("connected") and w.fs.findData(10) > 0)
w.fs.setCurrentIndex(w.fs.findData(10))
w.apply.click()
wait(lambda: w.state.get("configured") and w.next.isEnabled())
for _ in range(3):
    w.next.click()
wait(lambda: "Ready" in w.summary.toPlainText() and w.next.isEnabled())
w.next.click()
w.panel.reset_for_epoch(99)
w.panel.set_fs(10)


def show(t_end, name, size=None, fullscreen=False):
    w.panel.clear()
    w.panel.push([f for f in frames if f.t_s <= t_end and f.complete])   # skip the cut final frame
    w.panel.select("CH1")
    if fullscreen:
        w.showFullScreen()
    else:
        w.showNormal()
        w.resize(*size)
    QtTest.QTest.qWait(700)
    w.panel.refresh()
    QtTest.QTest.qWait(400)
    w.grab().save(str(out / name))
    print(name, w.size().width(), w.size().height())


show(29.5, "live-1366x768-first-sweep.png", (1366, 768))
show(82.9, "live-1366x768-end-of-recording.png", (1366, 768))
w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData("OD"))
show(82.9, "live-1366x768-od.png", (1366, 768))
w.panel.mode_combo.setCurrentIndex(w.panel.mode_combo.findData("Hb"))
show(82.9, "live-fullscreen.png", None, fullscreen=True)
w.showNormal()
w.resize(1280, 720)
show(29.5, "live-1280x720.png", (1280, 720))
w.worker.submit("shutdown")
w.worker.wait(5000)
