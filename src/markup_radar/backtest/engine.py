"""Replay engine: putar ulang classifier hari demi hari + forward return."""

from __future__ import annotations

import pandas as pd

from markup_radar.backtest.dataset import HistoricalDataset, _lookup
from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals


def _forward_returns(ohlcv: pd.DataFrame, i: int, horizon: int) -> dict[str, float]:
    """Return ke depan dari close hari ke-i sampai i+horizon."""
    base = ohlcv["close"].iloc[i]
    end = min(i + horizon, len(ohlcv) - 1)
    if i >= len(ohlcv) - 1 or base <= 0:
        return {"fwd_max": float("nan"), "fwd_min": float("nan"), "fwd_close": float("nan")}
    window = ohlcv.iloc[i + 1 : end + 1]
    return {
        "fwd_max": float(window["high"].max() / base - 1),
        "fwd_min": float(window["low"].min() / base - 1),
        "fwd_close": float(ohlcv["close"].iloc[end] / base - 1),
    }


def replay(
    dataset: HistoricalDataset,
    thresholds: dict,
    windows: dict,
    *,
    top_n: int = 5,
    horizon: int = 5,
    warmup: int = 20,
) -> pd.DataFrame:
    """Klasifikasikan tiap hari bursa lalu lampirkan forward return.

    Mengembalikan DataFrame: date, code, state, confidence,
    done_ratio, rvol, fwd_max, fwd_min, fwd_close.
    """
    ohlcv = dataset.ohlcv
    if ohlcv.empty or len(ohlcv) <= warmup:
        return pd.DataFrame()

    rows = []
    for i in range(warmup, len(ohlcv)):
        date = ohlcv["date"].iloc[i]
        hist = ohlcv.iloc[: i + 1]  # data sampai hari ke-i (inklusif)

        ihsg_hist = dataset.ihsg[dataset.ihsg["date"] <= date]["close"] if not dataset.ihsg.empty else pd.Series(dtype=float)
        net_hist = dataset.broker_daily_net[dataset.broker_daily_net["date"] <= date]["net"].tolist() if not dataset.broker_daily_net.empty else []

        data = StockData(
            code=dataset.code,
            ohlcv=hist,
            done_offer_value=_lookup(dataset.done, date, "done_offer_value"),
            done_bid_value=_lookup(dataset.done, date, "done_bid_value"),
            broker_daily_net=net_hist,
            closing_bid_volume=_lookup(dataset.closing_queue, date, "bid_volume"),
            closing_offer_volume=_lookup(dataset.closing_queue, date, "offer_volume"),
            foreign_net_value=_lookup(dataset.foreign, date, "net"),
            ihsg_close=ihsg_hist,
        )

        signals = compute_signals(data, thresholds, windows, top_n)
        state = classify(signals, thresholds)
        conf = confidence_markup_start(signals, {})
        fwd = _forward_returns(ohlcv, i, horizon)

        rows.append(
            {
                "date": date,
                "code": dataset.code,
                "state": state,
                "confidence": conf,
                "done_ratio": round(signals["done_ratio"], 3),
                "rvol": round(signals["rvol"], 2),
                **fwd,
            }
        )

    return pd.DataFrame(rows)
