"""Benchmark of the stacked Live display: CPU use and draw frame time (host-only, no device).

    set QT_QPA_PLATFORM=windows
    python bench_live.py <repo>/src <record.raw.bin> [seconds]

Frames of a replay of the archive are pushed into the graph panel at the recorded 10 Hz and, as a
stress replay, at 100 Hz (ten times the rate, 20 channels). The panel's own display timer (25 Hz)
draws the rows in view. Reported: process CPU as a share of one core (main thread plus Qt), the time
of each draw (`refresh` that drew rows), and the lateness of the 25 Hz timer.
"""

import statistics
import sys
import tempfile
import time
from pathlib import Path

src, raw_path = sys.argv[1], sys.argv[2]
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
sys.path.insert(0, src)
sys.dont_write_bytecode = True

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

from noctinirs_host.geometry import load_geometry  # noqa: E402
from noctinirs_host.preview.params import PreviewConfig  # noqa: E402
from noctinirs_host.preview.pipeline import FrameResult, PreviewPipeline  # noqa: E402
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
frames = [f for f in frames if f.complete]

app = QtWidgets.QApplication([])
app.setStyle("Fusion")
settings = QtCore.QSettings(str(Path(tempfile.mkdtemp()) / "bench.ini"), QtCore.QSettings.Format.IniFormat)
w = EngineeringWindow(geometry, documents=Path(tempfile.mkdtemp()), settings=settings)
w.show()
w.resize(1366, 768)
w._go(5)
QtTest.QTest.qWait(500)
panel = w.panel


def run(rate_hz: float, label: str):
    panel.clear()
    panel.set_fs(rate_hz or 10.0)
    panel._epoch = w.state.get("preview_epoch")
    draws, late, pushed = [], [], [0]
    original = panel.refresh

    def timed():
        before = panel.refresh_count
        t0 = time.perf_counter()
        original()
        if panel.refresh_count != before:
            draws.append((time.perf_counter() - t0) * 1000)

    panel.timer.timeout.disconnect()
    panel.timer.timeout.connect(timed)
    last_tick = [time.perf_counter()]
    period = 1000.0 / 25

    def lateness():
        now = time.perf_counter()
        late.append(max(0.0, (now - last_tick[0]) * 1000 - period))
        last_tick[0] = now

    watcher = QtCore.QTimer()
    watcher.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    watcher.setInterval(int(period))
    watcher.timeout.connect(lateness)
    watcher.start()
    start, cpu0 = time.perf_counter(), time.process_time()
    step = 1.0 / rate_hz if rate_hz else None
    n = 0
    while time.perf_counter() - start < SECONDS:
        due = int((time.perf_counter() - start) / step) if step else 0
        batch = []
        while n < due:
            base = frames[n % len(frames)]
            batch.append(FrameResult(n * step, base.points, True))
            n += 1
        if batch:
            panel.push(batch)
            pushed[0] += len(batch)
        QtTest.QTest.qWait(5)
    wall, cpu = time.perf_counter() - start, time.process_time() - cpu0
    watcher.stop()
    panel.timer.timeout.disconnect()
    panel.timer.timeout.connect(original)
    draws.sort()
    late.sort()
    p95 = draws[int(0.95 * (len(draws) - 1))] if draws else 0
    mean = statistics.mean(draws) if draws else 0.0
    return (f"| {label} | {pushed[0]} | {len(draws) / wall:.1f} | {mean:.2f} | {p95:.2f} | "
            f"{max(draws) if draws else 0.0:.2f} | {late[int(0.95 * (len(late) - 1))]:.1f} | {max(late):.1f} | {100 * cpu / wall:.0f} % |")


lines = [f"Stacked Live display, window {w.width()} x {w.height()}, {SECONDS:g} s per run, 3 rows in view, "
         f"sweep {panel.window_s} s, replay of `{Path(raw_path).name}` (SCR 5 mm)", "",
         "| run | frames pushed | draws/s | draw mean ms | draw p95 ms | draw max ms | timer late p95 ms | "
         "timer late max ms | process CPU (1 core = 100 %) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
lines.append(run(0.0, "idle: no frames, same loop (harness cost)"))
if "--profile" in sys.argv:
    import cProfile
    import pstats
    prof = cProfile.Profile()
    prof.enable()
    lines.append(run(10.0, "replay at 10 Hz (profiled)"))
    prof.disable()
    pstats.Stats(prof).sort_stats("tottime").print_stats(14)
else:
    lines.append(run(10.0, "replay at 10 Hz"))
lines.append(run(100.0, "stress at 100 Hz"))
panel.scroll.scroll_to(panel.scroll.verticalScrollBar().maximum())
QtTest.QTest.qWait(400)
lines.append(run(10.0, "replay at 10 Hz, scrolled to the end"))
print("\n".join(lines))
w.worker.submit("shutdown")
w.worker.wait(5000)
