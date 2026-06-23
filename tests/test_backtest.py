"""Test backtest engine + metrics dengan skenario sintetis (spec Phase 5 + §5 v2)."""

from types import SimpleNamespace

import pandas as pd

from markup_radar.backtest import (
    HistoricalDataset,
    backtest_levels,
    null_model,
    replay,
    signal_indices,
    simulate_exit,
    summarize,
    tune,
)

PROFILES = {
    "BULLISH": {"rvol_spike": 2.0, "require_relative_strength": False, "rs_min": 0.0},
    "BEARISH": {"rvol_spike": 2.5, "require_relative_strength": True, "rs_min": 0.0},
}
LEVELS_KW = {"lookback": 20, "atr_period": 14}

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


def test_replay_matches_tz_aware_ohlcv_vs_naive_done():
    """Regresi path --no-cache: OHLCV dari fetch_ohlcv bisa tz-aware
    (datetime64[*, UTC]) sedangkan done dirakit dari string "YYYY-MM-DD"
    (tz-naive). Sebelum normalisasi tz, _lookup (date == date) tak pernah match
    → done_ratio degrade senyap ke 0.5. Setelah fix, lookup benar.
    """
    dates = pd.bdate_range("2025-01-01", periods=40)
    rows = []
    for i, d in enumerate(dates):
        if i < MARKUP_IDX:
            rows.append({"date": d, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100})
        elif i == MARKUP_IDX:
            rows.append({"date": d, "open": 100, "high": 110, "low": 100, "close": 109, "volume": 300})
        else:
            base = 109 + (i - MARKUP_IDX) * 2
            rows.append({"date": d, "open": base, "high": base + 3, "low": base - 1, "close": base + 2, "volume": 200})
    ohlcv = pd.DataFrame(rows)
    # Tiru fetch_ohlcv pada API live: kolom date tz-aware UTC.
    ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.tz_localize("UTC")
    assert ohlcv["date"].dt.tz is not None  # tz-aware sebelum masuk dataset

    # Tiru fresh_rows (cache miss): done memakai date string -> tz-naive.
    done = pd.DataFrame([{
        "date": dates[MARKUP_IDX].strftime("%Y-%m-%d"),
        "done_offer_value": 80,
        "done_bid_value": 20,
    }])

    ds = HistoricalDataset(code="TEST", ohlcv=ohlcv, done=done)
    # __post_init__ harus menyeragamkan kedua sisi jadi tz-naive.
    assert ds.ohlcv["date"].dt.tz is None
    assert ds.done["date"].dt.tz is None

    res = replay(ds, THRESHOLDS, WINDOWS, horizon=5, warmup=20)
    markup_day = res[res["date"] == pd.Timestamp(dates[MARKUP_IDX])]
    assert not markup_day.empty
    assert markup_day.iloc[0]["done_ratio"] == 0.8   # ter-lookup benar, bukan 0.5 degrade


def test_replay_too_short_returns_empty():
    short = HistoricalDataset(
        code="X",
        ohlcv=pd.DataFrame([{"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]),
    )
    assert replay(short, THRESHOLDS, WINDOWS, warmup=20).empty


# ---- F7: replay regime-aware (spec §5.1) ----
def _dataset_bearish_ihsg() -> HistoricalDataset:
    """Sama seperti _dataset tapi IHSG MENURUN -> regime BEARISH."""
    ds = _dataset()
    dates = list(ds.ohlcv["date"])
    return HistoricalDataset(
        code="TEST", ohlcv=ds.ohlcv, done=ds.done,
        broker_daily_net=ds.broker_daily_net,
        ihsg=pd.DataFrame([{"date": d, "close": 200 - i} for i, d in enumerate(dates)]),
    )


def test_replay_emits_regime_and_rs_columns():
    res = replay(_dataset(), THRESHOLDS, WINDOWS, regime_profiles=PROFILES, horizon=5, warmup=20)
    assert "regime" in res.columns and "relative_strength" in res.columns
    assert set(res["regime"]).issubset({"BULLISH", "BEARISH"})


def test_replay_regime_reflects_rising_ihsg_bullish():
    res = replay(_dataset(), THRESHOLDS, WINDOWS, regime_profiles=PROFILES, horizon=5, warmup=20)
    assert (res["regime"] == "BULLISH").all()      # IHSG naik > MA -> BULLISH


def test_replay_bearish_regime_allows_outperformer():
    # IHSG turun -> BEARISH (require_relative_strength). Saham markup naik saat market
    # turun = outperform (rs>0) -> LOLOS RS gate -> tetap MARKUP_START.
    res = replay(_dataset_bearish_ihsg(), THRESHOLDS, WINDOWS,
                 regime_profiles=PROFILES, horizon=5, warmup=20)
    mk = res[res["date"] == pd.Timestamp(pd.bdate_range("2025-01-01", periods=40)[MARKUP_IDX])]
    assert mk.iloc[0]["regime"] == "BEARISH"
    assert mk.iloc[0]["state"] == "MARKUP_START"
    assert mk.iloc[0]["relative_strength"] > 0


# ---- F7: simulate_exit (SL-first konservatif, spec §5.3) ----
def _lvl(entry=100.0, sl=95.0, tp=110.0):
    return SimpleNamespace(entry=entry, stop_loss=sl, take_profit=tp)


def test_simulate_exit_sl_first_when_one_bar_touches_both():
    # bar1 menyentuh SL (low 94<=95) DAN TP (high 111>=110) -> SL diprioritaskan.
    ohlcv = pd.DataFrame([
        {"open": 100, "high": 100, "low": 100, "close": 100},   # entry bar (idx 0)
        {"open": 100, "high": 111, "low": 94, "close": 100},     # sentuh keduanya
    ])
    res = simulate_exit(ohlcv, 0, _lvl(), horizon=5)
    assert res["exit"] == "SL"
    assert res["ret"] < 0


def test_simulate_exit_tp_when_only_tp_touched():
    ohlcv = pd.DataFrame([
        {"open": 100, "high": 100, "low": 100, "close": 100},
        {"open": 100, "high": 111, "low": 98, "close": 109},
    ])
    res = simulate_exit(ohlcv, 0, _lvl(), horizon=5)
    assert res["exit"] == "TP"
    assert res["ret"] > 0


def test_simulate_exit_timeout_uses_last_close():
    ohlcv = pd.DataFrame([
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 100, "high": 102, "low": 98, "close": 101},
        {"open": 101, "high": 103, "low": 99, "close": 102},
    ])
    res = simulate_exit(ohlcv, 0, _lvl(), horizon=5)
    assert res["exit"] == "TIMEOUT"
    assert res["ret"] == 102 / 100 - 1


# ---- F7: fill realistis + NULL model + ongkos (spec §5.3) ----
def test_backtest_levels_fills_real_breakout():
    ohlcv = _dataset().ohlcv
    summary = backtest_levels(ohlcv, [MARKUP_IDX], horizon=20, levels_kwargs=LEVELS_KW)
    assert summary["n_signals"] == 1
    assert summary["n_filled"] == 1                # breakout beneran terjadi sesudahnya
    assert summary["TP"] + summary["SL"] + summary["TIMEOUT"] == 1


def test_backtest_levels_no_fill_at_data_end():
    # Sinyal di bar terakhir: tak ada bar sesudahnya yang high>=entry -> no fill.
    ohlcv = _dataset().ohlcv
    summary = backtest_levels(ohlcv, [len(ohlcv) - 1], horizon=20, levels_kwargs=LEVELS_KW)
    assert summary["n_signals"] == 1
    assert summary["n_filled"] == 0


def test_cost_reduces_net_return():
    ohlcv = _dataset().ohlcv
    free = backtest_levels(ohlcv, [MARKUP_IDX], levels_kwargs=LEVELS_KW, fee=0.0, slippage=0.0)
    costed = backtest_levels(ohlcv, [MARKUP_IDX], levels_kwargs=LEVELS_KW, fee=0.0015, slippage=0.002)
    assert free["n_filled"] == 1 and costed["n_filled"] == 1
    assert costed["avg_ret_net"] < free["avg_ret_net"]


def test_null_model_deterministic_and_apple_to_apple():
    ohlcv = _dataset().ohlcv
    a = null_model(ohlcv, 5, horizon=20, levels_kwargs=LEVELS_KW, seed=42)
    b = null_model(ohlcv, 5, horizon=20, levels_kwargs=LEVELS_KW, seed=42)
    assert a["n_signals"] == 5
    # seed sama -> entry-bar acak sama -> ringkasan identik (deterministik).
    assert (a["n_filled"], a["TP"], a["SL"], a["TIMEOUT"]) == \
           (b["n_filled"], b["TP"], b["SL"], b["TIMEOUT"])


def test_signal_indices_maps_replay_dates_to_positions():
    ds = _dataset()
    res = replay(ds, THRESHOLDS, WINDOWS, regime_profiles=PROFILES, horizon=5, warmup=20)
    idxs = signal_indices(ds, res)
    assert MARKUP_IDX in idxs
