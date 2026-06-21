"""Wadah data historis untuk replay + builder dari Invezgo API.

HistoricalDataset menampung time-series yang dibutuhkan engine. Semua opsional
kecuali OHLCV; sinyal yang datanya kosong akan degrade (mode degraded spec §7).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class HistoricalDataset:
    """Data EOD historis satu saham (date-indexed)."""

    code: str
    ohlcv: pd.DataFrame                                   # date, open, high, low, close, volume
    done: pd.DataFrame = field(default_factory=pd.DataFrame)        # date, done_offer_value, done_bid_value
    broker_daily_net: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, net
    closing_queue: pd.DataFrame = field(default_factory=pd.DataFrame)     # date, bid_volume, offer_volume
    foreign: pd.DataFrame = field(default_factory=pd.DataFrame)     # date, net
    ihsg: pd.DataFrame = field(default_factory=pd.DataFrame)        # date, close

    def __post_init__(self) -> None:
        for name in ("ohlcv", "done", "broker_daily_net", "closing_queue", "foreign", "ihsg"):
            df = getattr(self, name)
            if isinstance(df, pd.DataFrame) and not df.empty and "date" in df:
                df = df.copy()
                # Normalisasi ke tz-naive seragam: di path --no-cache OHLCV dari
                # fetch_ohlcv bisa tz-aware (datetime64[us, UTC]) sedangkan done
                # dirakit dari string "YYYY-MM-DD" (tz-naive). Tanpa strip tz,
                # _lookup (date == date) tak pernah match → done_ratio degrade 0.5.
                # Path cache aman (date string) & tetap tz-naive setelah ini.
                df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
                setattr(self, name, df.sort_values("date").reset_index(drop=True))

    @property
    def dates(self) -> list[pd.Timestamp]:
        return list(self.ohlcv["date"]) if not self.ohlcv.empty else []


def _lookup(df: pd.DataFrame, date: pd.Timestamp, col: str, default=0.0) -> float:
    if df.empty or col not in df:
        return default
    row = df[df["date"] == date]
    if row.empty:
        return default
    val = row.iloc[0][col]
    return float(val) if pd.notna(val) else default


def load_history(
    client, code: str, date_from: str, date_to: str, cache=None
) -> HistoricalDataset:
    """Rakit HistoricalDataset dari Invezgo API, dengan cache SQLite opsional.

    Bila `cache` (store.HistoryCache) diberikan, data dibaca dari cache lebih
    dulu dan hanya tanggal yang belum ada yang ditarik dari API — krusial untuk
    done detail (momentum-chart) yang per-tanggal & mahal.

    Field done/broker bergantung shape yang masih TODO(verify).
    """
    from markup_radar.ingest.broker_client import fetch_broker_daily_net_dated
    from markup_radar.ingest.done_client import fetch_done_breakdown
    from markup_radar.ingest.ihsg_client import fetch_ihsg
    from markup_radar.ingest.ohlc_client import fetch_ohlcv

    # --- OHLCV (1 call) ---
    ohlcv = cache.get_ohlcv(code, date_from, date_to) if cache else pd.DataFrame()
    if ohlcv.empty:
        ohlcv = fetch_ohlcv(client, code, date_from, date_to)
        if cache:
            cache.put_ohlcv(code, ohlcv)

    # --- IHSG (1 call, market-wide) ---
    ihsg = cache.get_ihsg(date_from, date_to) if cache else pd.DataFrame()
    if ihsg.empty:
        ihsg = fetch_ihsg(client, date_from, date_to)
        if cache:
            cache.put_ihsg(ihsg)

    # --- Broker daily net (1 call) — join BY-DATE pakai tanggal asli broker.
    # Hindari align positional `ohlcv["date"][-len(net):]` yang rapuh: date-set
    # broker bisa beda dari OHLCV (ValueError saat lebih panjang / mislabel hari).
    broker_df = cache.get_broker_net(code, date_from, date_to) if cache else pd.DataFrame()
    if broker_df.empty:
        dated = fetch_broker_daily_net_dated(client, code, date_from, date_to)
        if dated:
            broker_df = pd.DataFrame(dated, columns=["date", "net"])
            if cache:
                cache.put_broker_net(code, broker_df)

    # --- Done detail (per-tanggal): tarik hanya tanggal yang belum di-cache ---
    dates = list(ohlcv["date"]) if not ohlcv.empty else []
    have = cache.cached_done_dates(code) if cache else set()
    fresh_rows = []
    for d in dates:
        ds = pd.Timestamp(d).strftime("%Y-%m-%d")
        if ds in have:
            continue
        try:
            fresh_rows.append({"date": ds, **fetch_done_breakdown(client, code, ds)})
        except Exception:  # noqa: BLE001 — degrade per tanggal
            continue
    if fresh_rows and cache:
        cache.put_done(code, pd.DataFrame(fresh_rows))
    done = cache.get_done(code, date_from, date_to) if cache else pd.DataFrame(fresh_rows)

    return HistoricalDataset(
        code=code,
        ohlcv=ohlcv,
        done=done,
        broker_daily_net=broker_df,
        ihsg=ihsg,
    )
