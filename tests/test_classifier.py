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
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, ihsg_above_ma50=True)
    assert classify(s) == "MARKUP_START"


def test_markup_blocked_when_ihsg_bearish():
    s = _base(done_ratio=0.68, rvol=2.3, close_in_range=0.8,
              broker_net_buy_streak=3, ihsg_above_ma50=False)
    assert classify(s) != "MARKUP_START"


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
    s = _base(done_ratio=0.8, rvol=3.0, close_in_range=1.0,
              broker_net_buy_streak=5, queue_imbalance=2.0, ihsg_above_ma50=True)
    assert confidence_markup_start(s) == 100


def test_confidence_low_for_weak_signal():
    assert confidence_markup_start(_base()) < 40
