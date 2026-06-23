"""Laporan akurasi per state + grid-search threshold (spec Phase 5 DoD).

Plus simulasi exit level (TP/SL) + NULL model random-entry (spec §5.3) untuk
memvalidasi strategi trade-levels: edge level cuma nyata kalau MENGALAHKAN
entry-acak setelah ongkos.
"""

from __future__ import annotations

import random

import pandas as pd

from markup_radar.backtest.engine import replay
from markup_radar.signals.levels import compute_trade_levels

# Definisi "benar" per state. Pakai fwd_CLOSE (return ke-hold) bukan fwd_max
# (spike intraday yang menyesatkan — lihat catatan 2026-06-21: tuning ke fwd_max
# bikin edge semu; fwd_close realistis menunjukkan edge baru muncul di 10-20d):
#   MARKUP_CONFIRMED     -> markup ke-hold:  fwd_close >= target_up (sama dgn START)
#   MARKUP_START         -> markup ke-hold:  fwd_close >= target_up
#   DISTRIBUTION_WARNING -> harga jatuh:      fwd_close <= -target_down
#   ACCUMULATION_ONGOING -> tidak breakdown:  fwd_min > -target_down (survival)
# Catatan: MARKUP_CONFIRMED butuh S5 (order book) yang tak historis, jadi tak
# muncul di replay sekarang; entri ini menjaga semantik bila data live di-backtest.
_HIT = {
    "MARKUP_CONFIRMED": lambda r, up, dn: r["fwd_close"] >= up,
    "MARKUP_START": lambda r, up, dn: r["fwd_close"] >= up,
    "DISTRIBUTION_WARNING": lambda r, up, dn: r["fwd_close"] <= -dn,
    "ACCUMULATION_ONGOING": lambda r, up, dn: r["fwd_min"] > -dn,
}


def summarize(
    results: pd.DataFrame,
    *,
    target_up: float = 0.05,
    target_down: float = 0.05,
) -> pd.DataFrame:
    """Ringkas hit-rate & forward return rata-rata per state.

    Hanya pakai baris yang punya forward return (buang ekor tanpa horizon).
    """
    if results.empty:
        return pd.DataFrame()
    df = results.dropna(subset=["fwd_max", "fwd_min", "fwd_close"])

    out = []
    for state, grp in df.groupby("state"):
        n = len(grp)
        hit_fn = _HIT.get(state)
        if hit_fn is not None:
            hits = grp.apply(lambda r: hit_fn(r, target_up, target_down), axis=1).sum()
            hit_rate = hits / n if n else 0.0
        else:
            hit_rate = float("nan")  # NEUTRAL / INSUFFICIENT_DATA: tak relevan
        out.append(
            {
                "state": state,
                "n": n,
                "hit_rate": round(hit_rate, 3),
                "avg_fwd_max": round(grp["fwd_max"].mean(), 4),
                "avg_fwd_min": round(grp["fwd_min"].mean(), 4),
                "avg_fwd_close": round(grp["fwd_close"].mean(), 4),
            }
        )
    return pd.DataFrame(out).sort_values("state").reset_index(drop=True)


def tune(
    dataset,
    base_thresholds: dict,
    windows: dict,
    *,
    key: str = "done_ratio_markup",
    values: list[float] | None = None,
    horizon: int = 5,
    target_up: float = 0.05,
    top_n: int = 5,
) -> pd.DataFrame:
    """Grid-search satu threshold; ukur jumlah sinyal & hit-rate MARKUP_START.

    Bantu menjawab "threshold final" pada DoD Phase 5. Pilih nilai dengan
    keseimbangan terbaik antara jumlah sinyal (n) dan hit_rate.
    """
    values = values or [0.55, 0.58, 0.60, 0.62, 0.65, 0.70]
    rows = []
    for v in values:
        th = {**base_thresholds, key: v}
        res = replay(dataset, th, windows, top_n=top_n, horizon=horizon)
        rep = summarize(res, target_up=target_up)
        ms = rep[rep["state"] == "MARKUP_START"] if not rep.empty else rep
        if ms.empty:
            rows.append({key: v, "n_signals": 0, "hit_rate": float("nan"), "avg_fwd_max": float("nan")})
        else:
            row = ms.iloc[0]
            rows.append(
                {key: v, "n_signals": int(row["n"]), "hit_rate": row["hit_rate"], "avg_fwd_max": row["avg_fwd_max"]}
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulasi exit level (TP/SL) + NULL model random-entry — spec §5.3
# ---------------------------------------------------------------------------

# Ongkos round-trip default (IDX, konservatif). fee ~0.15% beli / lebih besar saat
# jual (broker+pajak); slippage besar di gocap yang sering gap. 2x = entry+exit.
_DEFAULT_FEE = 0.0015
_DEFAULT_SLIPPAGE = 0.002


def simulate_exit(ohlcv: pd.DataFrame, entry_idx: int, levels, horizon: int = 20) -> dict:
    """Walk-forward dari `entry_idx`+1 sampai SL/TP kena atau horizon habis.

    KONSERVATIF (spec §5.3 & §10): bila SATU bar harian menyentuh SL **dan** TP
    sekaligus, anggap SL kena dulu — tanpa data intraday jangan optimis. Return
    `ret` = (harga keluar / entry) − 1 (sebelum ongkos).
    """
    e, sl, tp = levels.entry, levels.stop_loss, levels.take_profit
    end = min(entry_idx + 1 + horizon, len(ohlcv))
    for j in range(entry_idx + 1, end):
        bar = ohlcv.iloc[j]
        hit_sl = bar["low"] <= sl
        hit_tp = bar["high"] >= tp
        if hit_sl:                                  # SL diprioritaskan (konservatif)
            return {"exit": "SL", "bars": j - entry_idx, "ret": sl / e - 1}
        if hit_tp:
            return {"exit": "TP", "bars": j - entry_idx, "ret": tp / e - 1}
    last = ohlcv.iloc[end - 1]
    return {"exit": "TIMEOUT", "bars": end - 1 - entry_idx, "ret": last["close"] / e - 1}


def _levels_at(ohlcv: pd.DataFrame, i: int, levels_kwargs: dict):
    """compute_trade_levels memakai data SAMPAI bar i (inklusif). None bila kurang."""
    return compute_trade_levels(ohlcv.iloc[: i + 1], **levels_kwargs)


def _fill_index(ohlcv: pd.DataFrame, signal_idx: int, entry: float, horizon: int):
    """Bar pertama SETELAH sinyal (dalam horizon) yang high >= entry = breakout
    terisi. None bila tak ada → trade TIDAK diambil ('no fill', spec §5.3)."""
    end = min(signal_idx + 1 + horizon, len(ohlcv))
    for j in range(signal_idx + 1, end):
        if ohlcv["high"].iloc[j] >= entry:
            return j
    return None


def _round_trip_cost(fee: float, slippage: float) -> float:
    return 2 * fee + 2 * slippage


def _summarize_exits(exits: list[dict], *, n_signals: int) -> dict:
    n = len(exits)
    if n == 0:
        return {"n_signals": n_signals, "n_filled": 0, "win_rate": float("nan"),
                "avg_ret_net": float("nan"), "TP": 0, "SL": 0, "TIMEOUT": 0, "exits": []}
    wins = sum(1 for e in exits if e["ret_net"] > 0)
    return {
        "n_signals": n_signals,
        "n_filled": n,
        "win_rate": round(wins / n, 3),
        "avg_ret_net": round(sum(e["ret_net"] for e in exits) / n, 4),
        "TP": sum(1 for e in exits if e["exit"] == "TP"),
        "SL": sum(1 for e in exits if e["exit"] == "SL"),
        "TIMEOUT": sum(1 for e in exits if e["exit"] == "TIMEOUT"),
        "exits": exits,
    }


def backtest_levels(
    ohlcv: pd.DataFrame,
    entry_indices,
    *,
    horizon: int = 20,
    levels_kwargs: dict | None = None,
    fee: float = _DEFAULT_FEE,
    slippage: float = _DEFAULT_SLIPPAGE,
) -> dict:
    """Untuk tiap bar-sinyal: hitung levels, cek FILL realistis (high>=entry sesudah
    sinyal), simulate_exit dari bar fill, kurangi ongkos round-trip. Sinyal tanpa
    levels / tanpa fill DILEWATI (bukan dihitung). Return ringkasan (lihat
    `_summarize_exits`). Bandingkan `avg_ret_net`-nya dengan `null_model`."""
    levels_kwargs = levels_kwargs or {}
    cost = _round_trip_cost(fee, slippage)
    entry_indices = list(entry_indices)
    exits: list[dict] = []
    for sidx in entry_indices:
        lv = _levels_at(ohlcv, sidx, levels_kwargs)
        if lv is None:
            continue
        fidx = _fill_index(ohlcv, sidx, lv.entry, horizon)
        if fidx is None:
            continue                                # no fill: breakout tak terjadi
        ex = simulate_exit(ohlcv, fidx, lv, horizon)
        ex["ret_net"] = ex["ret"] - cost
        exits.append(ex)
    return _summarize_exits(exits, n_signals=len(entry_indices))


def null_model(
    ohlcv: pd.DataFrame,
    n: int,
    *,
    horizon: int = 20,
    levels_kwargs: dict | None = None,
    fee: float = _DEFAULT_FEE,
    slippage: float = _DEFAULT_SLIPPAGE,
    warmup: int = 20,
    seed: int = 0,
) -> dict:
    """NULL MODEL (spec §5.3, non-negotiable): jalankan proses identik
    `backtest_levels` pada `n` entry-bar ACAK (deterministik via `seed`). Strategi
    level hanya punya edge bila `backtest_levels` mengalahkan baseline ini setelah
    ongkos. `n` biasanya = jumlah sinyal nyata (apple-to-apple)."""
    rng = random.Random(seed)
    lo, hi = warmup, len(ohlcv) - 2          # sisakan >=1 bar untuk fill/exit
    idxs = [rng.randint(lo, hi) for _ in range(n)] if hi >= lo and n > 0 else []
    return backtest_levels(ohlcv, idxs, horizon=horizon, levels_kwargs=levels_kwargs,
                           fee=fee, slippage=slippage)


def signal_indices(dataset, results: pd.DataFrame,
                   states=("MARKUP_START", "MARKUP_CONFIRMED")) -> list[int]:
    """Posisi bar (index ohlcv) untuk baris `results` yang state-nya actionable —
    jembatan dari output `replay` (per tanggal) ke `backtest_levels` (per index)."""
    if results.empty:
        return []
    date_to_idx = {d: i for i, d in enumerate(dataset.ohlcv["date"])}
    sig = results[results["state"].isin(list(states))]
    return [date_to_idx[d] for d in sig["date"] if d in date_to_idx]
