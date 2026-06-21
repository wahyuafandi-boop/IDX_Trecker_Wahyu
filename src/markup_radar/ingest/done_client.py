"""Normalisasi done-by-bid/offer dari momentum chart (S1 Done Ratio, S2 Absorption).

INI FIELD PALING PENTING (gating question spec §7).

PENTING — ARAH FIELD INVEZGO TERBALIK dari dugaan awal. momentum-chart me-label
buy/sell dari sisi PASIF (order resting yang ke-hit): saat agresor BUY ia
mengangkat resting OFFER, sehingga tercatat di field `sell`; agresor SELL
sebaliknya masuk field `buy`. Diverifikasi empiris 2026-06-21 pada BBRI/BBCA/TLKM:
done_ratio = buy/(buy+sell) berkorelasi NEGATIF dengan return harian
(BBRI -0.48, BBCA -0.40, TLKM -0.27) dan rata-rata lebih tinggi di hari TURUN.
Karena itu mapping di-SWAP di sini: done_offer (buy agresif) <- field `sell`,
done_bid (sell agresif) <- field `buy`. Sebelum fix ini semua state -> NEUTRAL.
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

    done_offer = sisi BUY agresif (done-at-offer)  <- field Invezgo `sell`
    done_bid   = sisi SELL agresif (done-at-bid)   <- field Invezgo `buy`
    (lihat catatan inversi di docstring modul).

    Shape Invezgo (scope='value'): list time-series intraday
    [{"time","value","buy","sell"}, ...] di mana `buy`/`sell` KUMULATIF
    sepanjang hari -> total sehari = nilai kumulatif terakhir (= max).
    """
    raw = client.momentum_chart(code, date)

    if isinstance(raw, list) and raw:
        # buy/sell kumulatif & monoton naik -> ambil maksimum (= total akhir).
        # SWAP arah: done_offer (buy agresif) <- `sell`; done_bid (sell agresif) <- `buy`.
        offer = max((_f(_pick(r, "sell", "sellValue", "SellVolume", "sellLot")) for r in raw), default=0.0)
        bid = max((_f(_pick(r, "buy", "buyValue", "BuyVolume", "buyLot")) for r in raw), default=0.0)
    else:
        raw = raw or {}
        offer = _f(_pick(raw, "sell", "sellValue", "SellVolume", "sellLot", "SellLot"))
        bid = _f(_pick(raw, "buy", "buyValue", "BuyVolume", "buyLot", "BuyLot"))

    return {"done_offer_value": offer, "done_bid_value": bid}
