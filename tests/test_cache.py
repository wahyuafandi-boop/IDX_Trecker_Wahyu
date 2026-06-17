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

    def stock_chart(self, code, a, b):
        # dipakai ohlc & ihsg; bedakan via code
        key = "ihsg" if code in ("COMPOSITE", "IHSG") else "ohlc"
        self.calls[key] += 1
        return [
            {"date": d.strftime("%Y-%m-%d"), "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 100}
            for d in self._ohlcv["date"]
        ]

    def momentum_chart(self, code, date, **k):
        self.calls["done"] += 1
        return {"buyLot": 60, "sellLot": 40}

    def inventory_chart_stock(self, code, a, b, **k):
        self.calls["broker"] += 1
        return [{"date": d.strftime("%Y-%m-%d"), "net": 5} for d in self._ohlcv["date"]]


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
