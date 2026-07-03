"""Mirror hasil scan harian ke Google Sheets (persistensi + dashboard Phase 6).

Kenapa: runner GitHub Actions ephemeral → SQLite (`store/db.py`) ke-reset tiap
run, jadi histori sinyal tak numpuk. Padahal kebutuhan #1 = akumulasi data
forward buat validasi edge (yang masih lemah/1-window). Google Sheet persisten
menampung histori lintas run sekaligus jadi sumber dashboard.

Append-only: tiap baris = satu (date, code) hasil scan (SEMUA kode, termasuk
NEUTRAL) supaya forward return bisa dihitung belakangan untuk validasi. Sink ini
**no-op rapi** bila lib gspread / kredensial / spreadsheet_id tak ada (lokal &
dev) sehingga `run_daily` tak pernah gagal gara-gara mirror ini.

Kredensial service-account (JSON) dibaca dari env:
  - GOOGLE_SERVICE_ACCOUNT_JSON : isi JSON mentah (dipakai di GitHub Actions secret)
  - GOOGLE_APPLICATION_CREDENTIALS : path ke file JSON (alternatif lokal)
Spreadsheet di-share (Editor) ke email service-account dulu.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Kolom log. Urutan ini = header sheet; jangan diacak (downstream/dashboard
# bergantung posisi). Tambah kolom baru di AKHIR saja.
_HEADER = [
    "run_ts",
    "date",
    "code",
    "state",
    "confidence",
    "done_ratio",
    "rvol",
    "close_in_range",
    "broker_net_buy_streak",
    "queue_imbalance",
    "ihsg_above_ma50",
    "narrative",
    # -- kolom evaluasi (ditambah 2026-07-03; posisi di akhir sesuai aturan) --
    "regime",
    "relative_strength",
    "alert_sent",      # True = sinyal ini benar-benar terkirim ke Telegram
    "entry",           # levels hanya terisi utk MARKUP_* (state lain kosong)
    "stop_loss",
    "take_profit",
    "rr_realized",
]


def load_service_account_info() -> dict | None:
    """Ambil kredensial service-account dari env. None bila tak ada."""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _result_row(result: dict, run_ts: str) -> list[Any]:
    """Ubah satu hasil scan jadi baris sesuai urutan `_HEADER`."""
    s = result.get("signals", {})
    lv = result.get("levels") or {}   # None utk non-MARKUP -> sel kosong
    return [
        run_ts,
        result.get("date", ""),
        result.get("code", ""),
        result.get("state", ""),
        result.get("confidence", 0),
        round(float(s.get("done_ratio", 0.0)), 4),
        round(float(s.get("rvol", 0.0)), 4),
        round(float(s.get("close_in_range", 0.0)), 4),
        int(s.get("broker_net_buy_streak", 0)),
        round(float(s.get("queue_imbalance", 0.0)), 4),
        bool(s.get("ihsg_above_ma50", False)),
        result.get("narrative", ""),
        result.get("regime", ""),
        round(float(result.get("relative_strength", 0.0)), 4),
        bool(result.get("alert_sent", False)),
        lv.get("entry", ""),
        lv.get("stop_loss", ""),
        lv.get("take_profit", ""),
        lv.get("rr_realized", ""),
    ]


class SheetsSink:
    """Penulis append-only ke satu worksheet. Worksheet di-inject agar mudah
    di-test (cukup objek apa pun yang punya `append_rows`)."""

    def __init__(self, worksheet: Any) -> None:
        self._ws = worksheet

    @classmethod
    def connect(
        cls,
        spreadsheet_id: str,
        worksheet: str,
        credentials_info: dict,
    ) -> "SheetsSink":
        """Auth service-account → buka spreadsheet → worksheet (buat + header
        bila belum ada). Import gspread di sini agar dependensi tetap opsional."""
        import gspread

        gc = gspread.service_account_from_dict(credentials_info, scopes=_SCOPES)
        sh = gc.open_by_key(spreadsheet_id)
        try:
            ws = sh.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=worksheet, rows=1000, cols=len(_HEADER))
            ws.append_row(_HEADER, value_input_option="RAW")
        return cls(ws)

    def append_results(self, results: list[dict], *, run_ts: str | None = None) -> int:
        """Append baris untuk tiap hasil scan. Return jumlah baris ditulis."""
        run_ts = run_ts or dt.datetime.now().isoformat(timespec="seconds")
        rows = [_result_row(r, run_ts) for r in results]
        if rows:
            self._ws.append_rows(rows, value_input_option="RAW")
        return len(rows)


def overwrite_worksheet(
    spreadsheet_id: str,
    worksheet: str,
    credentials_info: dict,
    header: list[str],
    rows: list[list[Any]],
) -> int:
    """Tulis-ulang SELURUH isi worksheet (clear + header + rows).

    Dipakai untuk data turunan (hasil evaluate_signals) yang selalu bisa
    dihitung ulang dari log `signals` — idempoten, aman dijalankan berulang
    tanpa menduplikasi baris. Return jumlah baris data ditulis.
    """
    import gspread

    gc = gspread.service_account_from_dict(credentials_info, scopes=_SCOPES)
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(worksheet)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(
            title=worksheet, rows=max(len(rows) + 10, 100), cols=len(header)
        )
    ws.update([header] + rows, value_input_option="RAW")
    return len(rows)


def build_sink(cfg) -> SheetsSink | None:
    """Bangun SheetsSink dari config + env. None (sink mati) bila salah satu
    syarat tak terpenuhi: feature off, tak ada spreadsheet_id, gspread tak
    terpasang, atau kredensial tak ada. Tak pernah melempar exception."""
    sheets = cfg.sheets
    if not sheets.get("enabled"):
        return None

    spreadsheet_id = os.getenv("MARKUP_RADAR_SHEET_ID") or sheets.get("spreadsheet_id", "")
    if not spreadsheet_id:
        return None

    try:
        import gspread  # noqa: F401
    except ImportError:
        return None

    info = load_service_account_info()
    if not info:
        return None

    try:
        return SheetsSink.connect(
            spreadsheet_id, sheets.get("worksheet", "signals"), info
        )
    except Exception:  # noqa: BLE001 — sink opsional, jangan jatuhkan run
        return None
