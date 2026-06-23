"""Paging histori chart Invezgo (helper bersama).

Endpoint chart Invezgo (stock/index/inventory) membatasi ~6 bln per request DAN
hanya menyimpan ~2 thn histori; rentang lebih lebar / lebih lama -> HTTP 422
(diverifikasi 2026-06-23 saat F8). `fetch_windowed` menarik per-jendela terbatas
(<= window_days, di bawah cap), menggeser `to` mundur, lalu men-stitch — dan
degrade rapi (berhenti, pakai yang sudah ada) saat chunk lama ditolak 422.
"""

from __future__ import annotations

import sys
from typing import Callable

import pandas as pd

from markup_radar.ingest.client import InvezgoError


def fetch_windowed(
    fetch_chunk: Callable[[str, str], pd.DataFrame],
    date_from: str,
    date_to: str,
    *,
    label: str = "fetch",
    columns: list[str] | None = None,
    max_chunks: int = 24,
    window_days: int = 120,
) -> pd.DataFrame:
    """Stitch histori [date_from..date_to] dari `fetch_chunk(from, to)`.

    `fetch_chunk` mengembalikan DataFrame dengan kolom `date` (boleh kosong =
    saham tak diperdagangkan di window itu -> dilewati, tetap mundur). 422 di
    chunk lama (lewat horizon histori / belum listing) menghentikan loop dengan
    sisa data utuh, BUKAN membatalkan semuanya.
    """
    frm = pd.Timestamp(date_from)
    to_ts = pd.Timestamp(date_to)
    to = to_ts
    chunks: list[pd.DataFrame] = []

    for _ in range(max_chunks):
        chunk_from = to - pd.Timedelta(days=window_days)
        if chunk_from < frm:
            chunk_from = frm
        try:
            chunk = fetch_chunk(chunk_from.strftime("%Y-%m-%d"), to.strftime("%Y-%m-%d"))
        except InvezgoError as exc:
            if chunks:
                print(f"[info] {label}: histori berhenti di "
                      f"{min(c['date'].min() for c in chunks).date()} "
                      f"(chunk {chunk_from.date()}..{to.date()} ditolak: {exc}).",
                      file=sys.stderr)
            else:
                print(f"[WARN] {label}: tak ada data ({exc}).", file=sys.stderr)
            break
        if not chunk.empty:
            chunks.append(chunk)
        if chunk_from <= frm:
            break  # sudah mencakup date_from
        to = chunk_from - pd.Timedelta(days=1)
    else:
        if chunks:
            print(f"[WARN] {label}: rentang melebihi {max_chunks} chunk "
                  f"(window {window_days}d); histori terpotong di "
                  f"{min(c['date'].min() for c in chunks).date()} (diminta dari {date_from}).",
                  file=sys.stderr)

    if not chunks:
        return pd.DataFrame(columns=columns) if columns else pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True).drop_duplicates("date")
    df = df.sort_values("date").reset_index(drop=True)
    return df[(df["date"] >= frm) & (df["date"] <= to_ts)].reset_index(drop=True)
