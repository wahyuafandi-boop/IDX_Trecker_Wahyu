"""Normalisasi IHSG untuk filter market (S9)."""

from __future__ import annotations

import pandas as pd

from markup_radar.ingest.client import InvezgoClient

IHSG_CODE = "COMPOSITE"  # TODO(verify): kode index IHSG di Invezgo (mis. 'COMPOSITE'/'IHSG')


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def fetch_ihsg(client: InvezgoClient, date_from: str, date_to: str) -> pd.DataFrame:
    """IHSG harian -> DataFrame[date, close] untuk hitung MA50 (S9)."""
    raw = client.stock_chart(IHSG_CODE, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))

    records = [
        {
            "date": _pick(r, "date", "time", "timestamp"),
            "close": _pick(r, "close", "c"),
        }
        for r in rows
    ]
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)
