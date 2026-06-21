#!/usr/bin/env python3
"""Tes koneksi Google Sheets — TANPA perlu data Invezgo / kirim Telegram.

Memverifikasi rantai setup mirror: kredensial service-account → buka
spreadsheet → tulis 1 baris penanda. Diagnostik step-by-step supaya jelas
bagian mana yang salah (kredensial / ID / sharing / API).

Lokal:
    set GOOGLE_APPLICATION_CREDENTIALS=path\\ke\\service-account.json
    set MARKUP_RADAR_SHEET_ID=<id-sheet>
    python scripts/test_sheets.py
Atau via GitHub Actions: tab Actions → "Test Sheets" → Run workflow.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings
from markup_radar.store.sheets import SheetsSink, load_service_account_info


def main() -> int:
    cfg = load_settings()
    sheets = cfg.sheets

    if not sheets.get("enabled"):
        print("[ERROR] sheets.enabled=false di config/settings.yaml.", file=sys.stderr)
        return 1

    spreadsheet_id = os.getenv("MARKUP_RADAR_SHEET_ID") or sheets.get("spreadsheet_id", "")
    if not spreadsheet_id:
        print("[ERROR] spreadsheet_id kosong. Set env MARKUP_RADAR_SHEET_ID atau "
              "isi sheets.spreadsheet_id di settings.yaml.", file=sys.stderr)
        return 1

    info = load_service_account_info()
    if not info:
        print("[ERROR] kredensial service-account tak ditemukan. Set env "
              "GOOGLE_SERVICE_ACCOUNT_JSON (isi JSON) atau "
              "GOOGLE_APPLICATION_CREDENTIALS (path file JSON).", file=sys.stderr)
        return 1

    worksheet = sheets.get("worksheet", "signals")
    print(f"[info] service-account : {info.get('client_email', '?')}")
    print(f"[info] spreadsheet_id  : {spreadsheet_id}")
    print(f"[info] worksheet       : {worksheet}")

    try:
        sink = SheetsSink.connect(spreadsheet_id, worksheet, info)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] gagal connect: {exc}", file=sys.stderr)
        print("        Periksa: (1) sheet sudah di-Share (Editor) ke client_email "
              "di atas, (2) Google Sheets API sudah Enable, (3) ID sheet benar.",
              file=sys.stderr)
        return 1

    record = {
        "date": dt.date.today().isoformat(),
        "code": "TEST",
        "state": "NEUTRAL",
        "confidence": 0,
        "narrative": "baris verifikasi koneksi Sheets — boleh dihapus",
        "signals": {
            "done_ratio": 0.0, "rvol": 0.0, "close_in_range": 0.0,
            "broker_net_buy_streak": 0, "queue_imbalance": 0.0,
            "ihsg_above_ma50": False,
        },
    }
    n = sink.append_results([record])
    print(f"\n[OK] {n} baris penanda ditulis ke worksheet '{worksheet}'. "
          "Cek Google Sheet kamu — baris 'TEST' itu boleh dihapus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
