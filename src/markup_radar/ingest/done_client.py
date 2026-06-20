"""Normalisasi done-by-bid/offer dari momentum chart (S1 Done Ratio, S2 Absorption).

INI FIELD PALING PENTING (gating question spec §7).
Momentum chart Invezgo memuat BuyLot/SellLot (atau BuyVolume/SellVolume) yang
secara efektif adalah done-at-offer (buy, agresor angkat offer) vs
done-at-bid (sell, agresor lego di bid).

Catatan: shape persis perlu diverifikasi via scripts/verify_data.py.
"""

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


def fetch_done_breakdown(client: InvezgoClient, code: str, date: str) -> dict[str, float]:
    """-> {done_offer_value, done_bid_value} untuk satu tanggal.

    done_offer = sisi BUY (agresor angkat offer)
    done_bid   = sisi SELL (agresor lego di bid)

    Shape Invezgo (scope='value'): list time-series intraday
    [{"time","value","buy","sell"}, ...] di mana `buy`/`sell` KUMULATIF
    sepanjang hari -> total sehari = nilai kumulatif terakhir (= max).
    """
    raw = client.momentum_chart(code, date)

    if isinstance(raw, list) and raw:
        # buy/sell kumulatif & monoton naik -> ambil maksimum (= total akhir).
        offer = max((_f(_pick(r, "buy", "buyValue", "BuyVolume", "buyLot")) for r in raw), default=0.0)
        bid = max((_f(_pick(r, "sell", "sellValue", "SellVolume", "sellLot")) for r in raw), default=0.0)
    else:
        raw = raw or {}
        offer = _f(_pick(raw, "buy", "buyValue", "BuyVolume", "buyLot", "BuyLot"))
        bid = _f(_pick(raw, "sell", "sellValue", "SellVolume", "sellLot", "SellLot"))

    return {"done_offer_value": offer, "done_bid_value": bid}
