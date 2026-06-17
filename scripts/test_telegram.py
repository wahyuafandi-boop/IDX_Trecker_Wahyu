#!/usr/bin/env python3
"""Tes koneksi Telegram bot — TANPA perlu data Invezgo.

Membangun pesan alert dari data sintetis (skenario demo) lalu mengirimkannya
ke chat kamu. Berguna untuk memvalidasi TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID
sebelum langganan Invezgo aktif.

    python scripts/test_telegram.py            # kirim ke Telegram
    python scripts/test_telegram.py --dry-run  # cuma cetak pesannya
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.alert import format_alert, send_telegram
from markup_radar.config import load_settings
from markup_radar.demo import classify_snapshot, make_snapshot

DEMO = [("markup", "MRKP"), ("accumulation", "ACUM"), ("distribution", "DSTR")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Tes Telegram bot (data sintetis)")
    ap.add_argument("--dry-run", action="store_true", help="cetak saja, jangan kirim")
    args = ap.parse_args()

    cfg = load_settings()
    items = []
    for kind, code in DEMO:
        state, conf, signals = classify_snapshot(make_snapshot(kind, code), cfg)
        if state in cfg.alert_states:
            items.append({"code": code, "state": state, "confidence": conf, "signals": signals})

    msg = format_alert("DEMO / tes bot", items)
    print(msg)

    if args.dry_run:
        return 0

    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        print("\n[ERROR] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set di .env",
              file=sys.stderr)
        return 1

    try:
        send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
        print("\n[OK] Pesan terkirim ke Telegram. Cek chat-mu.")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] gagal kirim: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
