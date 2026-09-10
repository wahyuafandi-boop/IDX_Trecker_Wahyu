#!/usr/bin/env python3
"""Probe verifikasi endpoint insider/kepemilikan/calendar/posts Invezgo.

Path endpoint sudah dikonfirmasi dari invezgo-go-sdk (analysis.go + others.go),
tapi SHAPE response (nama field JSON) belum. Jalankan sekali (~7 call) untuk
mencatat shape sebelum parser di src/markup_radar/ingest/insider_client.py
dipakai produksi — pola sama seperti scripts/verify_data.py.

    python scripts/verify_insider.py                    # default 30 hari terakhir
    python scripts/verify_insider.py --code WINR --days 60
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
    print(json.dumps(sample, indent=2, default=str, ensure_ascii=False)[:2500])
    if isinstance(data, list):
        print(f"  ... ({len(data)} rows total)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="WINR", help="sample utk endpoint per-kode")
    ap.add_argument("--days", type=int, default=30, help="lookback hari")
    args = ap.parse_args()

    cfg = load_settings()
    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url)
    to = dt.date.today().isoformat()
    frm = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()

    print(f"Verifikasi endpoint insider/berita — {frm} s/d {to}, sample {args.code}")

    _show("insider market-wide (/analysis/shareholder-insider)",
          lambda: client.shareholder_insider(frm, to, limit=20))
    _show("perubahan kepemilikan >1% (/analysis/shareholder-one)",
          lambda: client.shareholder_one(frm, to, limit=10))
    _show("perubahan kepemilikan >5% (/analysis/shareholder-above)",
          lambda: client.shareholder_above(frm, to, limit=10))
    _show("calendar corporate action (/analysis/calendar)",
          lambda: client.calendar(limit=20))
    _show(f"calendar per kode ({args.code})",
          lambda: client.calendar(code=args.code, limit=10))
    _show(f"posts per emiten (/posts/space/{args.code})",
          lambda: client.stock_posts(args.code))
    _show("API usage / quota", client.api_usage)

    print("\nCatat: nama field, format tanggal, arti sign (buy/sell), slug "
          "kategori posts, dan bentuk paging — untuk parser insider_client.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
