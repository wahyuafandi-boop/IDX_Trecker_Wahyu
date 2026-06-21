"""Test HistoryCache roundtrip + load_history cache-first (hemat API call)."""

import pandas as pd

from markup_radar.backtest import load_history
from markup_radar.store import HistoryCache


def _ohlcv():
    dates = pd.bdate_range("2025-01-01", periods=5)
    return pd.DataFrame(
        {"date": dates, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}
    )


def test_ohlcv_roundtrip(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.put_ohlcv("BBCA", _ohlcv())
    got = c.get_ohlcv("BBCA", "2025-01-01", "2025-01-10")
    assert len(got) == 5
    assert got.iloc[0]["close"] == 1.5
    c.close()


def test_done_missing_dates(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.put_done("BBCA", pd.DataFrame([
        {"date": "2025-01-01", "done_offer_value": 10, "done_bid_value": 5},
    ]))
    assert c.cached_done_dates("BBCA") == {"2025-01-01"}
    c.close()


def test_upsert_overwrites(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.put_ihsg(pd.DataFrame([{"date": "2025-01-01", "close": 7000}]))
    c.put_ihsg(pd.DataFrame([{"date": "2025-01-01", "close": 7100}]))
    got = c.get_ihsg("2025-01-01", "2025-01-01")
    assert len(got) == 1 and got.iloc[0]["close"] == 7100
    c.close()


class CountingClient:
    """Hitung berapa kali tiap endpoint dipanggil."""

    def __init__(self, ohlcv):
        self._ohlcv = ohlcv
        self.calls = {"ohlc": 0, "done": 0, "broker": 0, "ihsg": 0}

    def _ohlcv_rows(self):
        return [
            {"date": d.strftime("%Y-%m-%d"), "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 100}
            for d in self._ohlcv["date"]
        ]

    def stock_chart(self, code, a, b):
        self.calls["ohlc"] += 1
        return self._ohlcv_rows()

    def index_chart(self, code, a, b):
        # IHSG/index — endpoint /analysis/chart/index/{code}
        self.calls["ihsg"] += 1
        return self._ohlcv_rows()

    def momentum_chart(self, code, date, **k):
        self.calls["done"] += 1
        return {"buyLot": 60, "sellLot": 40}

    def inventory_chart_stock(self, code, a, b, **k):
        self.calls["broker"] += 1
        # shape Invezgo: {broker:[{broker,data:[{date,value}]}]}, value kumulatif.
        return {"broker": [
            {"broker": "AA", "data": [
                {"date": d.strftime("%Y-%m-%d"), "value": 5 * (i + 1)}
                for i, d in enumerate(self._ohlcv["date"])
            ]}
        ]}


def test_load_history_uses_cache_on_second_run(tmp_path):
    cache = HistoryCache(tmp_path / "t.db")
    client = CountingClient(_ohlcv())

    ds1 = load_history(client, "BBCA", "2025-01-01", "2025-01-10", cache)
    assert not ds1.ohlcv.empty
    assert client.calls["done"] == 5          # 5 hari ditarik sekali
    first = dict(client.calls)

    # Run kedua: semua dari cache, tidak ada API call tambahan.
    ds2 = load_history(client, "BBCA", "2025-01-01", "2025-01-10", cache)
    assert client.calls == first              # tidak bertambah
    assert len(ds2.done) == 5
    cache.close()


# ---------------------------------------------------------------------------- #
# Coverage tracking (rentang yang sudah di-fetch)
# ---------------------------------------------------------------------------- #
def test_missing_ranges_empty_when_no_coverage(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    assert c.missing_ranges("ohlcv", "BBCA", "2025-01-01", "2025-01-10") == [
        ("2025-01-01", "2025-01-10")
    ]
    c.close()


def test_missing_ranges_full_coverage(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-31")
    assert c.missing_ranges("ohlcv", "BBCA", "2025-01-05", "2025-01-20") == []
    c.close()


def test_missing_ranges_extension_tail(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-05")
    assert c.missing_ranges("ohlcv", "BBCA", "2025-01-01", "2025-01-10") == [
        ("2025-01-06", "2025-01-10")
    ]
    c.close()


def test_missing_ranges_gap_in_middle(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-05")
    c.record_coverage("ohlcv", "BBCA", "2025-01-10", "2025-01-15")
    assert c.missing_ranges("ohlcv", "BBCA", "2025-01-01", "2025-01-15") == [
        ("2025-01-06", "2025-01-09")
    ]
    c.close()


def test_missing_ranges_code_scoped(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-31")
    # coverage BBCA tak berlaku untuk kode lain.
    assert c.missing_ranges("ohlcv", "BMRI", "2025-01-01", "2025-01-10") == [
        ("2025-01-01", "2025-01-10")
    ]
    c.close()


def test_record_coverage_idempotent(tmp_path):
    c = HistoryCache(tmp_path / "t.db")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-05")
    c.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-05")  # dup → diabaikan
    rows = c.conn.execute("SELECT COUNT(*) n FROM cache_coverage").fetchone()["n"]
    assert rows == 1
    c.close()


def test_load_history_refetches_partial_coverage(tmp_path):
    """Regresi: cache parsial TIDAK boleh dianggap lengkap — sisa rentang yang
    hilang harus tetap ditarik (dulu skip-kalau-non-empty → backtest data kurang)."""
    cache = HistoryCache(tmp_path / "t.db")
    client = CountingClient(_ohlcv())

    # Seed cache parsial: data + coverage hanya untuk sub-rentang awal.
    cache.put_ohlcv("BBCA", _ohlcv().iloc[:2])
    cache.record_coverage("ohlcv", "BBCA", "2025-01-01", "2025-01-02")
    cache.record_coverage("ihsg", "", "2025-01-01", "2025-01-02")
    cache.record_coverage("broker_net", "BBCA", "2025-01-01", "2025-01-02")

    load_history(client, "BBCA", "2025-01-01", "2025-01-10", cache)
    assert client.calls["ohlc"] >= 1     # menarik ekor 01-03..01-10
    assert client.calls["ihsg"] >= 1
    assert client.calls["broker"] >= 1
    cache.close()
