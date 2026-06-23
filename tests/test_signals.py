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


def test_queue_verdict_labels():
    # demand default 1.0, neutral_low 0.8
    assert price_volume.queue_verdict(1.5) == "DEMAND_DOMINAN"
    assert price_volume.queue_verdict(0.9) == "SEIMBANG"
    assert price_volume.queue_verdict(0.5) == "SUPPLY_DOMINAN"
    assert price_volume.queue_verdict(0.0) == "NO_DATA"


def test_queue_verdict_respects_demand_threshold():
    # demand 1.2: imbalance 1.1 belum cukup dominan -> SEIMBANG.
    assert price_volume.queue_verdict(1.1, demand=1.2) == "SEIMBANG"
    assert price_volume.queue_verdict(1.25, demand=1.2) == "DEMAND_DOMINAN"


# ---- S5 komposisi (tape-reading bandarmologi) ----
def test_lot_per_order():
    assert price_volume.lot_per_order(2000, 20) == 100.0
    assert price_volume.lot_per_order(100, 0) == 0.0   # freq 0 -> tak bisa disimpulkan


def _comp(b_lot, b_freq, o_lot, o_freq):
    return price_volume.queue_composition_verdict(b_lot, b_freq, o_lot, o_freq)


def test_composition_passive_accum():
    # offer LEBIH berat (imb 0.5) tapi bid diisi order jumbo (200/2=100 lot/order)
    # vs offer ritel (400/40=10) -> bandar nampung diam-diam.
    assert _comp(200, 2, 400, 40) == "PASSIVE_ACCUM"


def test_composition_passive_distrib():
    # rasio kelihatan demand (imb 2.0) tapi offer = big money (500/5=100) menekan
    # vs bid ritel (1000/50=20) -> jual diam-diam, jangan beli.
    assert _comp(1000, 50, 500, 5) == "PASSIVE_DISTRIB"


def test_composition_ritel_noise():
    # kedua sisi order kecil acak (lpo 10) -> rasio tak bisa dipercaya.
    assert _comp(300, 30, 300, 30) == "RITEL_NOISE"
    # imb ekstrem pun tetap noise kalau tak ada big money.
    assert _comp(1000, 100, 100, 10) == "RITEL_NOISE"


def test_composition_clear_ratio_with_bigmoney():
    # big money kedua sisi & bid jelas dominan (lpo 200>100, imb 3.0) -> DEMAND terang.
    assert _comp(600, 3, 200, 2) == "DEMAND_DOMINAN"
    # offer = big money & dominan kuat (lpo offer 200>bid 50, imb 0.25) -> SUPPLY terang.
    assert _comp(200, 4, 800, 4) == "SUPPLY_DOMINAN"


def test_composition_edges():
    assert _comp(0, 0, 0, 0) == "NO_DATA"
    assert _comp(500, 5, 0, 0) == "DEMAND_DOMINAN"   # tak ada antri jual
    assert _comp(0, 0, 500, 5) == "SUPPLY_DOMINAN"   # tak ada antri beli


# ---- S5 intent (fake-over / fake-bid + konteks akumulasi) ----
def _intent(b_lot, b_freq, o_lot, o_freq, accumulating):
    return price_volume.queue_intent_verdict(
        b_lot, b_freq, o_lot, o_freq, accumulating=accumulating)


def test_intent_fake_over_vs_supply_real():
    # Tembok offer (8000 lot / 4 freq = 2000 lot/order), offer lebih berat dari bid.
    # + akumulasi -> FAKE_OVER (MM tahan harga buat nampung = bullish).
    assert _intent(2000, 20, 8000, 4, True) == "FAKE_OVER"
    # tanpa akumulasi -> suplai/distribusi asli.
    assert _intent(2000, 20, 8000, 4, False) == "SUPPLY_REAL"


def test_intent_fake_bid_vs_demand_real():
    # Tembok bid (8000 lot / 4 freq = 2000 lot/order), bid lebih berat dari offer.
    # tanpa akumulasi -> FAKE_BID (ilusi demand sambil distribusi = jebakan).
    assert _intent(8000, 4, 2000, 20, False) == "FAKE_BID"
    # + akumulasi -> demand asli.
    assert _intent(8000, 4, 2000, 20, True) == "DEMAND_REAL"


def test_intent_falls_back_to_composition_when_no_bigmoney():
    # Tak ada tembok big money (kedua sisi lpo kecil) -> baca komposisi murni.
    assert _intent(300, 30, 300, 30, True) == "RITEL_NOISE"
    # nampung tersembunyi: offer berat retail, bid big money -> PASSIVE_ACCUM.
    assert _intent(200, 2, 400, 40, False) == "PASSIVE_ACCUM"


def test_intent_no_data():
    assert _intent(0, 0, 0, 0, True) == "NO_DATA"


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


# ---- ATR (trade levels primitive) ----
def test_atr_constant_true_range():
    # Tiap bar range = 2 dan tak ada gap signifikan -> ATR = 2.0.
    high = [12, 12, 12]
    low = [10, 10, 10]
    close = [11, 11, 11]
    assert price_volume.atr(high, low, close, period=14) == 2.0


def test_atr_includes_gap_in_true_range():
    # Bar0 TR = h-l = 1; bar1 gap naik -> TR = |high-prev_close| = 20-9 = 11.
    # ATR = mean([1, 11]) = 6.0 (membuktikan gap masuk hitungan).
    assert price_volume.atr([10, 20], [9, 19], [9, 19], period=14) == 6.0


def test_atr_empty_is_zero():
    assert price_volume.atr([], [], [], period=14) == 0.0


# ---- Donchian channel ----
def test_donchian_window_max_min():
    high = [10, 15, 12, 20, 18]
    low = [5, 8, 6, 9, 7]
    # lookback melebihi data -> ambil semua: max high 20, min low 5.
    assert price_volume.donchian(high, low, lookback=20) == (20.0, 5.0)


def test_donchian_respects_lookback():
    high = [10, 15, 12, 20, 18]
    low = [5, 8, 6, 9, 7]
    # 2 bar terakhir: highs [20,18] -> 20; lows [9,7] -> 7.
    assert price_volume.donchian(high, low, lookback=2) == (20.0, 7.0)


def test_donchian_empty_is_zero():
    assert price_volume.donchian([], []) == (0.0, 0.0)


# ---- Regime selector ----
def test_market_regime_bullish():
    s = pd.Series([100] * 49 + [120])
    assert market.market_regime(s, window=50) is market.Regime.BULLISH


def test_market_regime_bearish():
    s = pd.Series([100] * 49 + [80])
    assert market.market_regime(s, window=50) is market.Regime.BEARISH


def test_market_regime_empty_is_bearish_failsafe():
    # Data kosong -> profil ketat (BEARISH), bukan BULLISH.
    assert market.market_regime(pd.Series([], dtype=float)) is market.Regime.BEARISH


# ---- S10 Relative Strength ----
def test_relative_strength_outperform_positive():
    stock = [100, 100, 110]   # +10%
    ihsg = [100, 100, 100]    # 0%
    assert market.relative_strength(stock, ihsg, window=2) > 0


def test_relative_strength_underperform_negative():
    stock = [100, 100, 90]    # -10%
    ihsg = [100, 100, 110]    # +10%
    assert market.relative_strength(stock, ihsg, window=2) < 0


def test_relative_strength_insufficient_data_is_zero():
    # Kurang dari window+1 data -> 0.0 (tak menggate apa-apa).
    assert market.relative_strength([100, 100], [100, 100, 100], window=20) == 0.0
