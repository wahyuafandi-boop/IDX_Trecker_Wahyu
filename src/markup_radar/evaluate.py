"""Evaluasi forward sinyal PRODUKSI (log SQLite/Sheets) vs harga aktual.

Beda dengan backtest (replay klasifikasi atas data historis): ini menilai
sinyal yang benar-benar terbit saat run harian — termasuk yang terkirim ke
Telegram (`alert_sent`) — jawaban untuk kebutuhan #1 pasca-F8: data historis
tipis, edge divalidasi lewat AKUMULASI SINYAL FORWARD.

Dua sudut evaluasi per sinyal:
  1. `forward_metrics`  — return dari close tanggal sinyal ke +5/+10/+20 bar
     (semua state, termasuk NEUTRAL sebagai baseline pembanding).
  2. `levels_outcome`   — khusus MARKUP_* yang punya levels terpublish:
     entry breakout terisi atau tidak, lalu SL/TP/timeout (aturan sama dengan
     backtest: fill bila high >= entry, SL-first konservatif).
"""

from __future__ import annotations

import math
from statistics import median
from types import SimpleNamespace

import pandas as pd

from markup_radar.backtest.metrics import _fill_index, simulate_exit

HORIZONS = (5, 10, 20)


def forward_metrics(
    ohlcv: pd.DataFrame, date: str, horizons: tuple[int, ...] = HORIZONS
) -> dict | None:
    """Return forward dari close pada `date`. None bila tanggal tak ada di frame.

    fwd_close_N = NaN bila belum ada N bar sesudah sinyal (sinyal masih muda —
    kolom terisi sendiri saat evaluasi diulang minggu berikutnya). fwd_max /
    fwd_min (MFE/MAE) dihitung atas bar yang SUDAH tersedia, maks horizon
    terpanjang. `bars_fwd` = jumlah bar tersedia setelah sinyal.
    """
    if ohlcv is None or ohlcv.empty:
        return None
    ohlcv = ohlcv.reset_index(drop=True)
    hits = ohlcv.index[ohlcv["date"].astype(str) == str(date)]
    if len(hits) == 0:
        return None
    i = int(hits[0])
    base = float(ohlcv["close"].iloc[i])
    if base <= 0:
        return None

    out: dict = {"bars_fwd": len(ohlcv) - 1 - i}
    window = ohlcv.iloc[i + 1 : i + 1 + max(horizons)]
    out["fwd_max"] = float(window["high"].max() / base - 1) if len(window) else math.nan
    out["fwd_min"] = float(window["low"].min() / base - 1) if len(window) else math.nan
    for h in horizons:
        out[f"fwd_close_{h}"] = (
            float(ohlcv["close"].iloc[i + h] / base - 1)
            if i + h < len(ohlcv)
            else math.nan
        )
    return out


def levels_outcome(
    ohlcv: pd.DataFrame,
    date: str,
    levels: dict,
    *,
    trigger_window: int = 5,
    horizon: int = 20,
) -> dict | None:
    """Simulasi hasil trade dari levels yang dipublish di alert.

    Aturan identik backtest (§5.3): entry terisi bila ada bar dalam
    `trigger_window` hari dengan high >= entry (breakout), lalu walk-forward
    SL-first sampai `horizon`. Return None bila tanggal sinyal tak ada di
    frame; {"filled": False} bila breakout tak pernah terisi (no-trade).
    """
    if ohlcv is None or ohlcv.empty or not levels:
        return None
    ohlcv = ohlcv.reset_index(drop=True)
    hits = ohlcv.index[ohlcv["date"].astype(str) == str(date)]
    if len(hits) == 0:
        return None
    i = int(hits[0])

    entry = float(levels.get("entry", 0) or 0)
    sl = float(levels.get("stop_loss", 0) or 0)
    tp = float(levels.get("take_profit", 0) or 0)
    if entry <= 0 or sl <= 0 or tp <= 0:
        return None

    fill_idx = _fill_index(ohlcv, i, entry, trigger_window)
    if fill_idx is None:
        return {"filled": False, "exit": "NO_FILL", "bars": 0, "ret": math.nan}
    lv = SimpleNamespace(entry=entry, stop_loss=sl, take_profit=tp)
    res = simulate_exit(ohlcv, fill_idx, lv, horizon=horizon)
    return {"filled": True, **res}


def summarize_by_state(
    rows: list[dict], horizons: tuple[int, ...] = HORIZONS
) -> list[dict]:
    """Agregasi per state: n sinyal, win-rate & median return per horizon.

    NEUTRAL ikut dihitung sebagai baseline: edge nyata = MARKUP_* mengalahkan
    baseline, bukan sekadar positif. Sinyal muda (fwd NaN) di-skip per horizon,
    jadi n_5 >= n_10 >= n_20.
    """
    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault(r.get("state", "?"), []).append(r)

    out = []
    for state in sorted(by_state):
        grp = by_state[state]
        summary: dict = {"state": state, "n": len(grp)}
        for h in horizons:
            vals = [
                r[f"fwd_close_{h}"]
                for r in grp
                if not math.isnan(r.get(f"fwd_close_{h}", math.nan))
            ]
            summary[f"n_{h}"] = len(vals)
            summary[f"win_{h}"] = (
                sum(1 for v in vals if v > 0) / len(vals) if vals else math.nan
            )
            summary[f"med_{h}"] = median(vals) if vals else math.nan
        out.append(summary)
    return out
