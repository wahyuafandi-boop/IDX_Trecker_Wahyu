"""Unit test sinyal S1..S9 dengan data sintetis (spec Phase 2)."""

import pandas as pd

from markup_radar.signals import broker_flow, done_ratio, market, price_volume


# ---- S1 Done Ratio ----
def test_done_ratio_buyer_control():
    assert done_ratio.done_ratio(70, 30) == 0.7


def test_done_ratio_no_trades_is_neutral():
    assert done_ratio.done_ratio(0, 0) == 0.5


# ---- S2 Absorption ----
def test_absorption_true_when_done_bid_but_price_held():
    # done-at-bid dominan (ratio<0.45), harga naik, volume tinggi -> absorpsi.
    assert done_ratio.absorption_flag(0.40, price_change=5, rvol=2.0) is True


def test_absorption_false_when_price_drops():
    assert done_ratio.absorption_flag(0.40, price_change=-5, rvol=2.0) is False


def test_absorption_false_when_low_volume():
    assert done_ratio.absorption_flag(0.40, price_change=5, rvol=0.8) is False


# ---- S3 Broker net buy streak ----
def test_broker_streak_counts_trailing_positives():
    assert broker_flow.broker_net_buy_streak([-1, 2, 3, 4]) == 3


def test_broker_streak_zero_when_last_negative():
    assert broker_flow.broker_net_buy_streak([1, 2, -1]) == 0


# ---- S4 Broker concentration ----
def test_broker_concentration_top_heavy():
    df = pd.DataFrame({"net_value": [100, 50, 5, 5, 1, -200]})
    # top-5 buyers = 161 dari total buy 161 -> 1.0
    assert broker_flow.broker_concentration(df, top_n=5) == 1.0


def test_broker_concentration_empty():
    assert broker_flow.broker_concentration(pd.DataFrame(), top_n=5) == 0.0


# ---- S5 Queue imbalance ----
def test_queue_imbalance_demand():
    assert price_volume.queue_imbalance(200, 100) == 2.0


# ---- S6 RVOL ----
def test_rvol_spike():
    vol = pd.Series([100] * 20 + [200])  # hari ini 2x MA20
    assert price_volume.rvol(vol, window=20) == 2.0


# ---- S7 Close in range ----
def test_close_in_range_strong():
    assert price_volume.close_in_range(high=110, low=100, close=109) == 0.9


def test_close_in_range_flat_bar():
    assert price_volume.close_in_range(high=100, low=100, close=100) == 0.5


# ---- S9 IHSG filter ----
def test_ihsg_above_ma50():
    s = pd.Series([100] * 49 + [120])
    assert market.ihsg_above_ma50(s, window=50) is True


def test_ihsg_below_ma50():
    s = pd.Series([100] * 49 + [80])
    assert market.ihsg_above_ma50(s, window=50) is False
