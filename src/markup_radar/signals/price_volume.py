"""S5 Closing Queue Imbalance, S6 RVOL, S7 Close-in-range (spec §3)."""

from __future__ import annotations

import pandas as pd


def rvol(volume_series: pd.Series, window: int = 20) -> float:
    """S6: volume hari ini / MA(window) volume. >=2.0 = spike.

    Butuh minimal `window`+1 data; kalau kurang, pakai rata-rata yang ada.
    """
    vol = pd.Series(volume_series).dropna()
    if len(vol) < 2:
        return 0.0
    today = vol.iloc[-1]
    hist = vol.iloc[-(window + 1):-1]
    avg = hist.mean()
    if not avg or avg <= 0:
        return 0.0
    return float(today / avg)


def close_in_range(high: float, low: float, close: float) -> float:
    """S7: (close - low) / (high - low). >0.6 = close kuat."""
    rng = high - low
    if rng <= 0:
        return 0.5
    return float((close - low) / rng)


def queue_imbalance(bid_volume: float, offer_volume: float) -> float:
    """S5: bid_volume / offer_volume di close. >1 demand menumpuk."""
    if offer_volume <= 0:
        return 0.0
    return float(bid_volume / offer_volume)


def price_change(prev_close: float, close: float) -> float:
    """Perubahan harga absolut (untuk absorption flag S2)."""
    return float(close - prev_close)


def price_ranging(closes: pd.Series, window: int = 10, band: float = 0.05) -> bool:
    """True bila harga bergerak sideways (range sempit) dalam `window` terakhir."""
    c = pd.Series(closes).dropna().iloc[-window:]
    if len(c) < 2 or c.mean() == 0:
        return False
    spread = (c.max() - c.min()) / c.mean()
    return bool(spread <= band)


def near_range_high(high: float, low: float, close: float, threshold: float = 0.8) -> bool:
    """True bila close berada di dekat puncak range harian (untuk distribusi)."""
    return close_in_range(high, low, close) >= threshold
