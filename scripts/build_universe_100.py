#!/usr/bin/env python3
"""Builder universe TETAP 100 emiten untuk watchlist_today.txt (one-time/re-run manual).

Kebutuhan (keputusan user 18 Jul 2026, proyek Stock Advisor):
    - Watchlist scan harian ditetapkan 100 emiten: 50 existing dipertahankan
      apa adanya + ~50 pick baru.
    - Pick baru tersebar merata di 11 sektor IDX, dipilih yang fundamentalnya
      paling layak (proxy: roa/per via formula screener) dengan harga
      terjangkau 100 < close < 2500 rupiah.
    - Setelah build, `screener.enabled: false` di settings.yaml supaya jadwal
      EOD tidak menimpa list tetap ini (screen_universe.py exit 0 tanpa
      menulis bila disabled — watchlist terjaga).

Desain hemat-throttle (pelajaran run pertama 18 Jul: /screener/screen 429
bahkan pada 1 call/menit — budget endpoint itu sangat kecil):
    - Peta sektor SELURUH bursa dari GET /analysis/list/stock (1 call,
      1203 emiten ber-field `sector`) — bukan 11 loose screen.
    - Screener hanya 3 call TOTAL (satu per tier, semua 11 kategori digabung
      dalam satu POST — pola yang sama dengan screen_universe produksi),
      jeda antar call panjang + backoff sabar saat 429.

Mekanisme pemilihan: 3 tier formula ketat→longgar (T1 roa>5 & per<15,
T2 roa>2 & per<20, T3 roa>0) — kandidat tier ketat diprioritaskan. Alokasi
antar sektor water-filling: sektor paling defisit terhadap target (≈100/11)
diisi lebih dulu; 50 existing TIDAK pernah dibuang (sektor overweight hanya
berhenti menerima pick baru).

Pakai:
    python scripts/build_universe_100.py             # build & tulis
    python scripts/build_universe_100.py --dry-run   # hitung & cetak saja
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from markup_radar.config import load_codes_file, load_settings  # noqa: E402
from screen_universe import extract_codes, write_watchlist_atomic  # noqa: E402

# Nama sektor persis seperti field `sector` di /analysis/list/stock
# (diverifikasi live 18 Jul 2026: 11 nilai, 1203 emiten).
SECTORS = [
    "Energi",
    "Barang Baku",
    "Perindustrian",
    "Barang Konsumen Primer",
    "Barang Konsumen Non-Primer",
    "Kesehatan",
    "Keuangan",
    "Properti & Real Estat",
    "Teknologi",
    "Infrastruktur",
    "Transportasi & Logistik",
]

# Kategori IDX untuk request screener (COMPOSITE balik kosong — lihat
# screen_universe.py); semua kategori digabung dalam SATU call per tier.
CATEGORIES = [
    "IDXENERGY", "IDXBASIC", "IDXINDUST", "IDXNONCYC", "IDXCYCLIC", "IDXHEALTH",
    "IDXFINANCE", "IDXPROPERT", "IDXTECHNO", "IDXINFRA", "IDXTRANS",
]

# Tier formula pick baru: harga 100<close<2500 di semua tier; likuiditas &
# kualitas fundamental dilonggarkan bertahap supaya sektor tipis tetap terisi.
# per > 0 sekaligus mengecualikan emiten rugi (EPS negatif → PER negatif).
TIERS = [
    ("T1", "close > 100 and close < 2500 and value > 2000000000 and roa > 5 and per > 0 and per < 15"),
    ("T2", "close > 100 and close < 2500 and value > 1000000000 and roa > 2 and per > 0 and per < 20"),
    ("T3", "close > 100 and close < 2500 and value > 500000000 and roa > 0 and per > 0"),
]


def fetch_sector_map(api_key: str, base_url: str) -> dict[str, str]:
    """GET /analysis/list/stock → {code: sector} seluruh bursa (1 call)."""
    r = requests.get(
        f"{base_url.rstrip('/')}/analysis/list/stock",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=60,
    )
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"list/stock HTTP {r.status_code}: {r.text[:200]}")
    rows = r.json()
    if isinstance(rows, dict):
        rows = rows.get("data", rows.get("items", []))
    out: dict[str, str] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("code") and row.get("sector"):
            out[str(row["code"]).strip().upper()] = str(row["sector"]).strip()
    return out


def screen_tier(api_key: str, base_url: str, formula: str,
                *, backoffs: tuple[int, ...] = (0, 120, 300),
                timeout: float = 90.0) -> list[str]:
    """Satu POST /screener/screen (semua kategori) dengan backoff sabar.

    Endpoint ini throttle-nya sangat ketat (429 bahkan di ~1 call/menit saat
    budget habis) → backoff default 2 & 5 menit sebelum menyerah.
    """
    url = f"{base_url.rstrip('/')}/screener/screen"
    for attempt, wait in enumerate(backoffs):
        if wait:
            print(f"    [retry] 429 throttle — tunggu {wait}s…", file=sys.stderr)
            time.sleep(wait)
        resp = requests.post(
            url,
            json={"formula": formula, "category": CATEGORIES},
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 429 and attempt < len(backoffs) - 1:
            continue
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"screener HTTP {resp.status_code}: {resp.text[:200]}")
        return extract_codes(resp.json())
    raise RuntimeError("screener: 429 terus setelah semua backoff")


def allocate(existing_by_sector: dict[str, list[str]],
             candidates: dict[str, list[str]],
             total: int, n_existing: int) -> dict[str, list[str]]:
    """Water-filling: isi sektor paling defisit dulu sampai slot baru habis.

    Return picks-baru per sektor (urutan = prioritas tier). Existing tidak
    pernah dibuang — sektor overweight hanya berhenti menerima pick baru.
    """
    slots = total - n_existing
    if slots <= 0:
        return {}

    base, rem = divmod(total, len(SECTORS))
    # Sisa pembagian (100 % 11 = 1) diberikan ke sektor berkandidat terbanyak.
    order_by_pool = sorted(SECTORS, key=lambda s: len(candidates.get(s, [])), reverse=True)
    target = {s: base for s in SECTORS}
    for s in order_by_pool[:rem]:
        target[s] += 1

    count = {s: len(existing_by_sector.get(s, [])) for s in SECTORS}
    queue = {s: list(candidates.get(s, [])) for s in SECTORS}
    picks: dict[str, list[str]] = {s: [] for s in SECTORS}

    while slots > 0:
        eligible = [s for s in SECTORS if queue[s]]
        if not eligible:
            break
        # Sektor defisit terbesar; seri → pool kandidat terbanyak.
        s = max(eligible, key=lambda x: (target[x] - count[x], len(queue[x])))
        if target[s] - count[s] <= 0:
            # Semua target terpenuhi tapi slot sisa (existing sangat skew) →
            # lanjut round-robin merata: sektor ber-count terkecil dulu.
            s = min(eligible, key=lambda x: (count[x], -len(queue[x])))
        picks[s].append(queue[s].pop(0))
        count[s] += 1
        slots -= 1
    return picks


def main() -> int:
    ap = argparse.ArgumentParser(description="Build universe tetap 100 emiten")
    ap.add_argument("--total", type=int, default=100, help="ukuran universe akhir")
    ap.add_argument("--out", default="watchlist_today.txt", help="file watchlist")
    ap.add_argument("--manifest", default="data/universe_100.json",
                    help="manifest audit (code/sector/tier/source)")
    ap.add_argument("--tier-gap", type=float, default=90.0,
                    help="jeda antar call screener (dtk; endpoint throttle ketat)")
    ap.add_argument("--dry-run", action="store_true", help="hitung & cetak, jangan tulis")
    args = ap.parse_args()

    cfg = load_settings()
    if not cfg.invezgo_api_key:
        print("[ERROR] INVEZGO_API_KEY belum di-set.", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    existing = load_codes_file(out_path) if out_path.exists() else []
    print(f"[info] existing watchlist: {len(existing)} kode (semua dipertahankan)")

    # ---- 1) Peta sektor seluruh bursa (1 call) -----------------------------
    sector_of = fetch_sector_map(cfg.invezgo_api_key, cfg.invezgo_base_url)
    print(f"[info] peta sektor: {len(sector_of)} emiten dari list/stock")

    existing_by_sector: dict[str, list[str]] = {}
    unknown: list[str] = []  # tetap dipertahankan; tak dihitung ke kuota sektor
    for code in existing:
        cat = sector_of.get(code)
        if cat in SECTORS:
            existing_by_sector.setdefault(cat, []).append(code)
        else:
            unknown.append(code)
    if unknown:
        print(f"[warn] {len(unknown)} kode existing tanpa sektor: {', '.join(unknown)}")

    # ---- 2) Kandidat baru: 3 call screener (tier ketat → longgar) ----------
    candidates: dict[str, list[str]] = {s: [] for s in SECTORS}
    tier_of: dict[str, str] = {}
    existing_set = set(existing)
    for i, (tier_name, formula) in enumerate(TIERS):
        if i:
            print(f"[info] jeda {args.tier_gap:.0f}s sebelum tier berikutnya…")
            time.sleep(args.tier_gap)
        print(f"[info] tier {tier_name}: {formula}")
        try:
            codes = screen_tier(cfg.invezgo_api_key, cfg.invezgo_base_url, formula)
        except Exception as exc:  # noqa: BLE001 — satu tier gagal ≠ build batal
            print(f"  {tier_name} GAGAL: {exc}", file=sys.stderr)
            continue
        fresh = 0
        for c in codes:
            if c in existing_set or c in tier_of:
                continue
            sector = sector_of.get(c)
            if sector not in SECTORS:
                continue  # tak bisa dibalance tanpa sektor
            tier_of[c] = tier_name
            candidates[sector].append(c)
            fresh += 1
        print(f"  {tier_name}: {len(codes)} lolos formula, +{fresh} kandidat baru")

    # ---- 3) Alokasi water-filling ------------------------------------------
    picks = allocate(existing_by_sector, candidates, args.total, len(existing))
    new_codes = [c for s in SECTORS for c in picks[s]]
    final = existing + new_codes

    print(f"\n[hasil] {len(existing)} existing + {len(new_codes)} baru "
          f"= {len(final)} kode (target {args.total})")
    print(f"{'Sektor':<28}{'Existing':>9}{'Baru':>6}{'Total':>7}  Pick baru")
    for s in SECTORS:
        n_e = len(existing_by_sector.get(s, []))
        p = picks.get(s, [])
        tampil = ", ".join(f"{c}({tier_of.get(c, '?')})" for c in p) or "—"
        print(f"{s:<28}{n_e:>9}{len(p):>6}{n_e + len(p):>7}  {tampil}")
    if unknown:
        print(f"{'(tanpa sektor — existing)':<28}{len(unknown):>9}{0:>6}{len(unknown):>7}")

    if len(new_codes) < 10:
        print("[ERROR] pick baru < 10 — kemungkinan screener bermasalah; "
              "file TIDAK ditulis.", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[info] dry-run: tidak menulis file.")
        return 0

    # ---- 4) Tulis: backup → watchlist atomik → manifest --------------------
    if out_path.exists():
        backup = out_path.with_name(
            f"{out_path.stem}.backup-{dt.date.today().isoformat()}{out_path.suffix}")
        shutil.copy2(out_path, backup)
        # ASCII saja di print (console Windows cp1252 tak kenal '→' — crash
        # nyata 18 Jul: build sukses tapi mati di print ini sebelum menulis).
        print(f"[OK] backup watchlist lama -> {backup}")

    write_watchlist_atomic(
        final, out_path,
        formula=f"UNIVERSE TETAP {len(final)} (build_universe_100.py; "
                f"screener harian dinonaktifkan)")

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "total": len(final),
        "tiers": dict(TIERS),
        "codes": [
            {"code": c,
             "sector": sector_of.get(c),
             "tier": tier_of.get(c),
             "source": "existing" if c in existing_set else "new"}
            for c in final
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] {len(final)} kode -> {out_path} | manifest -> {manifest_path}")
    print("[NEXT] set `screener.enabled: false` di config/settings.yaml "
          "supaya jadwal EOD tidak menimpa universe tetap ini.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
