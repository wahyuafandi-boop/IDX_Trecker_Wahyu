"""S8 Foreign Flow, S9 IHSG Filter (spec §3)."""

from __future__ import annotations

import pandas as pd


def ihsg_above_ma50(ihsg_close: pd.Series, window: int = 50) -> bool:
    """S9: IHSG close terakhir > MA(window). True = market suportif markup."""
    c = pd.Series(ihsg_close).dropna()
    if c.empty:
        return False
    ma = c.iloc[-window:].mean()
    return bool(c.iloc[-1] > ma)


def foreign_net_positive(foreign_net_value: float) -> bool:
    """S8: konfirmasi arah smart money via foreign net buy."""
    return foreign_net_value > 0
