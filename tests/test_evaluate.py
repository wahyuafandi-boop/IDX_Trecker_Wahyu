"""Unit test evaluasi forward sinyal produksi (pure, tanpa network)."""

import math

import pandas as pd

from markup_radar.evaluate import forward_metrics, levels_outcome, summarize_by_state


def _frame(closes, highs=None, lows=None, start_day=1):
    """OHLCV sintetis harian: date d01, d02, ... (string, cukup unik utk lookup)."""
    n = len(closes)
    return pd.DataFrame({
        "date": [f"2026-06-{start_day + i:02d}" for i in range(n)],
        "open": closes,
        "high": highs if highs is not None else [c * 1.01 for c in closes],
        "low": lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    })


# --- forward_metrics ---

def test_forward_metrics_basic():
    # close 100 di hari sinyal (idx 2), lalu 110 di +5 bar.
    closes = [95, 98, 100, 102, 104, 106, 108, 110]
    fwd = forward_metrics(_frame(closes), "2026-06-03", horizons=(5,))
    assert fwd is not None
    assert fwd["bars_fwd"] == 5
    assert abs(fwd["fwd_close_5"] - 0.10) < 1e-9
    # MFE dari high (+1%), MAE dari low (-1%) di window forward.
    assert fwd["fwd_max"] > 0.10
    assert fwd["fwd_min"] < 0.03


def test_forward_metrics_young_signal_is_nan():
    # Sinyal di bar terakhir kedua: cuma 1 bar forward -> fwd_close_5 NaN.
    fwd = forward_metrics(_frame([100, 100, 105]), "2026-06-02", horizons=(5,))
    assert fwd is not None
    assert fwd["bars_fwd"] == 1
    assert math.isnan(fwd["fwd_close_5"])
    assert not math.isnan(fwd["fwd_max"])  # MFE tetap dari bar yang ada


def test_forward_metrics_date_missing_returns_none():
    assert forward_metrics(_frame([100, 101]), "2099-01-01") is None
    assert forward_metrics(pd.DataFrame(), "2026-06-01") is None


# --- levels_outcome ---

_LEVELS = {"entry": 105.0, "stop_loss": 95.0, "take_profit": 125.0}


def test_levels_outcome_fill_then_tp():
    # Sinyal idx 0; +1 high menembus entry 105 (fill), lalu naik sampai TP 125.
    closes = [100, 106, 112, 120, 126, 128]
    highs = [101, 107, 113, 121, 127, 129]
    lows = [99, 104, 110, 118, 124, 126]
    out = levels_outcome(_frame(closes, highs, lows), "2026-06-01", _LEVELS)
    assert out["filled"] is True
    assert out["exit"] == "TP"
    assert abs(out["ret"] - (125.0 / 105.0 - 1)) < 1e-9


def test_levels_outcome_no_fill():
    # Harga tak pernah menyentuh entry dalam trigger_window -> no-trade.
    closes = [100, 101, 100, 99, 100, 101, 100]
    highs = [101, 102, 101, 100, 101, 102, 101]
    out = levels_outcome(_frame(closes, highs), "2026-06-01", _LEVELS,
                         trigger_window=5)
    assert out == {"filled": False, "exit": "NO_FILL", "bars": 0,
                   "ret": out["ret"]} and math.isnan(out["ret"])


def test_levels_outcome_sl_first_conservative():
    # Bar setelah fill menyentuh SL dan TP sekaligus -> SL menang (konservatif).
    closes = [100, 106, 110]
    highs = [101, 107, 126]   # bar idx2: high >= TP 125
    lows = [99, 104, 94]      # ... dan low <= SL 95
    out = levels_outcome(_frame(closes, highs, lows), "2026-06-01", _LEVELS)
    assert out["filled"] is True
    assert out["exit"] == "SL"


def test_levels_outcome_invalid_levels_returns_none():
    assert levels_outcome(_frame([100, 101]), "2026-06-01", {}) is None
    assert levels_outcome(_frame([100, 101]), "2026-06-01",
                          {"entry": 0, "stop_loss": 95, "take_profit": 120}) is None


# --- summarize_by_state ---

def test_summarize_by_state_winrate_and_median():
    rows = [
        {"state": "MARKUP_START", "fwd_close_5": 0.05},
        {"state": "MARKUP_START", "fwd_close_5": -0.02},
        {"state": "MARKUP_START", "fwd_close_5": 0.10},
        {"state": "MARKUP_START", "fwd_close_5": math.nan},  # sinyal muda: di-skip
        {"state": "NEUTRAL", "fwd_close_5": 0.01},
    ]
    out = {s["state"]: s for s in summarize_by_state(rows, horizons=(5,))}
    ms = out["MARKUP_START"]
    assert ms["n"] == 4 and ms["n_5"] == 3
    assert abs(ms["win_5"] - 2 / 3) < 1e-9
    assert abs(ms["med_5"] - 0.05) < 1e-9
    assert out["NEUTRAL"]["win_5"] == 1.0
