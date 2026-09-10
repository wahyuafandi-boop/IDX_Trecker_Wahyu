#!/usr/bin/env bash
# Wrapper konfirmasi EOD untuk cron VPS. Jadwal: 12:05 UTC = 19:05 WIB (Sen-Jum).
# Baca watchlist_today.txt (hasil screening Stockbit). Log ke logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
.venv/bin/python scripts/run_daily.py --codes-file watchlist_today.txt \
    >> "logs/eod_$(date +%Y%m%d).log" 2>&1
