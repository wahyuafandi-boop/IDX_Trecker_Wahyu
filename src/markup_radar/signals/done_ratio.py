"""S1 Done Ratio & S2 Absorption Flag (spec §3)."""

from __future__ import annotations


def done_ratio(done_offer_value: float, done_bid_value: float) -> float:
    """S1: done_offer / (done_offer + done_bid).

    >0.55 buyer control; <0.45 seller control; 0.45-0.55 netral.
    Return 0.5 (netral) bila tidak ada transaksi.
    """
    total = done_offer_value + done_bid_value
    if total <= 0:
        return 0.5
    return done_offer_value / total


def absorption_flag(
    ratio: float,
    price_change: float,
    rvol: float,
    *,
    done_ratio_seller: float = 0.45,
    min_rvol: float = 1.5,
) -> bool:
    """S2: done-at-bid dominan TAPI harga ditahan + volume tinggi = absorpsi.

    True -> akumulasi berlanjut (bandar menyerap jualan tanpa menurunkan harga).
    """
    return ratio < done_ratio_seller and price_change >= 0 and rvol >= min_rvol
