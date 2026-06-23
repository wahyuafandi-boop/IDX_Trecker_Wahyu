#!/usr/bin/env python3
"""F8 — TUNE regime-aware (spec §5). GERBANG sebelum live.

Menjawab 3 pertanyaan spec §5.2 pada DATA NYATA (Invezgo, cache SQLite):
  1. rvol_spike optimal PER REGIME (fwd_close, horizon 20).
  2. Ablation RS: hit-rate MARKUP di BEARISH dengan vs tanpa gate RS.
  3. Strategi trade-levels vs NULL model (random-entry) setelah ongkos.

Pakai:
    python scripts/tune_f8.py --from 2024-06-01 --to 2026-06-01 \
        --codes AVIA,TPIA,BULL,HEAL,MAPA,BREN,PTRO
    python scripts/tune_f8.py --from 2026-04-01 --to 2026-06-01 --codes AVIA --probe

Semua bersifat read-only (tak mengubah settings.yaml). Angka final ditulis manual
ke config/settings.yaml setelah membaca tabel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.backtest import (
    backtest_levels,
    load_history,
    null_model,
    replay,
    signal_indices,
)
from markup_radar.config import load_settings, parse_codes
from markup_radar.ingest import InvezgoClient
from markup_radar.store import HistoryCache

MARKUP_STATES = ("MARKUP_START", "MARKUP_CONFIRMED")
RVOL_GRID = [1.5, 2.0, 2.5, 3.0, 3.5]


def _markup_hit(rows: pd.DataFrame, regime: str, target_up: float) -> tuple[int, float]:
    """(n, hit_rate) MARKUP di `regime` pakai fwd_close >= target_up (bukan fwd_max)."""
    mk = rows[(rows["regime"] == regime) & (rows["state"].isin(MARKUP_STATES))]
    mk = mk.dropna(subset=["fwd_close"])
    n = len(mk)
    if n == 0:
        return 0, float("nan")
    return n, round(float((mk["fwd_close"] >= target_up).mean()), 3)


def _replay_all(datasets, cfg, profiles, horizon) -> pd.DataFrame:
    frames = []
    for ds in datasets:
        res = replay(ds, cfg.thresholds, cfg.windows,
                     regime_profiles=profiles, top_n=cfg.broker_top_n, horizon=horizon)
        if not res.empty:
            frames.append(res)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def tune_rvol_per_regime(datasets, cfg, horizon, target_up) -> pd.DataFrame:
    """Grid-search rvol_spike tiap regime; sisanya profil dikunci di nilai config."""
    base = cfg.regime_profiles
    rows = []
    for regime in ("BULLISH", "BEARISH"):
        for v in RVOL_GRID:
            prof = {r: dict(base.get(r, {})) for r in ("BULLISH", "BEARISH")}
            prof[regime]["rvol_spike"] = v
            res = _replay_all(datasets, cfg, prof, horizon)
            n, hit = _markup_hit(res, regime, target_up) if not res.empty else (0, float("nan"))
            rows.append({"regime": regime, "rvol_spike": v, "n_markup": n, "hit_rate": hit})
    return pd.DataFrame(rows)


def ablation_rs(datasets, cfg, horizon, target_up) -> pd.DataFrame:
    """BEARISH: hit-rate MARKUP dengan gate RS (require=True) vs tanpa (False)."""
    base = cfg.regime_profiles
    rows = []
    for require in (True, False):
        prof = {r: dict(base.get(r, {})) for r in ("BULLISH", "BEARISH")}
        prof["BEARISH"]["require_relative_strength"] = require
        res = _replay_all(datasets, cfg, prof, horizon)
        n, hit = _markup_hit(res, "BEARISH", target_up) if not res.empty else (0, float("nan"))
        rows.append({"require_RS": require, "n_markup_bearish": n, "hit_rate": hit})
    return pd.DataFrame(rows)


def levels_vs_null(datasets, cfg, horizon) -> pd.DataFrame:
    """Per saham: strategi trade-levels (sinyal MARKUP_*) vs random-entry, net ongkos."""
    lv = cfg.levels
    levels_kwargs = dict(
        lookback=cfg.windows.get("donchian_lookback", 20),
        atr_period=lv.get("atr_period", 14),
        breakout_buffer=lv.get("breakout_buffer", 0.005),
        atr_mult_sl=2.0, rr_target=2.0,                       # representatif (sama utk strat & null)
        min_stop_pct=lv.get("min_stop_pct", 0.03),
        hold_slack=lv.get("hold_slack", 1.8),
    )
    rows = []
    for ds in datasets:
        res = replay(ds, cfg.thresholds, cfg.windows,
                     regime_profiles=cfg.regime_profiles, top_n=cfg.broker_top_n, horizon=horizon)
        if res.empty:
            continue
        idxs = signal_indices(ds, res, MARKUP_STATES)
        strat = backtest_levels(ds.ohlcv, idxs, horizon=horizon, levels_kwargs=levels_kwargs)
        nul = null_model(ds.ohlcv, max(strat["n_signals"], 1), horizon=horizon,
                         levels_kwargs=levels_kwargs, seed=7)
        rows.append({
            "code": ds.code,
            "n_sig": strat["n_signals"], "filled": strat["n_filled"],
            "strat_ret": strat["avg_ret_net"], "strat_win": strat["win_rate"],
            "null_ret": nul["avg_ret_net"], "null_win": nul["win_rate"],
            "edge": (round(strat["avg_ret_net"] - nul["avg_ret_net"], 4)
                     if pd.notna(strat["avg_ret_net"]) and pd.notna(nul["avg_ret_net"]) else float("nan")),
        })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Markup Radar — F8 tune (regime-aware)")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--codes", nargs="+", metavar="CODE", help="universe tuning (mis. AVIA,TPIA,...)")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--target-up", type=float, default=0.05)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--probe", action="store_true", help="diagnostik 1 saham (validasi pipeline + kuota)")
    args = ap.parse_args()

    cfg = load_settings()
    codes = parse_codes(args.codes) if args.codes else cfg.watchlist
    if args.probe:
        codes = codes[:1]

    client = InvezgoClient(cfg.invezgo_api_key, cfg.invezgo_base_url,
                           rate_limit_per_min=cfg.rate_limit_per_min)
    cache = None if args.no_cache else HistoryCache(cfg.db_path)

    datasets = []
    for code in codes:
        print(f"[load] {code} {args.date_from}..{args.date_to}{' (cache)' if cache else ''}",
              file=sys.stderr)
        try:
            ds = load_history(client, code, args.date_from, args.date_to, cache)
            if ds.ohlcv.empty:
                print(f"[WARN] {code}: OHLCV kosong, skip.", file=sys.stderr)
                continue
            datasets.append(ds)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
    if cache:
        cache.close()

    if not datasets:
        print("Tidak ada data (cek API key / ketersediaan).")
        return 1

    if args.probe:
        ds = datasets[0]
        res = replay(ds, cfg.thresholds, cfg.windows,
                     regime_profiles=cfg.regime_profiles, top_n=cfg.broker_top_n, horizon=args.horizon)
        print(f"\n=== PROBE {ds.code} ===")
        print(f"OHLCV bars      : {len(ds.ohlcv)}")
        print(f"done rows       : {len(ds.done)} (butuh utk MARKUP; 0 = degrade)")
        print(f"replay rows     : {len(res)}")
        if not res.empty:
            print(f"regime split    : {res['regime'].value_counts().to_dict()}")
            print(f"state split     : {res['state'].value_counts().to_dict()}")
        return 0

    print(f"\nUniverse: {[d.code for d in datasets]} | horizon={args.horizon}d "
          f"| target_up={args.target_up:.0%}\n")

    print("=== 1) rvol_spike per REGIME (fwd_close) ===")
    print(tune_rvol_per_regime(datasets, cfg, args.horizon, args.target_up).to_string(index=False))

    print("\n=== 2) Ablation RS gate (BEARISH) ===")
    print(ablation_rs(datasets, cfg, args.horizon, args.target_up).to_string(index=False))

    print("\n=== 3) Strategi trade-levels vs NULL model (net ongkos) ===")
    lvn = levels_vs_null(datasets, cfg, args.horizon)
    print(lvn.to_string(index=False))
    if not lvn.empty and lvn["edge"].notna().any():
        print(f"\nAGREGAT edge (strat-null): mean={lvn['edge'].mean():.4f} "
              f"| stok edge>0: {int((lvn['edge'] > 0).sum())}/{lvn['edge'].notna().sum()}")
    print("\n>> Tulis angka final ke config/settings.yaml setelah baca tabel di atas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
