#!/usr/bin/env python3
"""Demo offline — jalankan pipeline penuh dengan data sintetis (tanpa API key).

Berguna untuk melihat output Markup Radar sebelum langganan Invezgo aktif:
  - klasifikasi "watchlist hari ini" (4 skenario state)
  - preview pesan alert Telegram
  - laporan backtest pada skenario markup

    python scripts/demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from markup_radar.alert import format_alert
from markup_radar.backtest import replay, summarize
from markup_radar.config import load_settings
from markup_radar.demo import classify_snapshot, make_history_markup, make_snapshot

SCENARIOS = [
    ("markup", "MRKP"),
    ("accumulation", "ACUM"),
    ("distribution", "DSTR"),
    ("neutral", "NEUT"),
]


def main() -> int:
    cfg = load_settings()

    print("=== Demo: scan watchlist (data sintetis) ===")
    print(f"{'code':6s} {'state':22s} conf")
    actionable = []
    for kind, code in SCENARIOS:
        data = make_snapshot(kind, code)
        state, conf, signals = classify_snapshot(data, cfg)
        print(f"{code:6s} {state:22s} {conf}")
        if state in cfg.alert_states:
            actionable.append({"code": code, "state": state, "confidence": conf, "signals": signals})

    print("\n=== Preview alert Telegram ===")
    print(format_alert("2026-06-17 (DEMO)", actionable))

    print("\n=== Demo: backtest skenario markup ===")
    ds = make_history_markup()
    res = replay(ds, cfg.thresholds, cfg.windows, top_n=cfg.broker_top_n, horizon=5)
    report = summarize(res, target_up=0.05)
    print(report.to_string(index=False))

    print("\n(catatan: ini data sintetis. Untuk data riil, isi INVEZGO_API_KEY "
          "lalu jalankan scripts/verify_data.py & run_daily.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
