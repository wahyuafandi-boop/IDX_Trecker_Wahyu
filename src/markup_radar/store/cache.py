"""Cache data historis di SQLite (hemat API call untuk backtest).

Tiap time-series disimpan per (code, date) sehingga backtest bisa di-replay
berulang tanpa menarik ulang dari Invezgo. Done detail (per-tanggal, paling
mahal) cukup ditarik sekali lalu di-cache.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_ohlcv (
    code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS cache_done (
    code TEXT, date TEXT, done_offer_value REAL, done_bid_value REAL,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS cache_broker_net (
    code TEXT, date TEXT, net REAL,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS cache_foreign (
    code TEXT, date TEXT, net REAL,
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS cache_ihsg (
    date TEXT PRIMARY KEY, close REAL
);
-- Lacak rentang KALENDER (bukan per-baris) yang sudah benar-benar di-fetch dari
-- API, per (series, code). Tanpa ini, load_history menganggap cache parsial =
-- lengkap (get_ohlcv non-empty → tak menarik sisa lama) → backtest diam-diam
-- pakai data kurang. code="" untuk series market-wide (ihsg).
CREATE TABLE IF NOT EXISTS cache_coverage (
    series TEXT NOT NULL, code TEXT NOT NULL,
    date_from TEXT NOT NULL, date_to TEXT NOT NULL,
    PRIMARY KEY (series, code, date_from, date_to)
);
"""


def _iso(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _date(value) -> dt.date:
    return pd.Timestamp(value).date()


class HistoryCache:
    """Penyimpanan time-series historis berbasis SQLite."""

    def __init__(self, db_path: str | Path = "data/markup_radar.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ #
    # Generic upsert / read
    # ------------------------------------------------------------------ #
    def _upsert(self, table: str, value_cols: list[str], df: pd.DataFrame, code: str | None) -> None:
        if df is None or df.empty:
            return
        key_cols = (["code", "date"] if code is not None else ["date"])
        all_cols = key_cols + value_cols
        placeholders = ",".join("?" * len(all_cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in value_cols)
        conflict = ",".join(key_cols)
        sql = (
            f"INSERT INTO {table} ({','.join(all_cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
        )
        records = []
        for _, r in df.iterrows():
            vals = [None if pd.isna(r.get(c)) else r.get(c) for c in value_cols]
            head = [code, _iso(r["date"])] if code is not None else [_iso(r["date"])]
            records.append((*head, *vals))
        self.conn.executemany(sql, records)
        self.conn.commit()

    def _read(self, table: str, value_cols: list[str], code: str | None,
              date_from, date_to) -> pd.DataFrame:
        where = "date BETWEEN ? AND ?"
        params: list = [_iso(date_from), _iso(date_to)]
        if code is not None:
            where = "code = ? AND " + where
            params = [code, *params]
        sel = ",".join(["date"] + value_cols)
        cur = self.conn.execute(f"SELECT {sel} FROM {table} WHERE {where} ORDER BY date", params)
        return pd.DataFrame([dict(r) for r in cur.fetchall()], columns=["date"] + value_cols)

    # ------------------------------------------------------------------ #
    # OHLCV
    # ------------------------------------------------------------------ #
    _OHLCV = ["open", "high", "low", "close", "volume"]

    def put_ohlcv(self, code: str, df: pd.DataFrame) -> None:
        self._upsert("cache_ohlcv", self._OHLCV, df, code)

    def get_ohlcv(self, code: str, date_from, date_to) -> pd.DataFrame:
        return self._read("cache_ohlcv", self._OHLCV, code, date_from, date_to)

    # ------------------------------------------------------------------ #
    # Done (per-tanggal — paling mahal)
    # ------------------------------------------------------------------ #
    _DONE = ["done_offer_value", "done_bid_value"]

    def put_done(self, code: str, df: pd.DataFrame) -> None:
        self._upsert("cache_done", self._DONE, df, code)

    def get_done(self, code: str, date_from, date_to) -> pd.DataFrame:
        return self._read("cache_done", self._DONE, code, date_from, date_to)

    def cached_done_dates(self, code: str) -> set[str]:
        cur = self.conn.execute("SELECT date FROM cache_done WHERE code = ?", (code,))
        return {r["date"] for r in cur.fetchall()}

    # ------------------------------------------------------------------ #
    # Broker net / Foreign / IHSG
    # ------------------------------------------------------------------ #
    def put_broker_net(self, code: str, df: pd.DataFrame) -> None:
        self._upsert("cache_broker_net", ["net"], df, code)

    def get_broker_net(self, code: str, date_from, date_to) -> pd.DataFrame:
        return self._read("cache_broker_net", ["net"], code, date_from, date_to)

    def put_foreign(self, code: str, df: pd.DataFrame) -> None:
        self._upsert("cache_foreign", ["net"], df, code)

    def get_foreign(self, code: str, date_from, date_to) -> pd.DataFrame:
        return self._read("cache_foreign", ["net"], code, date_from, date_to)

    def put_ihsg(self, df: pd.DataFrame) -> None:
        self._upsert("cache_ihsg", ["close"], df, None)

    def get_ihsg(self, date_from, date_to) -> pd.DataFrame:
        return self._read("cache_ihsg", ["close"], None, date_from, date_to)

    # ------------------------------------------------------------------ #
    # Coverage tracking (rentang yang sudah di-fetch)
    # ------------------------------------------------------------------ #
    def record_coverage(self, series: str, code: str | None, date_from, date_to) -> None:
        """Tandai rentang kalender [date_from, date_to] sudah ditarik dari API
        untuk (series, code). Idempoten (PRIMARY KEY mengabaikan duplikat)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO cache_coverage (series, code, date_from, date_to) "
            "VALUES (?, ?, ?, ?)",
            (series, code or "", _iso(date_from), _iso(date_to)),
        )
        self.conn.commit()

    def missing_ranges(
        self, series: str, code: str | None, date_from, date_to
    ) -> list[tuple[str, str]]:
        """Sub-rentang dari [date_from, date_to] yang BELUM tercakup coverage.

        Kembalikan list (from, to) hari-granular. Kosong = sudah lengkap; satu
        rentang penuh = belum ada coverage sama sekali. Dipakai load_history
        untuk menarik hanya bagian yang hilang (bukan skip-kalau-non-empty)."""
        lo, hi = _date(date_from), _date(date_to)
        if lo > hi:
            return []
        cur = self.conn.execute(
            "SELECT date_from, date_to FROM cache_coverage WHERE series = ? AND code = ?",
            (series, code or ""),
        )
        spans = []
        for r in cur.fetchall():
            a = max(_date(r["date_from"]), lo)
            b = min(_date(r["date_to"]), hi)
            if a <= b:
                spans.append((a, b))
        spans.sort()

        one = dt.timedelta(days=1)
        missing: list[tuple[str, str]] = []
        cursor = lo
        for a, b in spans:
            if a > cursor:
                missing.append((cursor.isoformat(), (a - one).isoformat()))
            if b + one > cursor:
                cursor = b + one
            if cursor > hi:
                break
        if cursor <= hi:
            missing.append((cursor.isoformat(), hi.isoformat()))
        return missing

    def close(self) -> None:
        self.conn.close()
