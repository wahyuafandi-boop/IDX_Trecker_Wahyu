"""S8 Foreign Flow, S9 IHSG Filter, S10 Relative Strength + Regime selector (spec §3-4)."""

from __future__ import annotations

from enum import Enum

import pandas as pd


class Regime(str, Enum):
    """Regime pasar dari IHSG vs MA — memilih profil parameter (spec §4.1)."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


def ihsg_above_ma50(ihsg_close: pd.Series, window: int = 50) -> bool:
    """S9: IHSG close terakhir > MA(window). True = market suportif markup."""
    c = pd.Series(ihsg_close).dropna()
    if c.empty:
        return False
    ma = c.iloc[-window:].mean()
    return bool(c.iloc[-1] > ma)


def market_regime(ihsg_close, window: int = 50) -> Regime:
    """IHSG vs MA(window) -> regime. Fail-safe: data kosong/kurang = BEARISH
    (profil lebih ketat saat market tak diketahui)."""
    c = pd.Series(ihsg_close).dropna()
    if c.empty:
        return Regime.BEARISH
    ma = c.iloc[-window:].mean()
    return Regime.BULLISH if c.iloc[-1] > ma else Regime.BEARISH


def relative_strength(stock_close, ihsg_close, window: int = 20) -> float:
    """S10: return saham - return IHSG selama `window` hari. >0 = outperform.

    CATATAN: pakai window posisional (bukan date-join). Akurat cukup untuk 20d EOD;
    refine ke date-align bila butuh presisi (lihat spec Edge Cases).
    """
    s = pd.Series(stock_close).dropna()
    i = pd.Series(ihsg_close).dropna()
    if len(s) < window + 1 or len(i) < window + 1:
        return 0.0
    s_ret = s.iloc[-1] / s.iloc[-(window + 1)] - 1
    i_ret = i.iloc[-1] / i.iloc[-(window + 1)] - 1
    return float(s_ret - i_ret)


def foreign_net_positive(foreign_net_value: float) -> bool:
    """S8: konfirmasi arah smart money via foreign net buy."""
    return foreign_net_value > 0
