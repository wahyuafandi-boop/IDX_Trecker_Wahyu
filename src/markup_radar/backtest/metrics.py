"""Laporan akurasi per state + grid-search threshold (spec Phase 5 DoD)."""

from __future__ import annotations

import pandas as pd

from markup_radar.backtest.engine import replay

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
