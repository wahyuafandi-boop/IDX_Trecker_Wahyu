"""Replay engine: putar ulang classifier hari demi hari + forward return."""

from __future__ import annotations

import pandas as pd

from markup_radar.backtest.dataset import HistoricalDataset, _lookup
from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals
from markup_radar.signals.market import market_regime


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
    regime_profiles: dict | None = None,
    top_n: int = 5,
    horizon: int = 20,
    warmup: int = 20,
) -> pd.DataFrame:
    """Klasifikasikan tiap hari bursa lalu lampirkan forward return.

    Regime-aware (spec §5.1): per bar, resolve regime dari history IHSG SAMPAI
    tanggal itu, pilih profil (`regime_profiles[regime]`), merge ke thresholds,
    lalu classify dengan `eff`. `regime_profiles=None` -> profil kosong -> perilaku
    identik backtest lama (backward-compatible). Default horizon 20 (bukan 5) —
    edge swing cuma muncul di 10-20d (pelajaran 2026-06-21).

    Mengembalikan DataFrame: date, code, state, regime, confidence,
    done_ratio, rvol, relative_strength, fwd_max, fwd_min, fwd_close.
    """
    ohlcv = dataset.ohlcv
    if ohlcv.empty or len(ohlcv) <= warmup:
        return pd.DataFrame()

    profiles = regime_profiles or {}
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

        # Regime per-bar -> profil -> eff (sama mekanika dgn run_daily, tapi pakai
        # history sampai tanggal ini saja: tak ada lookahead).
        regime = market_regime(ihsg_hist, windows.get("ihsg_ma", 50))
        eff = {**thresholds, **profiles.get(regime.value, {})}

        signals = compute_signals(data, thresholds, windows, top_n)
        state = classify(signals, eff)
        conf = confidence_markup_start(signals, {})
        fwd = _forward_returns(ohlcv, i, horizon)

        rows.append(
            {
                "date": date,
                "code": dataset.code,
                "state": state,
                "regime": regime.value,
                "confidence": conf,
                "done_ratio": round(signals["done_ratio"], 3),
                "rvol": round(signals["rvol"], 2),
                "relative_strength": round(signals.get("relative_strength", 0.0), 4),
                **fwd,
            }
        )

    return pd.DataFrame(rows)
