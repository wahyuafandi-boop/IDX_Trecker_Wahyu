"""Test optimasi ingest: rate limiter, foreign map, broker daily-net parsing."""

import time

from markup_radar.ingest.broker_client import fetch_broker_daily_net
from markup_radar.ingest.client import _RateLimiter
from markup_radar.ingest.foreign_client import fetch_foreign_map, foreign_net_for


class FakeClient:
    """Stub InvezgoClient: catat panggilan, balikan payload yang disetel."""

    def __init__(self, inventory=None, foreign=None):
        self._inventory = inventory or []
        self._foreign = foreign or []
        self.calls = 0

    def inventory_chart_stock(self, code, date_from, date_to, **kw):
        self.calls += 1
        return self._inventory

    def top_foreign(self, date):
        self.calls += 1
        return self._foreign


# ---- Rate limiter ----
def test_rate_limiter_allows_within_budget_without_blocking():
    rl = _RateLimiter(max_per_min=1000)
    start = time.monotonic()
    for _ in range(50):
        rl.acquire()
    assert time.monotonic() - start < 0.5  # tidak nge-sleep


def test_rate_limiter_disabled_when_zero():
    rl = _RateLimiter(max_per_min=0)
    for _ in range(10):
        rl.acquire()
    assert len(rl._hits) == 0


def test_rate_limiter_tracks_hits():
    rl = _RateLimiter(max_per_min=1000)
    for _ in range(5):
        rl.acquire()
    assert len(rl._hits) == 5


# ---- Broker daily net (1 call) ----
def test_broker_daily_net_topn_cumulative_delta():
    # Shape Invezgo: {broker:[{broker,data:[{date,value}]}]}, value = KUMULATIF.
    inv = {
        "broker": [
            {"broker": "AA", "data": [
                {"date": "2026-06-11", "value": 30},   # sengaja tak urut
                {"date": "2026-06-10", "value": 10},
                {"date": "2026-06-12", "value": 60},
            ]},
            {"broker": "BB", "data": [
                {"date": "2026-06-10", "value": 5},
                {"date": "2026-06-11", "value": 15},
                {"date": "2026-06-12", "value": 20},
            ]},
        ]
    }
    client = FakeClient(inventory=inv)
    out = fetch_broker_daily_net(client, "BBCA", "2026-06-10", "2026-06-12")
    # agregat kumulatif top-N per tgl = [15, 45, 80] -> delta harian:
    assert out == [15.0, 30.0, 35.0]
    assert client.calls == 1  # hanya 1 API call


def test_broker_daily_net_topn_excludes_distributors():
    inv = {"broker": [
        {"broker": "ACC", "data": [{"date": "2026-06-10", "value": 100},
                                   {"date": "2026-06-11", "value": 200}]},
        {"broker": "DIST", "data": [{"date": "2026-06-10", "value": -50},
                                    {"date": "2026-06-11", "value": -300}]},
    ]}
    client = FakeClient(inventory=inv)
    out = fetch_broker_daily_net(client, "X", "a", "b", top_n=1)
    # hanya akumulator teratas (ACC): kumulatif [100, 200] -> [100, 100]
    assert out == [100.0, 100.0]


def test_broker_daily_net_empty_on_error():
    class Boom:
        def inventory_chart_stock(self, *a, **k):
            raise RuntimeError("nope")

    assert fetch_broker_daily_net(Boom(), "X", "a", "b") == []


# ---- Foreign map (1 call, lookup lokal) ----
def test_foreign_map_and_lookup():
    rows = [
        {"code": "BBRI", "netValue": 1000},
        {"symbol": "tlkm", "net": -500},
    ]
    client = FakeClient(foreign=rows)
    fmap = fetch_foreign_map(client, "2026-06-16")
    assert client.calls == 1
    assert foreign_net_for("BBRI", fmap) == 1000.0
    assert foreign_net_for("TLKM", fmap) == -500.0
    assert foreign_net_for("ASII", fmap) == 0.0  # di luar top list
