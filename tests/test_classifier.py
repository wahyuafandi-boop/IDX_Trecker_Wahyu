"""Unit test rule engine §4 (state classification) + confidence."""

from markup_radar.scoring import classify, confidence_markup_start


def _base(**over):
    s = {
        "done_ratio": 0.5,
        "absorption_flag": False,
        "broker_net_buy_streak": 0,
        "broker_concentration": 0.0,
        "broker_turning_net_sell": False,
        "queue_imbalance": 1.0,
        "rvol": 1.0,
        "close_in_range": 0.5,
        "near_range_high": False,
        "price_ranging": False,
        "foreign_net": 0.0,
        "ihsg_above_ma50": True,
    }
    s.update(over)
    return s


def test_markup_start():
    # Gate dasar lolos tapi S5 (queue) belum menumpuk → tier START, bukan CONFIRMED.
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=0.8, ihsg_above_ma50=True)
    assert classify(s) == "MARKUP_START"


def test_markup_confirmed_when_queue_demand_stacks():
    # Gate dasar + antrian beli menumpuk di close (S5 >= demand) → tier konfirmasi.
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=1.2, ihsg_above_ma50=True)
    assert classify(s) == "MARKUP_CONFIRMED"


def test_markup_confirmed_needs_base_gate():
    # Queue tinggi saja tak cukup; gate dasar (done/rvol/cir/streak) tetap wajib.
    s = _base(done_ratio=0.50, rvol=1.0, queue_imbalance=2.0)
    assert classify(s) == "NEUTRAL"


def test_backtest_no_queue_data_stays_markup_start():
    # Di backtest S5 tak historis → queue_imbalance=0 < demand → tetap MARKUP_START.
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=0.0)
    assert classify(s) == "MARKUP_START"


def test_markup_not_vetoed_by_bearish_ihsg_but_lower_confidence():
    # IHSG bukan lagi veto keras (tuned 2026-06-21) — sinyal tetap MARKUP_START,
    # tapi market lemah menekan confidence (bobot `ihsg`), bukan memblokir.
    strong = dict(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
                  broker_net_buy_streak=3, queue_imbalance=0.8)
    bull = _base(**strong, ihsg_above_ma50=True)
    bear = _base(**strong, ihsg_above_ma50=False)
    assert classify(bear) == "MARKUP_START"
    assert classify(bull) == "MARKUP_START"
    assert confidence_markup_start(bear) < confidence_markup_start(bull)


# ---- S10 Relative Strength gate (opt-in via profil regime, spec §4.6) ----
def _bearish_eff(**over):
    """Threshold ala profil BEARISH ter-merge: RS wajib + rvol dinaikkan."""
    t = {"require_relative_strength": True, "rs_min": 0.0, "rvol_spike": 2.5}
    t.update(over)
    return t


def test_bearish_blocks_markup_when_underperform():
    # done/rvol/cir/streak semua lolos, TAPI saham underperform IHSG (rs < rs_min)
    # di regime BEARISH → RS gate memblok → NEUTRAL, bukan MARKUP.
    s = _base(done_ratio=0.68, rvol=2.6, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=0.0, relative_strength=-0.02)
    assert classify(s, _bearish_eff()) == "NEUTRAL"


def test_bearish_allows_markup_when_outperform():
    # Sama persis tapi outperform (rs > rs_min) → lolos gate → MARKUP_START.
    s = _base(done_ratio=0.68, rvol=2.6, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=0.0, relative_strength=0.03)
    assert classify(s, _bearish_eff()) == "MARKUP_START"


def test_rs_gate_noop_when_not_required_backward_compat():
    # Regresi backward-compat: tanpa profil (require_relative_strength absent/False),
    # klausa RS tak berpengaruh — bahkan rs sangat negatif tetap MARKUP_START.
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, queue_imbalance=0.0, relative_strength=-0.5)
    assert classify(s) == "MARKUP_START"
    # rs absent sama sekali → identik (klausa no-op).
    s2 = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
               broker_net_buy_streak=3, queue_imbalance=0.0)
    assert classify(s2) == "MARKUP_START"


def test_accumulation_via_absorption():
    s = _base(absorption_flag=True)
    assert classify(s) == "ACCUMULATION_ONGOING"


def test_accumulation_via_quiet_broker_buying():
    s = _base(broker_net_buy_streak=4, price_ranging=True, done_ratio=0.50)
    assert classify(s) == "ACCUMULATION_ONGOING"


def test_distribution_warning():
    s = _base(done_ratio=0.35, broker_turning_net_sell=True, near_range_high=True)
    assert classify(s) == "DISTRIBUTION_WARNING"


def test_neutral():
    assert classify(_base()) == "NEUTRAL"


def test_insufficient_data():
    assert classify({}) == "INSUFFICIENT_DATA"


def test_confidence_high_for_strong_markup():
    # "Strong" kini termasuk outperform IHSG (relative_strength) sejak bobot S10
    # masuk score (spec §4.7) — perfect score butuh komponen RS juga.
    s = _base(done_ratio=0.8, rvol=3.0, close_in_range=1.0,
              broker_net_buy_streak=5, queue_imbalance=2.0, ihsg_above_ma50=True,
              relative_strength=0.10)
    assert confidence_markup_start(s) == 100


def test_confidence_low_for_weak_signal():
    assert confidence_markup_start(_base()) < 40
