#!/usr/bin/env python3
"""Phase 0 — Verifikasi data Invezgo (spec §7 GATING QUESTION, §8 checklist).

Jalankan SEBELUM full build untuk menjawab:
  "Apakah momentum-chart Invezgo mengekspos done-by-bid/offer (buy vs sell),
   bukan cuma net buy/sell broker?"

Skrip ini menarik sample 1 saham dan menampilkan struktur response tiap
endpoint, supaya bisa di-map ke parser di src/markup_radar/ingest/.

    python scripts/verify_data.py --code BBCA --date 2026-06-16
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings
from markup_radar.ingest import InvezgoClient, InvezgoError


def _show(title: str, fn) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
    try:
        data = fn()
    except InvezgoError as exc:
        print(f"  [ERROR] {exc}")
        return
    sample = data[:2] if isinstance(data, list) else data
    print(json.dumps(sample, indent=2, default=str)[:2000])
    if isinstance(data, list):
        print(f"  ... ({len(data)} rows total)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="BBCA")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args()

    cfg = load_settings()
    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url)
    code, date = args.code, args.date
    week_ago = (dt.date.fromisoformat(date) - dt.timedelta(days=7)).isoformat()

    print(f"Verifikasi data Invezgo — {code} @ {date}")

    # ⭐ GATING QUESTION: cek apakah ada breakdown buy/sell (done offer/bid).
    _show("S1/S2 momentum-chart (done by offer/bid?)", lambda: client.momentum_chart(code, date))
    _show("S3/S4 broker summary (net buy/sell per broker)",
          lambda: client.broker_summary_stock(code, week_ago, date))
    _show("S5 order book (closing bid/offer queue)", lambda: client.order_book(code))
    _show("S6/S7 stock chart OHLCV (TODO verify path)",
          lambda: client.stock_chart(code, week_ago, date))
    _show("S8 top foreign", lambda: client.top_foreign(date))
    _show("API usage / quota", client.api_usage)

    print("\nChecklist (spec §8): catat field done-at-bid/offer, granularitas, "
          "histori, rate limit, dan JSON shape tiap endpoint di atas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
