"""D4 screenshots: both themes, four window sizes, every screen. Read-only; no device.

    set QT_QPA_PLATFORM=windows            (a real platform, so fonts render)
    python screenshots_d4.py <repo>/src <record.raw.bin> <output dir> [--replay-seconds N]

The window is the normal desktop window connected to the local fake device (no acquisition runs).
The Live screen shows the preview frames of a replay of the recorded archive pushed to the
stacked rows. Sizes: 1280x720, 1366x768, 1920x1080 (the window is resized; the grab is the whole
window even if the screen is smaller) and the real fullscreen of the primary screen.
"""

import sys
import tempfile
import time
from pathlib import Path

src, raw_path, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, src)
sys.dont_write_bytecode = True
out.mkdir(parents=True, exist_ok=True)
T_END = float(sys.argv[sys.argv.index("--replay-seconds") + 1]) if "--replay-seconds" in sys.argv else 29.5

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
frames = [f for f in frames if f.complete]
print("frames", len(frames))

app = QtWidgets.QApplication([])
app.setStyle("Fusion")
settings = QtCore.QSettings(str(Path(tempfile.mkdtemp()) / "shots.ini"), QtCore.QSettings.Format.IniFormat)


def wait(cond, seconds=20):
    end = time.monotonic() + seconds
    while not cond():
        if time.monotonic() > end:
            return False
        QtTest.QTest.qWait(20)
    return True


w = EngineeringWindow(geometry, documents=Path(tempfile.mkdtemp()), settings=settings)
w.show()
w.next.click()
w.connect_button.click()
wait(lambda: w.state.get("connected") and w.fs.findData(10) > 0)
w.fs.setCurrentIndex(w.fs.findData(10))
w.apply.click()
wait(lambda: w.state.get("configured") and w.next.isEnabled())
w.mbll_form.baseline.setValue(10.0)
w._go(0)
w.panel.set_fs(10)

SIZES = {"1280x720": (1280, 720), "1366x768": (1366, 768), "1920x1080": (1920, 1080), "fullscreen": None}
SCREENS = {"1-welcome": 0, "2-setup-device": 1, "3-setup-mbll": 2, "4-setup-ssr": 3, "5-setup-session": 4,
           "6-live": 5}
for theme in ("light", "dark"):
    w.set_theme(theme)
    for size_name, size in SIZES.items():
        if size is None:
            w.showFullScreen()
        else:
            w.showNormal()
            w.resize(*size)
        QtTest.QTest.qWait(600)
        for screen_name, flat in SCREENS.items():
            w._go(flat)
            if flat == 5 and not w.panel._tl:      # the stream of the fake is idle; show the replay frames
                w.panel._epoch = w.state.get("preview_epoch")
                w.panel.push([f for f in frames if f.t_s <= T_END])
            QtTest.QTest.qWait(450)          # let the fade finish
            w.panel.refresh()
            QtTest.QTest.qWait(200)
            name = f"{theme}-{size_name}-{screen_name}.png"
            w.grab().save(str(out / name))
        print(theme, size_name, w.size().width(), w.size().height())
w.showNormal()
w.worker.submit("shutdown")
w.worker.wait(5000)
