"""Text for the battery indicator. No Qt, no protocol constants: it reads the snapshot dict only.

Rules from the firmware contract (ADR-0004 section 14): valid=0 is "not available", never 0 %;
percent is not clamped by the device; there is no charging field, so nothing here says or draws
"charging".
"""

from __future__ import annotations

from dataclasses import dataclass

LOW_PERCENT = 20.0  # host-chosen warning threshold, not a device value


@dataclass(frozen=True)
class BatteryText:
    text: str
    tooltip: str
    level: str  # "normal" | "low" | "problem"


def _num(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _age(age_ms: int | None) -> str:
    if age_ms is None:
        return ""
    s = max(0, int(age_ms // 1000))
    return f"{s} s lalu" if s < 600 else f"{s // 60} mnt lalu"


def format_battery(b: dict | None) -> BatteryText:
    b = b or {}
    status = b.get("status", "DISCONNECTED")
    if status in ("DISCONNECTED", "UNKNOWN") or not b.get("connected", status != "DISCONNECTED"):
        return BatteryText("Baterai —", "Tidak terhubung atau belum ditanya.", "normal")
    pct, volts, age = b.get("percent"), b.get("volts"), b.get("age_ms")
    err, detail = b.get("error"), b.get("detail") or ""
    source = "SIMULATED (fake lokal)" if b.get("simulated") else (
        "perangkat" if b.get("simulated") is False else "tidak diketahui")
    value = ""
    tip = []
    if pct is not None:
        value = f"{_num(pct, 1)} % · {_num(volts, 2)} V"
        tip.append(f"{int(round(pct * 1000))} per mil-persen · {_num(volts * 1000, 0)} mV "
                   f"({b.get('uv')} µV)")
        if pct > 100:
            tip.append("Firmware tidak membatasi nilai pada 100 %.")
        tip.append(f"Umur sampel perkiraan: {_age(age)}. Firmware tidak membaca baterai saat akuisisi.")
    tip.append(f"Kode refresh terakhir: {err}")
    tip.append(f"Sumber: {source}")
    tip.append("Persentase dan tegangan saja; status pengisian tidak dilaporkan firmware.")
    suffix = ""
    if b.get("streaming") and pct is not None:
        suffix += " · nilai cache sebelum akuisisi"
    if b.get("waiting_post_stop"):
        suffix += " · menunggu refresh setelah STOP"
    level = "low" if pct is not None and pct < LOW_PERCENT else "normal"

    if status == "OK":
        text = f"Baterai {value} · {_age(age)}"
    elif status == "NOT_AVAILABLE":
        text = "Baterai belum tersedia"
        if detail:
            text += f" ({detail})"
    elif status == "STALE_REFRESH_FAILED":
        text = f"Baterai {_num(pct, 1)} % · refresh gagal ({err}) · {_age(age)}"
        level = "problem" if level == "normal" else level
    elif status == "UNSUPPORTED":
        text, level = "Baterai: tidak didukung firmware ini", "normal"
    elif status == "ERROR":
        text, level = f"Baterai: error {detail}", "problem"
    elif status == "NO_REPLY":
        text = (f"Baterai {value} · tidak ada jawaban · {_age(age)}" if pct is not None
                else "Baterai: tidak ada jawaban")
        level = "problem" if level == "normal" else level
    else:
        text = f"Baterai: {status}"
    if level == "low":
        text += " · rendah (ambang pilihan host)"
    return BatteryText(text + suffix, "\n".join(tip), level)
