#!/usr/bin/env python3
"""Live watch bid/offer (S5) selama market buka — dijalankan MANUAL saat kamu
hadir (pola 2 jam awal + 2 jam jelang tutup, 5 saham).

Beda dengan run_daily (EOD, sekali tembak): ini polling order book tiap N menit
dan tampilkan apakah antri beli (demand) lagi dominan = konfirmasi bandar mulai
markup, atau antri jual (supply) dominan / seimbang = masih ditahan.

KUOTA: 1 call/saham/siklus. 5 saham, interval 120s, 4 jam ~= 600 call/hari
(~12k/bulan, aman di paket Advance 30k). Interval di-floor 60s; pakai >=120s
untuk 5 saham. Tool ini cuma jalan saat kamu jalankan -> kuota terbatas otomatis.

Contoh:
    python scripts/live_watch.py --probe                 # 1x dump bentuk order_book (verifikasi)
    python scripts/live_watch.py                          # watch watchlist, interval 120s
    python scripts/live_watch.py --codes BBRI BMRI --interval 180
    python scripts/live_watch.py --once                  # 1 siklus lalu keluar
    python scripts/live_watch.py --telegram              # ping saat saham flip ke DEMAND
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.broker_client import fetch_closing_queue
from markup_radar.signals.price_volume import queue_imbalance, queue_verdict

_MIN_INTERVAL = 60          # floor keras biar tak menjebol kuota
_TAG = {
    "DEMAND_DOMINAN": "DEMAND >>",
    "SEIMBANG":       "seimbang",
    "SUPPLY_DOMINAN": "SUPPLY <<",
    "NO_DATA":        "no-data",
}


def _trend(curr: float, prev: float | None) -> str:
    if prev is None:
        return " "
    if curr > prev * 1.02:
        return "+"     # demand menguat
    if curr < prev * 0.98:
        return "-"     # demand melemah
    return "="


def _probe(client: InvezgoClient, codes: list[str]) -> int:
    """Tarik order_book 1x per kode lalu cetak bentuk mentah + hasil parse.
    Dipakai sekali (di hari bursa, setelah ada antrian) untuk verifikasi shape."""
    for code in codes:
        print(f"\n===== {code} : raw order_book =====")
        try:
            raw = client.order_book(code)
            print(json.dumps(raw, indent=2, ensure_ascii=False)[:2000])
            q = fetch_closing_queue(client, code)
            imb = queue_imbalance(q["bid_volume"], q["offer_volume"])
            print(f"-> parse: bid_lot={q['bid_volume']:.0f} offer_lot={q['offer_volume']:.0f} "
                  f"imbalance={imb:.2f} ({queue_verdict(imb)})")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {code}: {exc}")
    print("\nCek: apakah bid_lot/offer_lot masuk akal (bukan 0 / bukan ketuker)? "
          "Kalau shape beda, sesuaikan fetch_closing_queue.")
    return 0


def _cycle(client, codes, cfg, prev, last_verdict, *, send_tg, token, chat_id) -> None:
    from markup_radar.alert import send_telegram

    demand = float(cfg.thresholds.get("queue_imbalance_demand", 1.0))
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}]  demand>={demand:g}")
    print(f"  {'code':6s} {'bid_lot':>10s} {'offer_lot':>10s} {'imb':>6s}  {'verdict':10s} trend")
    for code in codes:
        try:
            q = fetch_closing_queue(client, code)
            imb = queue_imbalance(q["bid_volume"], q["offer_volume"])
        except Exception as exc:  # noqa: BLE001 — jangan hentikan loop
            print(f"  {code:6s} [WARN] {exc}")
            continue
        verdict = queue_verdict(imb, demand=demand)
        arrow = _trend(imb, prev.get(code))
        prev[code] = imb
        print(f"  {code:6s} {q['bid_volume']:>10.0f} {q['offer_volume']:>10.0f} "
              f"{imb:>6.2f}  {_TAG[verdict]:10s}  {arrow}")

        # Telegram opsional: hanya saat TRANSISI ke DEMAND_DOMINAN (hindari spam).
        if send_tg and verdict == "DEMAND_DOMINAN" and last_verdict.get(code) != "DEMAND_DOMINAN":
            try:
                send_telegram(token, chat_id,
                              f"[LIVE] {code}: antri beli dominan (imbalance {imb:.2f}) "
                              f"-> konfirmasi markup intraday. Pantau & kelola risiko sendiri.")
            except Exception as exc:  # noqa: BLE001
                print(f"  [WARN] telegram: {exc}")
        last_verdict[code] = verdict


def main() -> int:
    ap = argparse.ArgumentParser(description="Live watch bid/offer (S5) saat market buka")
    ap.add_argument("--codes", nargs="+", metavar="CODE", help="override watchlist")
    ap.add_argument("--interval", type=int, default=120, help="detik antar siklus (floor 60)")
    ap.add_argument("--probe", action="store_true", help="dump bentuk order_book 1x lalu keluar")
    ap.add_argument("--once", action="store_true", help="1 siklus lalu keluar")
    ap.add_argument("--duration", type=int, default=0, help="auto-stop setelah N menit (0=manual)")
    ap.add_argument("--telegram", action="store_true", help="ping saat flip ke DEMAND_DOMINAN")
    args = ap.parse_args()

    cfg = load_settings()
    codes = parse_codes(args.codes) if args.codes else cfg.watchlist
    if not codes:
        print("[ERROR] tidak ada kode untuk dipantau.", file=sys.stderr)
        return 1
    if not cfg.invezgo_api_key:
        print("[ERROR] INVEZGO_API_KEY belum di-set di .env", file=sys.stderr)
        return 1

    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url,
                           rate_limit_per_min=cfg.rate_limit_per_min)

    if args.probe:
        return _probe(client, codes)

    interval = max(args.interval, _MIN_INTERVAL)
    if interval != args.interval:
        print(f"[info] interval dinaikkan ke {interval}s (floor kuota).", file=sys.stderr)
    calls_hr = int(len(codes) * 3600 / interval)
    print(f"Live watch: {', '.join(codes)} | interval {interval}s | ~{calls_hr} call/jam")
    try:
        print(f"[info] kuota API: {json.dumps(client.api_usage())}", file=sys.stderr)
    except Exception:  # noqa: BLE001 — usage opsional
        pass
    print("Ctrl+C untuk berhenti.")

    prev: dict[str, float] = {}
    last_verdict: dict[str, str] = {}
    deadline = time.monotonic() + args.duration * 60 if args.duration else None
    try:
        while True:
            _cycle(client, codes, cfg, prev, last_verdict,
                   send_tg=args.telegram,
                   token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id)
            if args.once:
                break
            if deadline and time.monotonic() >= deadline:
                print("\n[info] durasi tercapai, berhenti.")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[info] dihentikan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
