#!/usr/bin/env python3
"""Auto-screener: refresh `watchlist_today.txt` dari Invezgo screener.

Menggantikan langkah manual "copy-paste hasil screener Stockbit". Dijalankan di
VPS SEBELUM `run_eod.sh` (cron), sehingga scan harian selalu pakai watchlist
yang baru di-screen otomatis.

Endpoint (dikonfirmasi dari SDK resmi invezgo-go-sdk / invezgo-js-sdk + probe live):
    POST https://api.invezgo.com/screener/screen
    body: {"formula": "<str>", "category": ["IDXENERGY", ...]}
    auth: Authorization: Bearer <INVEZGO_API_KEY>
    sukses: HTTP 201 (NestJS) + list[{"code","matched","close"}] saham yg cocok
    catatan: category "COMPOSITE" balik KOSONG -> pakai 11 kategori sektor IDX.

Formula & category dibaca dari blok `screener` di config/settings.yaml (bisa
di-override via argumen). Contoh penggunaan:

    python scripts/screen_universe.py                  # tulis watchlist_today.txt
    python scripts/screen_universe.py --dry-run        # cetak saja, tak menulis
    python scripts/screen_universe.py --limit 40 --out watchlist_today.txt

SAFETY: file watchlist HANYA ditimpa bila screener balik >= --min-count kode.
Bila screener error / hasil kosong, file lama DIPERTAHANKAN (scan tetap jalan
pakai watchlist terakhir yang baik) dan script keluar dengan kode != 0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

import requests

# Izinkan import 'markup_radar' tanpa install (src layout), sama seperti run_daily.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.config import load_settings  # noqa: E402

# Default bila blok `screener` belum ada di settings.yaml. Diverifikasi via probe
# live API 2026-06-25: engine screener TIDAK punya fungsi indikator (ema/sma -> 400
# "Unknown function"), hanya variabel skalar (close/value/volume/open/high/low/
# change + rasio fundamental per/eps/roa) + operator 'and'/'or'. RVOL-spike & tren
# tetap dihitung run_daily.py. category "COMPOSITE" balik KOSONG di endpoint ini,
# jadi pakai gabungan 11 kategori sektor IDX = seluruh saham BEI (~900).
_DEFAULT_FORMULA = "close > 100 and value > 3000000000"
_DEFAULT_CATEGORY = [
    "IDXENERGY", "IDXBASIC", "IDXINDUST", "IDXNONCYC", "IDXCYCLIC", "IDXHEALTH",
    "IDXFINANCE", "IDXPROPERT", "IDXTECHNO", "IDXINFRA", "IDXTRANS",
]

# Kandidat nama field kode saham di response (shape map generik → robust).
_CODE_KEYS = ("code", "Kode", "kode", "symbol", "ticker", "Code", "stock", "emiten")


def extract_codes(payload, limit: int | None = None) -> list[str]:
    """Ambil daftar kode saham dari response screener (robust terhadap shape).

    Terima: list[dict] | {"data": list[dict]} | list[str]. UPPERCASE, dedupe,
    urutan dipertahankan, lalu dipotong ke `limit` bila diberikan.
    """
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("data", payload.get("items", payload.get("results", [])))
    if not isinstance(rows, list):
        return []

    out: list[str] = []
    for row in rows:
        code = None
        if isinstance(row, str):
            code = row
        elif isinstance(row, dict):
            for key in _CODE_KEYS:
                val = row.get(key)
                if isinstance(val, str) and val.strip():
                    code = val
                    break
        if not code:
            continue
        code = code.strip().upper()
        if code and code not in out:
            out.append(code)
    return out[:limit] if limit else out


def fetch_screened_codes(
    api_key: str, base_url: str, formula: str, category: list[str],
    *, timeout: float = 60.0, limit: int | None = None,
) -> list[str]:
    """Panggil POST /screener/screen lalu ekstrak kode. Raise pada error HTTP."""
    url = f"{base_url.rstrip('/')}/screener/screen"
    resp = requests.post(
        url,
        json={"formula": formula, "category": category},
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=timeout,
    )
    # API ber-basis NestJS: POST sukses balas 201 Created (BUKAN async/error),
    # jadi terima seluruh 2xx; hanya status >= 300 yang dianggap gagal. (Cek
    # '!= 200' lama menolak setiap respons sukses, termasuk yang berisi hasil.)
    if not 200 <= resp.status_code < 300:
        # Tampilkan body singkat untuk diagnosa: formula salah → 400/422,
        # burst throttle → 429 ThrottlerException (endpoint ini limitnya ketat).
        raise RuntimeError(f"screener HTTP {resp.status_code}: {resp.text[:300]}")
    return extract_codes(resp.json(), limit=limit)


def write_watchlist_atomic(codes: list[str], path: Path, *, formula: str) -> None:
    """Tulis watchlist secara atomik (tmp → os.replace) agar tak ada file separuh."""
    header = (
        f"# auto-generated oleh screen_universe.py @ "
        f"{dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"# {len(codes)} kode | formula: {formula}\n"
    )
    body = "\n".join(codes) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".watchlist_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(header + body)
        os.replace(tmp, path)  # atomic pada filesystem yang sama
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-screener → watchlist_today.txt")
    ap.add_argument("--out", default="watchlist_today.txt", help="file output")
    ap.add_argument("--formula", default=None, help="override formula screener")
    ap.add_argument("--category", nargs="+", default=None,
                    help="override kategori, mis. --category COMPOSITE")
    ap.add_argument("--limit", type=int, default=None, help="batas jumlah kode (cap)")
    ap.add_argument("--min-count", type=int, default=1,
                    help="minimum kode agar file ditimpa (SAFETY, default 1)")
    ap.add_argument("--dry-run", action="store_true", help="cetak saja, jangan menulis")
    args = ap.parse_args()

    cfg = load_settings()
    sc = cfg.raw.get("screener", {})
    if sc.get("enabled") is False:
        print("[info] screener.enabled=false di settings.yaml — dilewati.", file=sys.stderr)
        return 0

    formula = args.formula or sc.get("formula", _DEFAULT_FORMULA)
    category = args.category or sc.get("category", _DEFAULT_CATEGORY)
    limit = args.limit if args.limit is not None else sc.get("max_watchlist")
    out_path = Path(args.out)

    if not cfg.invezgo_api_key:
        print("[ERROR] INVEZGO_API_KEY belum di-set.", file=sys.stderr)
        return 2

    try:
        codes = fetch_screened_codes(
            cfg.invezgo_api_key, cfg.invezgo_base_url, formula, category, limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] screener gagal: {exc}", file=sys.stderr)
        print(f"[info] {out_path} TIDAK diubah (pakai watchlist terakhir).", file=sys.stderr)
        return 1

    print(f"[info] screener balik {len(codes)} kode: {', '.join(codes) or '(kosong)'}")

    if len(codes) < args.min_count:
        print(f"[ERROR] hasil ({len(codes)}) < min-count ({args.min_count}); "
              f"{out_path} TIDAK ditimpa (anti watchlist kosong).", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[info] dry-run: tidak menulis file.")
        return 0

    write_watchlist_atomic(codes, out_path, formula=formula)
    print(f"[OK] {len(codes)} kode ditulis ke {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
