"""Signal engine: rakit S1..S9 menjadi satu dict untuk classifier.

Semua input adalah data EOD yang sudah dinormalisasi (DataFrame/skalar).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from markup_radar.signals import broker_flow, done_ratio, market, price_volume

__all__ = ["StockData", "compute_signals", "broker_flow", "done_ratio", "market", "price_volume"]


@dataclass
class StockData:
    """Bundel data EOD satu saham untuk satu tanggal evaluasi."""

    code: str
    ohlcv: pd.DataFrame                       # date, open, high, low, close, volume
    done_offer_value: float = 0.0             # S1/S2 (done at offer = buy)
    done_bid_value: float = 0.0               # S1/S2 (done at bid = sell)
    broker_summary: pd.DataFrame = field(default_factory=pd.DataFrame)  # S4
    broker_daily_net: list[float] = field(default_factory=list)         # S3 (kronologis)
    closing_bid_volume: float = 0.0           # S5
    closing_offer_volume: float = 0.0         # S5
    foreign_net_value: float = 0.0            # S8
    ihsg_close: pd.Series = field(default_factory=pd.Series)            # S9


def compute_signals(
    data: StockData,
    thresholds: dict[str, float],
    windows: dict[str, int],
    top_n: int = 5,
) -> dict:
    """Hitung S1..S9 + flag turunan untuk satu saham.

    Mengembalikan dict yang siap dikonsumsi `scoring.classifier.classify`.
    """
    t = thresholds
    w = windows
    df = data.ohlcv

    last = df.iloc[-1] if not df.empty else pd.Series(dtype=float)
    prev_close = df["close"].iloc[-2] if len(df) >= 2 else last.get("close", 0.0)

    ratio = done_ratio.done_ratio(data.done_offer_value, data.done_bid_value)
    rvol_val = price_volume.rvol(df["volume"], w.get("volume_ma", 20)) if not df.empty else 0.0
    cir = price_volume.close_in_range(last.get("high", 0), last.get("low", 0), last.get("close", 0))
    pchg = price_volume.price_change(prev_close, last.get("close", 0)) if not df.empty else 0.0

    streak = broker_flow.broker_net_buy_streak(data.broker_daily_net)

    return {
        "code": data.code,
        # S1
        "done_ratio": ratio,
        # S2
        "absorption_flag": done_ratio.absorption_flag(
            ratio, pchg, rvol_val,
            done_ratio_seller=t.get("done_ratio_seller", 0.45),
            min_rvol=t.get("absorption_min_rvol", 1.5),
        ),
        # S3
        "broker_net_buy_streak": streak,
        # S4
        "broker_concentration": broker_flow.broker_concentration(data.broker_summary, top_n),
        "broker_turning_net_sell": broker_flow.broker_turning_net_sell(data.broker_daily_net),
        # S5
        "queue_imbalance": price_volume.queue_imbalance(
            data.closing_bid_volume, data.closing_offer_volume
        ),
        # S6
        "rvol": rvol_val,
        # S7
        "close_in_range": cir,
        "near_range_high": price_volume.near_range_high(
            last.get("high", 0), last.get("low", 0), last.get("close", 0),
            t.get("near_range_high", 0.8),
        ),
        "price_ranging": price_volume.price_ranging(df["close"]) if not df.empty else False,
        # S8
        "foreign_net": data.foreign_net_value,
        # S9
        "ihsg_above_ma50": market.ihsg_above_ma50(data.ihsg_close, w.get("ihsg_ma", 50)),
        # S10 (nilai mentah; threshold/gate diterapkan di classifier per profil regime)
        "relative_strength": market.relative_strength(
            df["close"], data.ihsg_close, w.get("rs_window", 20)
        ) if not df.empty else 0.0,
    }
