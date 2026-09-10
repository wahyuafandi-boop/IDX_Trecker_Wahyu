"""Normalisasi OHLCV harian (S6 RVOL, S7 close-in-range).

Catatan kapasitas: endpoint `stock_chart` Invezgo membatasi ~6 bln per request.
Rentang terlalu lebar -> HTTP 422 (Unprocessable Entity), BUKAN auto-truncate
(diverifikasi 2026-06-23 saat F8: from=2024..to=2026 -> 422). Maka `fetch_ohlcv`
menarik per-chunk dengan JENDELA TERBATAS (`from` = `to` - window_days, bukan
date_from asli) lalu menggeser `to` mundur & men-stitch. Tanggal Invezgo berupa
UTC midnight ('...T00:00:00.000Z') -> dinormalisasi ke tz-naive (tanpa geser tanggal).
"""

from __future__ import annotations

import sys

import pandas as pd

from markup_radar.ingest._history import fetch_windowed
from markup_radar.ingest.client import InvezgoClient

_COLS = ["date", "open", "high", "low", "close", "volume"]


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _fetch_ohlcv_chunk(client: InvezgoClient, code: str, date_from: str, date_to: str) -> pd.DataFrame:
    """Satu request stock_chart -> DataFrame[date, open, high, low, close, volume].

    Defensif terhadap variasi nama field (shape response perlu diverifikasi).
    """
    raw = client.stock_chart(code, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))

    records = []
    for r in rows:
        records.append(
            {
                "date": _pick(r, "date", "time", "timestamp"),
                "open": _pick(r, "open", "o"),
                "high": _pick(r, "high", "h"),
                "low": _pick(r, "low", "l"),
                "close": _pick(r, "close", "c"),
                "volume": _pick(r, "volume", "v", "vol"),
            }
        )
    df = pd.DataFrame.from_records(records, columns=_COLS)
    if df.empty:
        return df
    # UTC midnight ('Z') -> tz-naive tanpa geser tanggal.
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    # Invezgo kirim volume (dan kadang OHLC) sebagai STRING -> coerce ke angka.
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def detect_corporate_action(
    df: pd.DataFrame, *, drop_pct: float = -0.35, jump_pct: float = 0.60
) -> str | None:
    """Tanggal loncatan skala harga TERAKHIR (stock split / reverse split), atau None.

    Chart Invezgo TIDAK di-adjust mundur untuk aksi korporasi: harga pra-split
    dibiarkan apa adanya, jadi bar split muncul sebagai terjunan -50%..-95% dalam
    SATU hari — dan volume ikut meledak sebesar rasio split.

    Kenapa ambang -35%: auto-reject bawah (ARB) IDX maksimal 35% (pita harga
    50-200); penurunan close-to-close LEBIH DALAM dari itu tidak bisa lahir dari
    perdagangan biasa. Sisi atas +60% mengantisipasi reverse split (ambang longgar
    supaya ARA beruntun 25%+25% tidak salah tangkap).

    Kerusakan nyata yang dicegah (audit 2026-09-09): MLPT split 1:20 pada 21 Jul
    2026 -> engine membaca RVOL 341x & memicu MARKUP_START dua malam berturut,
    dengan level resistance 29.500 lawan support 1.075 (stop 22%) yang dikirim ke
    Telegram apa adanya. RAJA (-81%), RMKE (-82%), CYBR (-50%), COCO (-38%) kena
    pola yang sama di periode yang sama.

    Mengembalikan tanggal event TERAKHIR (kalau ada beberapa) sebagai string.
    """
    if df is None or df.empty or len(df) < 2:
        return None
    close = pd.to_numeric(df["close"], errors="coerce")
    ret = close.pct_change()
    hit = df.index[(ret < drop_pct) | (ret > jump_pct)]
    if len(hit) == 0:
        return None
    return str(df["date"].iloc[int(hit[-1])])[:10]


def trim_at_corporate_action(
    df: pd.DataFrame, *, drop_pct: float = -0.35, jump_pct: float = 0.60
) -> tuple[pd.DataFrame, str | None]:
    """Buang bar SEBELUM aksi korporasi supaya semua bar sebanding skalanya.

    Bukan meng-adjust mundur (rasio split tak tersedia dari endpoint chart, dan
    menebaknya dari rasio harga rawan salah) melainkan MEMOTONG: sisakan bar sejak
    hari event. Konsekuensinya histori jadi pendek untuk beberapa minggu -> RVOL/
    MA/donchian tak cukup data -> caller WAJIB memeriksa jumlah bar dan melewati
    saham itu, bukan menghitung di atas data tipis (lihat run_daily.MIN_BARS).

    Return (df_terpotong, tanggal_event). Tanpa event -> (df asli, None).
    """
    ev = detect_corporate_action(df, drop_pct=drop_pct, jump_pct=jump_pct)
    if ev is None:
        return df, None
    keep = df[df["date"].astype(str).str[:10] >= ev]
    return keep.reset_index(drop=True), ev


def fetch_ohlcv(
    client: InvezgoClient,
    code: str,
    date_from: str,
    date_to: str,
    *,
    max_chunks: int = 24,
    window_days: int = 120,
    trim_corporate_action: bool = True,
) -> pd.DataFrame:
    """Tarik OHLCV harian [date_from..date_to], stitch antar-chunk via fetch_windowed
    (jendela <= window_days < cap server ~6 bln; degrade rapi di horizon histori).

    `trim_corporate_action` (default aktif) memotong bar pra-split supaya sinyal
    tak dihitung dari harga dua skala berbeda — lihat trim_at_corporate_action.
    Set False bila memang butuh deret mentah apa adanya.
    """
    df = fetch_windowed(
        lambda f, t: _fetch_ohlcv_chunk(client, code, f, t),
        date_from, date_to,
        label=f"fetch_ohlcv {code}", columns=_COLS,
        max_chunks=max_chunks, window_days=window_days,
    )
    if trim_corporate_action and not df.empty:
        n_before = len(df)
        df, ev = trim_at_corporate_action(df)
        if ev:
            print(f"[WARN] {code}: aksi korporasi terdeteksi {ev} (harga pra-event "
                  f"beda skala) -> {n_before - len(df)} bar lama dibuang, sisa "
                  f"{len(df)}.", file=sys.stderr)
    return df
