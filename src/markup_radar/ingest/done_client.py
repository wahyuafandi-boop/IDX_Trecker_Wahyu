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


def fetch_done_breakdown(client: InvezgoClient, code: str, date: str) -> dict[str, float]:
    """-> {done_offer_value, done_bid_value} untuk satu tanggal.

    done_offer = sisi BUY (agresor angkat offer)
    done_bid   = sisi SELL (agresor lego di bid)
    """
    raw = client.momentum_chart(code, date)

    # Bentuk umum: ringkasan agregat, atau list per interval yang perlu dijumlah.
    if isinstance(raw, list):
        offer = sum(_pick(r, "buyLot", "BuyLot", "buyValue", "BuyVolume", default=0) or 0 for r in raw)
        bid = sum(_pick(r, "sellLot", "SellLot", "sellValue", "SellVolume", default=0) or 0 for r in raw)
    else:
        raw = raw or {}
        offer = _pick(raw, "buyLot", "BuyLot", "buyValue", "BuyVolume", "buy", default=0) or 0
        bid = _pick(raw, "sellLot", "SellLot", "sellValue", "SellVolume", "sell", default=0) or 0

    return {"done_offer_value": float(offer), "done_bid_value": float(bid)}
