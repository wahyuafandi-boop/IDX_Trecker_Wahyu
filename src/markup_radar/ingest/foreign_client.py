"""Normalisasi foreign flow (S8)."""

from __future__ import annotations

from markup_radar.ingest.client import InvezgoClient


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_foreign_map(client: InvezgoClient, date: str) -> dict[str, float]:
    """Tarik top/foreign SEKALI per run -> {code: foreign_net_value}.

    Dipanggil 1x untuk seluruh watchlist (bukan per saham) demi hemat kuota.
    Shape Invezgo: {accum:[{code,value,...}], dist:[{code,value,...}]} dengan
    `value` STRING magnitudo -> accum = net beli asing (+), dist = net jual (−).
    """
    raw = client.top_foreign(date)
    out: dict[str, float] = {}

    def _ingest(rows, sign: float) -> None:
        for r in rows or []:
            rcode = str(_pick(r, "code", "symbol", "stock", default="")).upper()
            if rcode:
                # AKUMULASI (bukan overwrite): kode yang muncul di accum DAN dist
                # harus jadi net (+accum −dist), bukan ketimpa nilai yang belakangan.
                val = sign * abs(_f(_pick(r, "value", "netValue", "foreignNet", "net")))
                out[rcode] = out.get(rcode, 0.0) + val

    if isinstance(raw, dict):
        _ingest(raw.get("accum"), +1.0)
        _ingest(raw.get("dist"), -1.0)
    elif isinstance(raw, list):  # fallback shape datar bertanda
        for r in raw:
            rcode = str(_pick(r, "code", "symbol", "stock", default="")).upper()
            if rcode:
                out[rcode] = _f(_pick(r, "netValue", "foreignNet", "net", "value"))
    return out


def foreign_net_for(code: str, foreign_map: dict[str, float]) -> float:
    """Lookup lokal dari map. 0.0 bila saham di luar top list."""
    return foreign_map.get(code.upper(), 0.0)


def fetch_foreign_net(client: InvezgoClient, code: str, date: str) -> float:
    """(Legacy) ambil foreign net 1 saham via 1 call. Untuk batch pakai
    fetch_foreign_map + foreign_net_for agar tidak boros kuota."""
    return foreign_net_for(code, fetch_foreign_map(client, date))
