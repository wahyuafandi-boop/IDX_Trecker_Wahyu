#!/usr/bin/env python3
"""Evaluasi forward-return sinyal produksi (SQLite) vs harga aktual Invezgo.

Baca semua hasil scan dari data/markup_radar.db (yang ditulis run_daily tiap
malam), tarik OHLCV tiap kode 1x (hemat kuota), lalu hitung per sinyal:
  - return +5/+10/+20 bar dari close tanggal sinyal (semua state; NEUTRAL =
    baseline pembanding),
  - khusus MARKUP_* yang punya levels: entry breakout terisi/tidak, lalu
    SL/TP/timeout (aturan konservatif sama dengan backtest).
Ringkasan win-rate & median per state dicetak di akhir — inilah rapor
"apakah sinyal yang dikirim ke Telegram ada edge-nya".

KUOTA: 1 call chart per kode unik di DB (bukan per sinyal). 50 kode ~ 50 call.

Contoh:
    python scripts/evaluate_signals.py                    # console saja
    python scripts/evaluate_signals.py --csv data/eval.csv
    python scripts/evaluate_signals.py --sheets           # + mirror ke Google Sheets
    python scripts/evaluate_signals.py --min-conf 60 --states MARKUP_START MARKUP_CONFIRMED
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings
from markup_radar.evaluate import HORIZONS, forward_metrics, levels_outcome, summarize_by_state
from markup_radar.ingest import InvezgoClient
from markup_radar.ingest.ohlc_client import fetch_ohlcv
from markup_radar.store import Store
from markup_radar.store.sheets import load_service_account_info, overwrite_worksheet

_DETAIL_HEADER = [
    "date", "code", "state", "confidence", "regime", "relative_strength",
    "alert_sent", "bars_fwd",
    "fwd_close_5", "fwd_close_10", "fwd_close_20", "fwd_max", "fwd_min",
    "trade_filled", "trade_exit", "trade_bars", "trade_ret",
]
_SUMMARY_HEADER = ["state", "n"] + [
    k for h in HORIZONS for k in (f"n_{h}", f"win_{h}", f"med_{h}")
]


def _fmt(v, pct: bool = False) -> str:
    """Format sel: NaN -> kosong; float -> 4dp / persen 1dp."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        return f"{v:+.1%}" if pct else f"{v:.4f}"
    return str(v)


def _cell(v):
    """Nilai untuk CSV/Sheets: NaN/None -> "" (NaN bukan JSON valid)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluasi forward sinyal tersimpan")
    ap.add_argument("--db", default=None, help="path SQLite (default: dari config)")
    ap.add_argument("--csv", metavar="PATH", help="tulis detail evaluasi ke CSV")
    ap.add_argument("--sheets", action="store_true",
                    help="mirror detail+ringkasan ke Google Sheets (worksheet "
                         "'evaluation' & 'eval_summary', ditulis-ulang tiap run)")
    ap.add_argument("--states", nargs="+", metavar="STATE",
                    help="filter state, mis. --states MARKUP_START MARKUP_CONFIRMED "
                         "(default: semua, NEUTRAL jadi baseline)")
    ap.add_argument("--min-conf", type=int, default=0, help="filter confidence minimum")
    args = ap.parse_args()

    cfg = load_settings()
    store = Store(args.db or cfg.db_path)
    rows_db = store.all_results()
    store.close()
    if args.states:
        wanted = {s.upper() for s in args.states}
        rows_db = [r for r in rows_db if r["state"] in wanted]
    if args.min_conf:
        rows_db = [r for r in rows_db if (r.get("confidence") or 0) >= args.min_conf]
    if not rows_db:
        print("[info] tidak ada sinyal di DB yang lolos filter — belum ada "
              "yang bisa dievaluasi.")
        return 0

    codes = sorted({r["code"] for r in rows_db})
    date_min = min(r["date"] for r in rows_db)
    date_from = (dt.date.fromisoformat(date_min) - dt.timedelta(days=7)).isoformat()
    date_to = dt.date.today().isoformat()
    print(f"[info] {len(rows_db)} sinyal, {len(codes)} kode unik "
          f"({date_min}..) — ~{len(codes)} call chart.", file=sys.stderr)

    if not cfg.invezgo_api_key:
        print("[ERROR] INVEZGO_API_KEY belum di-set di .env", file=sys.stderr)
        return 1
    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url,
                           rate_limit_per_min=cfg.rate_limit_per_min)

    frames: dict = {}
    for code in codes:
        try:
            frames[code] = fetch_ohlcv(client, code, date_from, date_to)
        except Exception as exc:  # noqa: BLE001 — satu kode gagal jangan gugurkan semua
            print(f"[WARN] {code}: gagal fetch OHLCV: {exc}", file=sys.stderr)

    detail: list[dict] = []
    for r in rows_db:
        ohlcv = frames.get(r["code"])
        fwd = forward_metrics(ohlcv, r["date"]) if ohlcv is not None else None
        if fwd is None:
            # Tanggal sinyal tak ada di frame (libur/kode gagal fetch) — skip.
            print(f"[WARN] {r['code']} {r['date']}: tanggal tak ada di chart, skip.",
                  file=sys.stderr)
            continue
        levels = json.loads(r["levels"]) if r.get("levels") else None
        trade = (levels_outcome(ohlcv, r["date"], levels) or {}) if levels else {}
        detail.append({
            "date": r["date"], "code": r["code"], "state": r["state"],
            "confidence": r.get("confidence") or 0,
            "regime": r.get("regime") or "",
            "relative_strength": r.get("relative_strength") or 0.0,
            "alert_sent": bool(r.get("alert_sent") or 0),
            **fwd,
            "trade_filled": trade.get("filled", ""),
            "trade_exit": trade.get("exit", ""),
            "trade_bars": trade.get("bars", ""),
            "trade_ret": trade.get("ret", ""),
        })

    # --- Console: detail ringkas + ringkasan per state ---
    print(f"\n{'date':10s} {'code':6s} {'state':22s} {'conf':>4s} {'sent':4s} "
          f"{'+5d':>7s} {'+10d':>7s} {'+20d':>7s} {'MFE':>7s} {'MAE':>7s}  trade")
    for d in detail:
        trade = (f"{d['trade_exit']}({_fmt(d['trade_ret'], pct=True)})"
                 if d["trade_exit"] else "")
        print(f"{d['date']:10s} {d['code']:6s} {d['state']:22s} "
              f"{d['confidence']:>4d} {'ya' if d['alert_sent'] else '-':4s} "
              f"{_fmt(d['fwd_close_5'], True):>7s} {_fmt(d['fwd_close_10'], True):>7s} "
              f"{_fmt(d['fwd_close_20'], True):>7s} {_fmt(d['fwd_max'], True):>7s} "
              f"{_fmt(d['fwd_min'], True):>7s}  {trade}")

    summary = summarize_by_state(detail)
    print(f"\n{'state':22s} {'n':>3s}" + "".join(
        f" {'n'+str(h):>4s} {'win'+str(h):>6s} {'med'+str(h):>7s}" for h in HORIZONS))
    for s in summary:
        line = f"{s['state']:22s} {s['n']:>3d}"
        for h in HORIZONS:
            line += (f" {s[f'n_{h}']:>4d} {_fmt(s[f'win_{h}'], True):>6s} "
                     f"{_fmt(s[f'med_{h}'], True):>7s}")
        print(line)
    print("\nBaca: win/med MARKUP_* harus MENGALAHKAN baseline NEUTRAL, bukan "
          "sekadar positif. n kecil = belum bisa disimpulkan, kumpulkan terus.")

    # --- CSV ---
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(_DETAIL_HEADER)
            for d in detail:
                w.writerow([_cell(d.get(k)) for k in _DETAIL_HEADER])
        print(f"\n[OK] detail ditulis ke {args.csv}")

    # --- Google Sheets (ditulis-ulang, idempoten) ---
    if args.sheets:
        sheets = cfg.sheets
        sid = os.getenv("MARKUP_RADAR_SHEET_ID") or sheets.get("spreadsheet_id", "")
        info = load_service_account_info()
        if not sid or not info:
            print("[WARN] --sheets diminta tapi spreadsheet_id/kredensial tak ada; "
                  "lewati.", file=sys.stderr)
        else:
            try:
                det_rows = [[_cell(d.get(k)) for k in _DETAIL_HEADER] for d in detail]
                sum_rows = [[_cell(s.get(k)) for k in _SUMMARY_HEADER] for s in summary]
                n1 = overwrite_worksheet(
                    sid, sheets.get("eval_worksheet", "evaluation"), info,
                    _DETAIL_HEADER, det_rows)
                n2 = overwrite_worksheet(
                    sid, sheets.get("eval_summary_worksheet", "eval_summary"), info,
                    _SUMMARY_HEADER, sum_rows)
                print(f"[OK] Sheets: {n1} baris evaluation + {n2} baris eval_summary.")
            except Exception as exc:  # noqa: BLE001 — mirror opsional
                print(f"[WARN] gagal tulis Sheets: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
