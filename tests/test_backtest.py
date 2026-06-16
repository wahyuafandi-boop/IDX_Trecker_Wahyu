"""Test backtest engine + metrics dengan skenario sintetis (spec Phase 5)."""

import pandas as pd

from markup_radar.backtest import HistoricalDataset, replay, summarize, tune

THRESHOLDS = {
    "done_ratio_markup": 0.60,
    "done_ratio_seller": 0.45,
    "done_ratio_buyer": 0.55,
    "done_ratio_distribution": 0.40,
    "rvol_spike": 2.0,
    "close_in_range_strong": 0.60,
    "broker_net_buy_streak_min": 3,
    "absorption_min_rvol": 1.5,
    "near_range_high": 0.8,
}
WINDOWS = {"volume_ma": 20, "ihsg_ma": 50}

MARKUP_IDX = 30  # hari ke-30: bar markup


def _dataset() -> HistoricalDataset:
    """Akumulasi datar 30 hari, lalu bar markup + lanjut naik."""
    dates = pd.bdate_range("2025-01-01", periods=40)
    rows = []
    for i, d in enumerate(dates):
        if i < MARKUP_IDX:                       # fase datar
            rows.append({"date": d, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100})
        elif i == MARKUP_IDX:                    # bar markup: RVOL 3x, close kuat
            rows.append({"date": d, "open": 100, "high": 110, "low": 100, "close": 109, "volume": 300})
        else:                                    # markup berlanjut
            base = 109 + (i - MARKUP_IDX) * 2
            rows.append({"date": d, "open": base, "high": base + 3, "low": base - 1, "close": base + 2, "volume": 200})
    ohlcv = pd.DataFrame(rows)

    done = pd.DataFrame([{"date": dates[MARKUP_IDX], "done_offer_value": 80, "done_bid_value": 20}])
    broker = pd.DataFrame([{"date": d, "net": 10} for d in dates])         # net buy konsisten
    ihsg = pd.DataFrame([{"date": d, "close": 100 + i} for i, d in enumerate(dates)])  # naik > MA50

    return HistoricalDataset(code="TEST", ohlcv=ohlcv, done=done, broker_daily_net=broker, ihsg=ihsg)


def test_replay_detects_markup_start():
    res = replay(_dataset(), THRESHOLDS, WINDOWS, horizon=5, warmup=20)
    assert not res.empty
    markup_day = res[res["date"] == pd.Timestamp(pd.bdate_range("2025-01-01", periods=40)[MARKUP_IDX])]
    assert markup_day.iloc[0]["state"] == "MARKUP_START"
    assert markup_day.iloc[0]["done_ratio"] == 0.8


def test_forward_return_positive_after_markup():
    res = replay(_dataset(), THRESHOLDS, WINDOWS, horizon=5, warmup=20)
    markup_rows = res[res["state"] == "MARKUP_START"]
    assert (markup_rows["fwd_max"] > 0).all()


def test_summarize_hit_rate():
    res = replay(_dataset(), THRESHOLDS, WINDOWS, horizon=5, warmup=20)
    report = summarize(res, target_up=0.05)
    ms = report[report["state"] == "MARKUP_START"]
    assert not ms.empty
    assert ms.iloc[0]["hit_rate"] == 1.0      # markup memang diikuti kenaikan >=5%
    assert ms.iloc[0]["n"] >= 1


def test_summarize_empty_input():
    assert summarize(pd.DataFrame()).empty


def test_tune_returns_grid():
    grid = tune(_dataset(), THRESHOLDS, WINDOWS, values=[0.60, 0.70], horizon=5)
    assert list(grid["done_ratio_markup"]) == [0.60, 0.70]
    assert "hit_rate" in grid.columns


def test_replay_too_short_returns_empty():
    short = HistoricalDataset(
        code="X",
        ohlcv=pd.DataFrame([{"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]),
    )
    assert replay(short, THRESHOLDS, WINDOWS, warmup=20).empty
