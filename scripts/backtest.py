#!/usr/bin/env python3
"""Phase 5 — Backtesting & threshold tuning.

Replay histori watchlist, laporkan akurasi per state, dan (opsional)
grid-search threshold MARKUP_START.

    python scripts/backtest.py --from 2024-01-01 --to 2026-06-01
    python scripts/backtest.py --from 2024-01-01 --to 2026-06-01 --tune
    python scripts/backtest.py --code BBRI --horizon 10 --target-up 0.07

Catatan: butuh INVEZGO_API_KEY dan data historis (API-heavy untuk done detail).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.backtest import load_history, replay, summarize, tune
from markup_radar.config import load_settings
from markup_radar.ingest import InvezgoClient
from markup_radar.store import HistoryCache


def main() -> int:
    ap = argparse.ArgumentParser(description="Markup Radar — backtest (Phase 5)")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--code", help="batasi ke 1 saham (default: seluruh watchlist)")
    ap.add_argument("--horizon", type=int, default=5, help="hari forward return")
    ap.add_argument("--target-up", type=float, default=0.05, help="ambang markup sukses")
    ap.add_argument("--target-down", type=float, default=0.05, help="ambang distribusi sukses")
    ap.add_argument("--tune", action="store_true", help="grid-search done_ratio_markup")
    ap.add_argument("--no-cache", action="store_true", help="abaikan cache SQLite (selalu tarik API)")
    args = ap.parse_args()

    cfg = load_settings()
    client = InvezgoClient(
        cfg.invezgo_api_key, cfg.invezgo_base_url, rate_limit_per_min=cfg.rate_limit_per_min
    )
    cache = None if args.no_cache else HistoryCache(cfg.db_path)
    codes = [args.code] if args.code else cfg.watchlist

    all_results = []
    for code in codes:
        print(f"[load] {code} {args.date_from}..{args.date_to}"
              f"{' (cache)' if cache else ''}", file=sys.stderr)
        try:
            ds = load_history(client, code, args.date_from, args.date_to, cache)
            res = replay(ds, cfg.thresholds, cfg.windows, top_n=cfg.broker_top_n, horizon=args.horizon)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
            continue
        if not res.empty:
            all_results.append(res)
        if args.tune and args.code:
            print("\n=== Threshold tuning (done_ratio_markup) ===")
            print(tune(ds, cfg.thresholds, cfg.windows, horizon=args.horizon,
                       target_up=args.target_up, top_n=cfg.broker_top_n).to_string(index=False))

    if cache:
        cache.close()

    if not all_results:
        print("Tidak ada hasil (cek API key / ketersediaan data historis).")
        return 1

    results = pd.concat(all_results, ignore_index=True)
    report = summarize(results, target_up=args.target_up, target_down=args.target_down)

    print(f"\n=== Akurasi per state (horizon={args.horizon}d, "
          f"target_up={args.target_up:.0%}, n_total={len(results)}) ===")
    print(report.to_string(index=False))
    print("\nGunakan hit_rate + n untuk men-tune threshold di config/settings.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
