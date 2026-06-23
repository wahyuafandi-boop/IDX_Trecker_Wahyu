"""Trade levels (entry/SL/TP) untuk state MARKUP_* — ATR-based, R:R DIHITUNG.

Hanya dipanggil untuk MARKUP_START / MARKUP_CONFIRMED. Output dipakai alert +
(opsional) simulasi exit di backtest.

TODO: tick-size rounding ke fraksi harga IDX valid (Rp1/Rp2/Rp5/… per pita harga) —
ditunda untuk v2 (lihat spec Edge Cases).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from markup_radar.signals.price_volume import atr, donchian


@dataclass
class TradeLevels:
    resistance: float
    support: float
    atr: float
    entry: float
    stop_loss: float
    take_profit: float
    rr_realized: float     # DIHITUNG dari level — bukan dilabel
    stop_pct: float        # jarak SL dari entry (%) → transparansi whipsaw
    est_hold_days: int     # estimasi lama swing → bunuh fantasi "1 minggu"

    def as_dict(self) -> dict:
        return asdict(self)


def compute_trade_levels(
    ohlcv: pd.DataFrame, *,
    lookback: int = 20, atr_period: int = 14, breakout_buffer: float = 0.005,
    atr_mult_sl: float = 2.0, rr_target: float = 2.0,
    min_stop_pct: float = 0.03, hold_slack: float = 1.8,
) -> TradeLevels | None:
    """Hitung level breakout. None bila data kurang."""
    if ohlcv is None or len(ohlcv) < lookback + 1:
        return None
    resistance, support = donchian(ohlcv["high"], ohlcv["low"], lookback)
    a = atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], atr_period)
    if a <= 0 or resistance <= 0:
        return None

    entry = resistance * (1 + breakout_buffer)
    # SL: ATR-based, tapi tak pernah lebih ketat dari min_stop_pct (anti-whipsaw).
    stop_dist = max(atr_mult_sl * a, min_stop_pct * entry)
    stop_loss = entry - stop_dist
    risk = entry - stop_loss
    take_profit = entry + rr_target * risk
    rr_realized = (take_profit - entry) / risk if risk > 0 else 0.0
    stop_pct = stop_dist / entry if entry > 0 else 0.0
    est_hold = int(round((take_profit - entry) / a * hold_slack))

    return TradeLevels(
        resistance=round(resistance, 2), support=round(support, 2), atr=round(a, 2),
        entry=round(entry, 2), stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2), rr_realized=round(rr_realized, 2),
        stop_pct=round(stop_pct, 4), est_hold_days=est_hold,
    )
