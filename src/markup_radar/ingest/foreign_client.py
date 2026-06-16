"""Normalisasi foreign flow (S8)."""

from __future__ import annotations

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def fetch_foreign_net(client: InvezgoClient, code: str, date: str) -> float:
    """Foreign net value untuk saham `code` pada `date` (positif = net buy).

    top/foreign mengembalikan daftar saham dengan foreign flow tertinggi;
    kita cari baris untuk `code`. Return 0.0 bila tidak ada (di luar top list).
    """
    raw = client.top_foreign(date)
    rows = raw if isinstance(raw, list) else raw.get("items", [])
    for r in rows:
        rcode = _pick(r, "code", "symbol", "stock", default="")
        if str(rcode).upper() == code.upper():
            return float(_pick(r, "netValue", "foreignNet", "net", default=0) or 0)
    return 0.0
