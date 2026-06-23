"""Normalisasi OHLCV harian (S6 RVOL, S7 close-in-range).

Catatan kapasitas: endpoint `stock_chart` Invezgo membatasi ~6 bln per request.
Rentang terlalu lebar -> HTTP 422 (Unprocessable Entity), BUKAN auto-truncate
(diverifikasi 2026-06-23 saat F8: from=2024..to=2026 -> 422). Maka `fetch_ohlcv`
menarik per-chunk dengan JENDELA TERBATAS (`from` = `to` - window_days, bukan
date_from asli) lalu menggeser `to` mundur & men-stitch. Tanggal Invezgo berupa
UTC midnight ('...T00:00:00.000Z') -> dinormalisasi ke tz-naive (tanpa geser tanggal).
"""

from __future__ import annotations

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


def fetch_ohlcv(
    client: InvezgoClient,
    code: str,
    date_from: str,
    date_to: str,
    *,
    max_chunks: int = 24,
    window_days: int = 120,
) -> pd.DataFrame:
    """Tarik OHLCV harian [date_from..date_to], stitch antar-chunk via fetch_windowed
    (jendela <= window_days < cap server ~6 bln; degrade rapi di horizon histori)."""
    return fetch_windowed(
        lambda f, t: _fetch_ohlcv_chunk(client, code, f, t),
        date_from, date_to,
        label=f"fetch_ohlcv {code}", columns=_COLS,
        max_chunks=max_chunks, window_days=window_days,
    )
