"""Normalisasi OHLCV harian (S6 RVOL, S7 close-in-range).

Catatan kapasitas: endpoint `stock_chart` Invezgo membatasi ~6 bln (~120 hari
bursa) per request, mengembalikan jendela TERAKHIR yang berakhir di `to`
(parameter `from` efektif diabaikan bila rentang > cap). Untuk backtest panjang,
`fetch_ohlcv` menarik per-chunk dengan `to` digeser mundur lalu di-stitch.
Tanggal Invezgo berupa UTC midnight ('...T00:00:00.000Z') -> dinormalisasi ke
tz-naive (tanpa pergeseran tanggal).
"""

from __future__ import annotations

import sys

import pandas as pd

from markup_radar.ingest.client import InvezgoClient

_COLS = ["date", "open", "high", "low", "close", "volume"]


def _pick(row: dict, *keys: str, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def _fetch_ohlcv_chunk(client: InvezgoClient, code: str, date_from: str, date_to: str) -> pd.DataFrame:
    """Satu request stock_chart -> DataFrame[date, open, high, low, close, volume].

    Defensif terhadap variasi nama field (shape response perlu diverifikasi).
    """
    raw = client.stock_chart(code, date_from, date_to)
    rows = raw if isinstance(raw, list) else raw.get("items", raw.get("data", []))

    records = []
    for r in rows:
        records.append(
            {
                "date": _pick(r, "date", "time", "timestamp"),
                "open": _pick(r, "open", "o"),
                "high": _pick(r, "high", "h"),
                "low": _pick(r, "low", "l"),
                "close": _pick(r, "close", "c"),
                "volume": _pick(r, "volume", "v", "vol"),
            }
        )
    df = pd.DataFrame.from_records(records, columns=_COLS)
    if df.empty:
        return df
    # UTC midnight ('Z') -> tz-naive tanpa geser tanggal.
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    # Invezgo kirim volume (dan kadang OHLC) sebagai STRING -> coerce ke angka.
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def fetch_ohlcv(
    client: InvezgoClient, code: str, date_from: str, date_to: str, *, max_chunks: int = 12
) -> pd.DataFrame:
    """Tarik OHLCV harian [date_from..date_to], stitch antar-chunk bila perlu.

    Rentang <= cap server cukup 1 call. Untuk rentang panjang, `to` digeser ke
    sebelum tanggal terawal chunk sebelumnya sampai mencapai `date_from`,
    chunk kosong, atau tak ada progres mundur (guard anti-loop, max_chunks).
    """
    frm = pd.Timestamp(date_from)
    to_ts = pd.Timestamp(date_to)
    to = date_to
    chunks: list[pd.DataFrame] = []
    prev_min: pd.Timestamp | None = None

    for _ in range(max_chunks):
        chunk = _fetch_ohlcv_chunk(client, code, date_from, to)
        if chunk.empty:
            break
        chunks.append(chunk)
        cmin = chunk["date"].min()
        if cmin <= frm:
            break  # sudah mencakup date_from
        if prev_min is not None and cmin >= prev_min:
            break  # tak ada progres mundur -> hindari loop tak berujung
        prev_min = cmin
        to = (cmin - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        # Loop habis tanpa break = max_chunks tercapai sebelum mencapai date_from.
        # Jangan truncate diam-diam; histori bisa tak lengkap di sisi awal.
        if chunks:
            print(f"[WARN] fetch_ohlcv {code}: rentang melebihi {max_chunks} chunk; "
                  f"histori terpotong di {chunks[-1]['date'].min().date()} "
                  f"(diminta dari {date_from}).", file=sys.stderr)

    if not chunks:
        return pd.DataFrame(columns=_COLS)

    df = pd.concat(chunks, ignore_index=True).drop_duplicates("date")
    df = df.sort_values("date").reset_index(drop=True)
    return df[(df["date"] >= frm) & (df["date"] <= to_ts)].reset_index(drop=True)
