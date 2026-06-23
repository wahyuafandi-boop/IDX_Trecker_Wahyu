"""Rule-based classification (spec §4).

Output salah satu dari enam state:
  MARKUP_CONFIRMED, MARKUP_START, ACCUMULATION_ONGOING, DISTRIBUTION_WARNING,
  NEUTRAL, INSUFFICIENT_DATA
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
    "queue_imbalance_demand": 1.0,
    "broker_net_buy_streak_min": 3,
}


def classify(signals: dict, thresholds: dict | None = None) -> str:
    """Klasifikasikan satu saham berdasarkan dict sinyal (lihat spec §4)."""
    t = {**_DEFAULTS, **(thresholds or {})}
    s = signals

    if not s or s.get("insufficient_data"):
        return "INSUFFICIENT_DATA"

    # MARKUP: buyer ambil alih + konfirmasi volume + close kuat + broker masih
    #         akumulasi. Gate dasar ini sama untuk dua tier di bawah.
    # CATATAN: filter market IHSG>MA50 TIDAK lagi veto keras di sini — dipindah ke
    # confidence score (bobot `ihsg`) agar engine tak bisu saat IHSG sedang <MA50.
    # Validasi 2026-06-21: dengan veto IHSG, 0 sinyal di 5 saham; tanpa veto,
    # 10 sinyal, avg fwd_max +9.0%. Market lemah menekan confidence, bukan memveto.
    # Relative strength (S10) — OPT-IN lewat profil regime. Default
    # require_relative_strength=False -> klausa no-op -> backward-compatible.
    # Di regime BEARISH (profil set True), saham WAJIB outperform IHSG (rs>rs_min)
    # untuk lolos MARKUP — bounce yang kalah dari index = bull-trap, ditolak.
    require_rs = t.get("require_relative_strength", False)
    outperforms = s.get("relative_strength", 0.0) > t.get("rs_min", 0.0)

    base_markup = (
        s["done_ratio"] > t["done_ratio_markup"]
        and s["rvol"] >= t["rvol_spike"]
        and s["close_in_range"] > t["close_in_range_strong"]
        and s["broker_net_buy_streak"] >= 1
        and (not require_rs or outperforms)   # ← BEARISH: wajib outperform IHSG
    )
    if base_markup:
        # MARKUP_CONFIRMED: gate dasar + antrian beli menumpuk di close (S5
        # bid/offer >= demand) → tesis "pengumpulan bandar selesai, siap markup".
        # queue_imbalance hanya tersedia LIVE (order book tidak historis); di
        # backtest S5=0 < demand sehingga CONFIRMED tak pernah muncul dan
        # validasi MARKUP_START tetap berlaku apa adanya.
        if s.get("queue_imbalance", 0.0) >= t["queue_imbalance_demand"]:
            return "MARKUP_CONFIRMED"
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
