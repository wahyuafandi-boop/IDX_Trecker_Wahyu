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
                df["date"] = pd.to_datetime(df["date"])
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


def load_history(client, code: str, date_from: str, date_to: str) -> HistoricalDataset:
    """Rakit HistoricalDataset dari Invezgo API.

    ⚠️ API-HEAVY: done detail (momentum-chart) bersifat per-tanggal sehingga
    ditarik per hari bursa. Untuk universe besar / rentang panjang, cache hasil
    ke SQLite dulu. Field done/broker bergantung shape yang masih TODO(verify).
    """
    from markup_radar.ingest.broker_client import fetch_broker_daily_net
    from markup_radar.ingest.done_client import fetch_done_breakdown
    from markup_radar.ingest.ihsg_client import fetch_ihsg
    from markup_radar.ingest.ohlc_client import fetch_ohlcv

    ohlcv = fetch_ohlcv(client, code, date_from, date_to)
    ihsg = fetch_ihsg(client, date_from, date_to)

    net = fetch_broker_daily_net(client, code, date_from, date_to)
    broker_df = pd.DataFrame(
        {"date": list(ohlcv["date"])[-len(net):], "net": net}
    ) if net and not ohlcv.empty else pd.DataFrame()

    done_rows = []
    for d in ohlcv["date"] if not ohlcv.empty else []:
        ds = pd.Timestamp(d).date().isoformat()
        try:
            br = fetch_done_breakdown(client, code, ds)
            done_rows.append({"date": ds, **br})
        except Exception:  # noqa: BLE001 — degrade per tanggal
            continue

    return HistoricalDataset(
        code=code,
        ohlcv=ohlcv,
        done=pd.DataFrame(done_rows),
        broker_daily_net=broker_df,
        ihsg=ihsg,
    )
