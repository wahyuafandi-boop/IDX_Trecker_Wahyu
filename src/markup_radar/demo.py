"""Data sintetis untuk demo & smoke-test offline (tanpa API key).

Membangun skenario yang memicu tiap state classifier, supaya pipeline bisa
dilihat berjalan end-to-end sebelum langganan Invezgo aktif.
"""

from __future__ import annotations

import pandas as pd

from markup_radar.scoring import classify, confidence_markup_start
from markup_radar.signals import StockData, compute_signals

_N = 26          # panjang history (cukup untuk MA20)
_BASE = 100.0
_VOL = 100.0


def _history(last_bar: dict, *, n: int = _N) -> pd.DataFrame:
    """History datar n-1 hari + 1 bar terakhir custom."""
    dates = pd.bdate_range("2025-01-01", periods=n)
    rows = [
        {"date": d, "open": _BASE, "high": _BASE + 1, "low": _BASE - 1,
         "close": _BASE, "volume": _VOL}
        for d in dates[:-1]
    ]
    rows.append({"date": dates[-1], **last_bar})
    return pd.DataFrame(rows)


def make_snapshot(kind: str, code: str) -> StockData:
    """StockData 'hari ini' yang dirancang menghasilkan state tertentu."""
    if kind == "markup":
        return StockData(
            code=code,
            ohlcv=_history({"open": _BASE, "high": _BASE + 10, "low": _BASE,
                            "close": _BASE + 9, "volume": _VOL * 3}),
            done_offer_value=80, done_bid_value=20,            # ratio 0.8
            broker_daily_net=[10, 10, 10],                     # streak 3
            closing_bid_volume=200, closing_offer_volume=100,
            foreign_net_value=5_000,
            ihsg_close=pd.Series([100 + i for i in range(60)]),  # > MA50
        )
    if kind == "accumulation":
        return StockData(
            code=code,
            ohlcv=_history({"open": _BASE, "high": _BASE + 2, "low": _BASE - 1,
                            "close": _BASE + 1, "volume": _VOL * 2}),
            done_offer_value=20, done_bid_value=80,            # ratio 0.2, harga ditahan -> absorpsi
            broker_daily_net=[10, 10, 10],
            ihsg_close=pd.Series([100 + i for i in range(60)]),
        )
    if kind == "distribution":
        return StockData(
            code=code,
            ohlcv=_history({"open": _BASE, "high": _BASE + 5, "low": _BASE,
                            "close": _BASE + 4.5, "volume": _VOL}),  # rvol ~1 -> bukan absorpsi
            done_offer_value=20, done_bid_value=80,            # ratio 0.2
            broker_daily_net=[10, 10, 10, -20],                # akumulasi lalu berbalik jual
            ihsg_close=pd.Series([100 + i for i in range(60)]),
        )
    # neutral
    return StockData(
        code=code,
        ohlcv=_history({"open": _BASE, "high": _BASE + 1, "low": _BASE - 1,
                        "close": _BASE, "volume": _VOL}),
        done_offer_value=50, done_bid_value=50,
        broker_daily_net=[0, 0, 0],
        ihsg_close=pd.Series([100 + i for i in range(60)]),
    )


def classify_snapshot(data: StockData, cfg) -> tuple[str, int, dict]:
    """Hitung sinyal + state + confidence untuk satu snapshot."""
    signals = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
    state = classify(signals, cfg.thresholds)
    conf = confidence_markup_start(signals, cfg.score_weights)
    return state, conf, signals


def make_history_markup(code: str = "DEMO", n: int = 40, markup_idx: int = 30):
    """HistoricalDataset: akumulasi datar -> bar markup -> lanjut naik.

    Untuk demo backtest (punya forward return). Mengembalikan HistoricalDataset.
    """
    from markup_radar.backtest import HistoricalDataset

    dates = pd.bdate_range("2025-01-01", periods=n)
    rows = []
    for i, d in enumerate(dates):
        if i < markup_idx:
            rows.append({"date": d, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100})
        elif i == markup_idx:
            rows.append({"date": d, "open": 100, "high": 110, "low": 100, "close": 109, "volume": 300})
        else:
            base = 109 + (i - markup_idx) * 2
            rows.append({"date": d, "open": base, "high": base + 3, "low": base - 1,
                         "close": base + 2, "volume": 200})
    return HistoricalDataset(
        code=code,
        ohlcv=pd.DataFrame(rows),
        done=pd.DataFrame([{"date": dates[markup_idx], "done_offer_value": 80, "done_bid_value": 20}]),
        broker_daily_net=pd.DataFrame([{"date": d, "net": 10} for d in dates]),
        ihsg=pd.DataFrame([{"date": d, "close": 100 + i} for i, d in enumerate(dates)]),
    )
