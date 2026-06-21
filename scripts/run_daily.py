#!/usr/bin/env python3
"""Entrypoint EOD: scan watchlist -> sinyal -> klasifikasi -> store -> alert.

Contoh:
    python scripts/run_daily.py                 # tanggal hari ini
    python scripts/run_daily.py --date 2026-06-16
    python scripts/run_daily.py --dry-run       # tanpa kirim Telegram
    python scripts/run_daily.py --codes BBCA,BBRI   # override watchlist YAML

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
from markup_radar.config import load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import (
    fetch_broker_daily_net,
    fetch_closing_queue,
)
from markup_radar.ingest.done_client import (
    fetch_done_breakdown,
    latest_available_done_date,
)
from markup_radar.ingest.ihsg_client import fetch_ihsg
from markup_radar.ingest.ohlc_client import fetch_ohlcv
from markup_radar.narrative import generate_narrative
from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals
from markup_radar.store import Store, build_sink


def _date_range(end: dt.date, days_back: int) -> tuple[str, str]:
    start = end - dt.timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def build_stock_data(
    client: InvezgoClient,
    code: str,
    date: dt.date,
    cfg,
    *,
    ihsg_close,
) -> StockData:
    """Tarik & normalisasi data EOD per saham (4 call/saham).

    IHSG sudah ditarik 1x per run dan di-inject ke sini.

    S4 broker_concentration & S8 foreign TIDAK di-fetch: keduanya tidak dipakai
    classify()/confidence (plumbed-but-unused, audit 2026-06-21), jadi
    fetch_broker_summary (dulu call #5) & top/foreign di-skip demi hemat kuota.
    Field StockData terkait default (concentration/foreign_net = 0). Bila S4/S8
    nanti di-wire ke scoring, tambah lagi fetch-nya di sini.
    """
    windows = cfg.windows
    ohlc_from, ohlc_to = _date_range(date, max(windows.get("volume_ma", 20) * 2, 60))
    streak_lb = windows.get("broker_streak_lookback", 5)

    ohlcv = fetch_ohlcv(client, code, ohlc_from, ohlc_to)              # 1
    done = fetch_done_breakdown(client, code, date.isoformat())       # 2
    queue = fetch_closing_queue(client, code)                         # 3
    # Streak S3 dari 1 call inventory-chart (bukan loop per hari).
    daily_net = fetch_broker_daily_net(client, code, *_date_range(date, streak_lb))  # 4

    return StockData(
        code=code,
        ohlcv=ohlcv,
        done_offer_value=done["done_offer_value"],
        done_bid_value=done["done_bid_value"],
        broker_daily_net=daily_net,
        closing_bid_volume=queue["bid_volume"],
        closing_offer_volume=queue["offer_volume"],
        ihsg_close=ihsg_close,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Markup Radar — daily EOD scan")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="jangan kirim Telegram")
    ap.add_argument(
        "--codes",
        nargs="+",
        metavar="CODE",
        help="override watchlist untuk run ini, mis. --codes BBCA,BBRI atau "
        "--codes BBCA BBRI. Kalau tidak diberikan, pakai watchlist di "
        "config/settings.yaml.",
    )
    args = ap.parse_args()

    cfg = load_settings()
    if args.codes:
        codes = parse_codes(args.codes)
        if codes:
            cfg.raw["watchlist"] = codes
            print(f"[info] watchlist override (--codes): {', '.join(codes)}",
                  file=sys.stderr)
        else:
            print("[WARN] --codes tidak berisi kode valid; "
                  "fallback ke watchlist config.", file=sys.stderr)
    client = InvezgoClient(
        cfg.invezgo_api_key,
        cfg.invezgo_base_url,
        rate_limit_per_min=cfg.rate_limit_per_min,
    )

    # Resolusi tanggal scan: done (momentum-chart) telat ~1 hari bursa & kosong di
    # libur/weekend. Pakai tanggal terakhir yang sudah ada data done agar MARKUP
    # bisa terhitung (kalau hari ini belum siap, mundur). Aman utk swing 20-hari.
    scan_date_str = args.date
    if cfg.watchlist:
        resolved = latest_available_done_date(client, cfg.watchlist[0], args.date)
        if resolved != args.date:
            print(f"[info] data done belum tersedia utk {args.date}; "
                  f"pakai tanggal terakhir berdata: {resolved}", file=sys.stderr)
            scan_date_str = resolved
    date = dt.date.fromisoformat(scan_date_str)

    store = Store(cfg.db_path)
    narrative_cfg = cfg.narrative
    # Mirror persisten ke Google Sheets (histori numpuk lintas run GH Actions).
    # None bila sink mati (fitur off / tak ada spreadsheet_id / kredensial / lib).
    sink = build_sink(cfg)
    if sink is not None:
        print("[info] mirror Google Sheets aktif.", file=sys.stderr)

    # Data market-wide: IHSG ditarik 1x per run, dipakai semua saham (hemat kuota).
    ihsg = fetch_ihsg(client, *_date_range(date, cfg.windows.get("ihsg_ma", 50) * 2))
    ihsg_close = ihsg["close"] if not ihsg.empty else None

    scan_log: list[dict] = []   # SEMUA kode (termasuk NEUTRAL) untuk mirror Sheets
    actionable: list[dict] = []
    for code in cfg.watchlist:
        try:
            data = build_stock_data(
                client, code, date, cfg, ihsg_close=ihsg_close,
            )
            signals = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
            state = classify(signals, cfg.thresholds)
            conf = confidence_markup_start(signals, cfg.score_weights)
        except Exception as exc:  # noqa: BLE001 — jangan gagalkan seluruh batch
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
            continue

        store.save_result(scan_date_str, code, state, conf, signals)
        print(f"{code:6s} {state:22s} conf={conf}")

        record = {
            "date": scan_date_str, "code": code, "state": state,
            "confidence": conf, "signals": signals, "narrative": "",
        }
        if state in cfg.alert_states:
            if narrative_cfg.get("enabled"):
                record["narrative"] = generate_narrative(
                    code, state, signals,
                    api_key=cfg.anthropic_api_key,
                    model=narrative_cfg.get("model", "claude-opus-4-8"),
                )
            actionable.append(record)
        scan_log.append(record)

    msg = format_alert(scan_date_str, actionable)
    print("\n" + msg)

    if not args.dry_run and actionable:
        try:
            send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
            print("\n[OK] alert terkirim ke Telegram.")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[WARN] gagal kirim Telegram: {exc}", file=sys.stderr)

    # Mirror histori ke Sheets — dilewati saat dry-run agar sheet tak terkotori
    # baris uji. SQLite lokal tetap menyimpan (audit), Sheets = histori lintas run.
    if sink is not None and scan_log:
        if args.dry_run:
            print("[info] dry-run: mirror Google Sheets dilewati.", file=sys.stderr)
        else:
            try:
                n = sink.append_results(scan_log)
                print(f"[OK] {n} baris di-mirror ke Google Sheets.")
            except Exception as exc:  # noqa: BLE001 — sink opsional
                print(f"[WARN] gagal mirror ke Sheets: {exc}", file=sys.stderr)

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
