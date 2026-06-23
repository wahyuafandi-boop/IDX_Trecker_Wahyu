"""Unit test trade levels (spec §4.4 / §7 DoD).

Fokus: self-check anti-bug-label (rr_realized ≈ rr_target), floor stop, est_hold
masuk akal, dan None saat data kurang.
"""

import pandas as pd

from markup_radar.signals.levels import TradeLevels, compute_trade_levels


def _ohlcv(closes, spread: float = 4.0) -> pd.DataFrame:
    """Bangun OHLCV sintetis: high/low simetris di sekitar close (spread = range harian)."""
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"high": c + spread / 2, "low": c - spread / 2, "close": c})


def _typical() -> pd.DataFrame:
    # 25 bar naik landai 140 → 152, range harian ~4 (ATR ~4 di harga ~150).
    return _ohlcv([140 + i * 0.5 for i in range(25)], spread=4.0)


# ---- Self-check anti-bug-label: R:R DIHITUNG, bukan dilabel ----
def test_rr_realized_matches_target():
    lv = compute_trade_levels(_typical(), rr_target=2.0)
    assert lv is not None
    assert abs(lv.rr_realized - 2.0) <= 0.05


def test_rr_realized_matches_non_default_target():
    lv = compute_trade_levels(_typical(), rr_target=1.5)
    assert lv is not None
    assert abs(lv.rr_realized - 1.5) <= 0.05


# ---- Floor stop: SL tak pernah lebih ketat dari min_stop_pct ----
def test_floor_stop_active_when_atr_tiny():
    # Harga ~100 nyaris flat (range 0.2 → ATR ~0.2). 2×ATR=0.4 jauh lebih ketat
    # dari 3% (~3.0) → floor WAJIB aktif.
    flat = _ohlcv([100.0] * 25, spread=0.2)
    lv = compute_trade_levels(flat, atr_mult_sl=2.0, min_stop_pct=0.03)
    assert lv is not None
    # stop_pct tak boleh di bawah floor; saat floor aktif ≈ tepat 3%.
    assert lv.stop_pct >= 0.03 - 1e-9
    assert abs(lv.stop_pct - 0.03) <= 1e-3
    # R:R tetap utuh meski floor mengubah jarak SL.
    assert abs(lv.rr_realized - 2.0) <= 0.05


def test_atr_based_stop_when_atr_large():
    # ATR besar (~4 di harga ~150) → 2×ATR=8 mengalahkan floor 3% (~4.6).
    lv = compute_trade_levels(_typical(), atr_mult_sl=2.0, min_stop_pct=0.03)
    assert lv is not None
    assert lv.stop_pct > 0.03   # ATR-based, bukan floor.


# ---- est_hold_days: positif & masuk akal (bukti bukan trade 1-minggu paksaan) ----
def test_est_hold_positive_and_reasonable():
    lv = compute_trade_levels(_typical())
    assert lv is not None
    assert lv.est_hold_days > 0
    assert 5 <= lv.est_hold_days <= 20


# ---- Data kurang dari lookback+1 → None ----
def test_returns_none_when_insufficient_data():
    short = _ohlcv([100 + i for i in range(20)])  # 20 bar < lookback(20)+1
    assert compute_trade_levels(short, lookback=20) is None


def test_returns_none_for_none_input():
    assert compute_trade_levels(None) is None


def test_returns_none_when_flat_zero_atr():
    # Range 0 di semua bar → ATR 0 → tak bisa hitung level.
    zero = _ohlcv([100.0] * 25, spread=0.0)
    assert compute_trade_levels(zero) is None


# ---- Bentuk output ----
def test_output_is_tradelevels_and_dict_roundtrips():
    lv = compute_trade_levels(_typical())
    assert isinstance(lv, TradeLevels)
    d = lv.as_dict()
    assert set(d) == {
        "resistance", "support", "atr", "entry", "stop_loss",
        "take_profit", "rr_realized", "stop_pct", "est_hold_days",
    }
    # entry di atas resis (breakout buffer), TP di atas entry, SL di bawah.
    assert d["entry"] > d["resistance"]
    assert d["take_profit"] > d["entry"] > d["stop_loss"]
