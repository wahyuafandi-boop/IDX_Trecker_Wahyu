"""Normalisasi OHLCV harian (S6 RVOL, S7 close-in-range)."""

from __future__ import annotations

import pandas as pd

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def fetch_ohlcv(client: InvezgoClient, code: str, date_from: str, date_to: str) -> pd.DataFrame:
    """Tarik OHLCV harian -> DataFrame[date, open, high, low, close, volume].

    Defensif terhadap variasi nama field karena shape response perlu
    diverifikasi (lihat client.stock_chart TODO).
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
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)
