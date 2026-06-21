"""Rule-based classification (spec §4).

Output salah satu dari lima state:
  MARKUP_START, ACCUMULATION_ONGOING, DISTRIBUTION_WARNING, NEUTRAL, INSUFFICIENT_DATA
"""

from __future__ import annotations

# Default threshold; bisa di-override lewat argumen `thresholds`.
_DEFAULTS = {
    "done_ratio_markup": 0.60,
    "done_ratio_seller": 0.45,
    "done_ratio_buyer": 0.55,
    "done_ratio_distribution": 0.40,
    "rvol_spike": 2.0,
    "close_in_range_strong": 0.60,
    "broker_net_buy_streak_min": 3,
}


def classify(signals: dict, thresholds: dict | None = None) -> str:
    """Klasifikasikan satu saham berdasarkan dict sinyal (lihat spec §4)."""
    t = {**_DEFAULTS, **(thresholds or {})}
    s = signals

    if not s or s.get("insufficient_data"):
        return "INSUFFICIENT_DATA"

    # MARKUP_START: buyer ambil alih + konfirmasi volume + close kuat
    #               + broker masih akumulasi.
    # CATATAN: filter market IHSG>MA50 TIDAK lagi veto keras di sini — dipindah ke
    # confidence score (bobot `ihsg`) agar engine tak bisu saat IHSG sedang <MA50.
    # Validasi 2026-06-21: dengan veto IHSG, 0 sinyal di 5 saham; tanpa veto,
    # 10 sinyal, avg fwd_max +9.0%. Market lemah menekan confidence, bukan memveto.
    if (
        s["done_ratio"] > t["done_ratio_markup"]
        and s["rvol"] >= t["rvol_spike"]
        and s["close_in_range"] > t["close_in_range_strong"]
        and s["broker_net_buy_streak"] >= 1
    ):
        return "MARKUP_START"

    # ACCUMULATION_ONGOING: absorpsi, ATAU broker net buy senyap saat harga ranging.
    if s["absorption_flag"] or (
        s["broker_net_buy_streak"] >= t["broker_net_buy_streak_min"]
        and s.get("price_ranging", False)
        and t["done_ratio_seller"] <= s["done_ratio"] <= t["done_ratio_buyer"]
    ):
        return "ACCUMULATION_ONGOING"

    # DISTRIBUTION_WARNING: seller menang + broker berbalik jual + harga di puncak range.
    if (
        s["done_ratio"] < t["done_ratio_distribution"]
        and s.get("broker_turning_net_sell", False)
        and s.get("near_range_high", False)
    ):
        return "DISTRIBUTION_WARNING"

    return "NEUTRAL"
