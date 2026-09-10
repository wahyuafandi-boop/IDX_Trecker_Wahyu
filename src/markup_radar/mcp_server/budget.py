"""Budget guard persisten untuk konsumsi kuota Invezgo lewat MCP.

Beda dari kuota Invezgo (30k/bln, billing) — ini pagar SELF-imposed: berapa
banyak call yang boleh dihabiskan LEWAT server MCP, harian & bulanan, supaya
jatah cron EOD/live tetap aman walau token MCP bocor / ada yang nyalahgunain.

Persisten ke file JSON (atomic write) → restart service TIDAK me-reset hitungan
(kalau in-memory, penyerang tinggal paksa restart utk reset). Thread-safe:
tool sync FastMCP dijalankan di worker thread → butuh lock utk read-modify-write.

Hari/bulan pakai WIB (UTC+7, Indonesia tanpa DST) supaya rollover harian selaras
hari bursa. Cap default: 500/hari, 5000/bulan (sisakan >=25k utk cron).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WIB = timezone(timedelta(hours=7))


class BudgetExceeded(RuntimeError):
    """Cap harian/bulanan tercapai."""

    def __init__(self, scope: str, used: int, cap: int) -> None:
        self.scope = scope
        self.used = used
        self.cap = cap
        super().__init__(f"budget {scope} exceeded: {used}/{cap}")


class QuotaBudget:
    def __init__(self, path: str | Path, *, daily_cap: int, monthly_cap: int) -> None:
        self.path = Path(path)
        self.daily_cap = daily_cap
        self.monthly_cap = monthly_cap
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _keys() -> tuple[str, str]:
        now = datetime.now(_WIB)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _rolled(self, st: dict) -> tuple[str, str, int, int]:
        """Ambil hitungan hari/bulan ini; auto-reset bila tanggal/bulan berganti."""
        day, month = self._keys()
        day_calls = int(st.get("day_calls", 0)) if st.get("date") == day else 0
        month_calls = int(st.get("month_calls", 0)) if st.get("month") == month else 0
        return day, month, day_calls, month_calls

    def _write(self, day: str, month: str, day_calls: int, month_calls: int) -> None:
        st = {"date": day, "day_calls": day_calls, "month": month, "month_calls": month_calls}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        tmp.replace(self.path)  # atomic

    def snapshot(self) -> dict:
        with self._lock:
            day, month, dc, mc = self._rolled(self._load())
        return {
            "date": day,
            "day_calls": dc,
            "daily_cap": self.daily_cap,
            "day_remaining": max(0, self.daily_cap - dc),
            "month": month,
            "month_calls": mc,
            "monthly_cap": self.monthly_cap,
            "month_remaining": max(0, self.monthly_cap - mc),
        }

    def spend(self, n: int = 1) -> None:
        """Cek cap lalu increment. Raise BudgetExceeded bila lewat (belum increment)."""
        with self._lock:
            day, month, dc, mc = self._rolled(self._load())
            if dc + n > self.daily_cap:
                raise BudgetExceeded("harian", dc, self.daily_cap)
            if mc + n > self.monthly_cap:
                raise BudgetExceeded("bulanan", mc, self.monthly_cap)
            self._write(day, month, dc + n, mc + n)

    def record(self, n: int = 1) -> None:
        """Increment tanpa cek cap (utk call meta spt check_quota yang tak boleh diblok)."""
        with self._lock:
            day, month, dc, mc = self._rolled(self._load())
            self._write(day, month, dc + n, mc + n)
