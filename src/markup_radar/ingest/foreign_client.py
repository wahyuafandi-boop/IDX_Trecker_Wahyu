"""Normalisasi foreign flow (S8)."""

from __future__ import annotations

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def fetch_foreign_map(client: InvezgoClient, date: str) -> dict[str, float]:
    """Tarik top/foreign SEKALI per run -> {code: foreign_net_value}.

    Dipanggil 1x untuk seluruh watchlist (bukan per saham) demi hemat kuota.
    """
    raw = client.top_foreign(date)
    rows = raw if isinstance(raw, list) else raw.get("items", [])
    out: dict[str, float] = {}
    for r in rows:
        rcode = str(_pick(r, "code", "symbol", "stock", default="")).upper()
        if rcode:
            out[rcode] = float(_pick(r, "netValue", "foreignNet", "net", default=0) or 0)
    return out


def foreign_net_for(code: str, foreign_map: dict[str, float]) -> float:
    """Lookup lokal dari map. 0.0 bila saham di luar top list."""
    return foreign_map.get(code.upper(), 0.0)


def fetch_foreign_net(client: InvezgoClient, code: str, date: str) -> float:
    """(Legacy) ambil foreign net 1 saham via 1 call. Untuk batch pakai
    fetch_foreign_map + foreign_net_for agar tidak boros kuota."""
    return foreign_net_for(code, fetch_foreign_map(client, date))
