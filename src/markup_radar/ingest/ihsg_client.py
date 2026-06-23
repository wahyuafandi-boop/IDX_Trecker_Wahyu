"""Normalisasi IHSG untuk filter market (S9)."""

from __future__ import annotations

import pandas as pd

from markup_radar.ingest._history import fetch_windowed
from markup_radar.ingest.client import InvezgoClient

IHSG_CODE = "COMPOSITE"  # TODO(verify): kode index IHSG di Invezgo (mis. 'COMPOSITE'/'IHSG'); endpoint = /analysis/chart/index/{code}

_COLS = ["date", "close"]


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _fetch_ihsg_chunk(client: InvezgoClient, date_from: str, date_to: str) -> pd.DataFrame:
    """Satu request index_chart -> DataFrame[date, close]."""
    raw = client.index_chart(IHSG_CODE, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))

    records = [
        {
            "date": _pick(r, "date", "time", "timestamp"),
            "close": _pick(r, "close", "c"),
        }
        for r in rows
    ]
    df = pd.DataFrame.from_records(records, columns=_COLS)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # index chart kadang kirim baris duplikat per tanggal -> dedupe.
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def fetch_ihsg(
    client: InvezgoClient, date_from: str, date_to: str,
    *, max_chunks: int = 24, window_days: int = 120,
) -> pd.DataFrame:
    """IHSG harian -> DataFrame[date, close] untuk MA50/regime (S9), stitch antar-chunk
    via fetch_windowed (endpoint index_chart sama-sama ber-cap ~6 bln + horizon ~2 thn)."""
    return fetch_windowed(
        lambda f, t: _fetch_ihsg_chunk(client, f, t),
        date_from, date_to,
        label="fetch_ihsg COMPOSITE", columns=_COLS,
        max_chunks=max_chunks, window_days=window_days,
    )
