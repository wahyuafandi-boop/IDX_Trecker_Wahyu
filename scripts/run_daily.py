#!/usr/bin/env python3
"""Entrypoint EOD: scan watchlist -> sinyal -> klasifikasi -> store -> alert.

Contoh:
    python scripts/run_daily.py                 # tanggal hari ini
    python scripts/run_daily.py --date 2026-06-16
    python scripts/run_daily.py --dry-run       # tanpa kirim Telegram

Catatan: butuh INVEZGO_API_KEY di .env. Beberapa path endpoint Invezgo masih
perlu diverifikasi (lihat scripts/verify_data.py & client.py TODO).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Izinkan import 'markup_radar' tanpa install (src layout).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.alert import format_alert, send_telegram
from markup_radar.config import load_settings
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import fetch_broker_summary, fetch_closing_queue
from markup_radar.ingest.done_client import fetch_done_breakdown
from markup_radar.ingest.foreign_client import fetch_foreign_net
from markup_radar.ingest.ihsg_client import fetch_ihsg
from markup_radar.ingest.ohlc_client import fetch_ohlcv
from markup_radar.narrative import generate_narrative
from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals
from markup_radar.store import Store


def _date_range(end: dt.date, days_back: int) -> tuple[str, str]:
    start = end - dt.timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def build_stock_data(client: InvezgoClient, code: str, date: dt.date, cfg) -> StockData:
    """Tarik & normalisasi semua data EOD untuk satu saham."""
    windows = cfg.windows
    ohlc_from, ohlc_to = _date_range(date, max(windows.get("volume_ma", 20) * 2, 60))
    streak_lb = windows.get("broker_streak_lookback", 5)

    ohlcv = fetch_ohlcv(client, code, ohlc_from, ohlc_to)
    done = fetch_done_breakdown(client, code, date.isoformat())
    queue = fetch_closing_queue(client, code)

    # Broker summary per hari (untuk streak S3) + agregat range (untuk concentration S4).
    daily_net: list[float] = []
    for i in range(streak_lb, -1, -1):
        d = (date - dt.timedelta(days=i)).isoformat()
        bs = fetch_broker_summary(client, code, d, d)
        if not bs.empty:
            top = bs.nlargest(cfg.broker_top_n, "net_value")["net_value"].sum()
            daily_net.append(float(top))
    broker_agg = fetch_broker_summary(client, code, *_date_range(date, streak_lb))

    ihsg = fetch_ihsg(client, *_date_range(date, windows.get("ihsg_ma", 50) * 2))

    return StockData(
        code=code,
        ohlcv=ohlcv,
        done_offer_value=done["done_offer_value"],
        done_bid_value=done["done_bid_value"],
        broker_summary=broker_agg,
        broker_daily_net=daily_net,
        closing_bid_volume=queue["bid_volume"],
        closing_offer_volume=queue["offer_volume"],
        foreign_net_value=fetch_foreign_net(client, code, date.isoformat()),
        ihsg_close=ihsg["close"] if not ihsg.empty else None,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Markup Radar — daily EOD scan")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="jangan kirim Telegram")
    args = ap.parse_args()

    cfg = load_settings()
    date = dt.date.fromisoformat(args.date)
    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url)
    store = Store(cfg.db_path)
    narrative_cfg = cfg.narrative

    actionable: list[dict] = []
    for code in cfg.watchlist:
        try:
            data = build_stock_data(client, code, date, cfg)
            signals = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
            state = classify(signals, cfg.thresholds)
            conf = confidence_markup_start(signals, cfg.score_weights)
        except Exception as exc:  # noqa: BLE001 — jangan gagalkan seluruh batch
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
            continue

        store.save_result(args.date, code, state, conf, signals)
        print(f"{code:6s} {state:22s} conf={conf}")

        if state in cfg.alert_states:
            item = {"code": code, "state": state, "confidence": conf, "signals": signals}
            if narrative_cfg.get("enabled"):
                item["narrative"] = generate_narrative(
                    code, state, signals,
                    api_key=cfg.anthropic_api_key,
                    model=narrative_cfg.get("model", "claude-opus-4-8"),
                )
            actionable.append(item)

    msg = format_alert(args.date, actionable)
    print("\n" + msg)

    if not args.dry_run and actionable:
        try:
            send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
            print("\n[OK] alert terkirim ke Telegram.")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[WARN] gagal kirim Telegram: {exc}", file=sys.stderr)

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
