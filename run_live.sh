#!/usr/bin/env bash
# Wrapper live bid/offer untuk cron VPS. Jadwal: 02:00 UTC = 09:00 WIB (Sen-Jum).
# Polling 2 jam, ping Telegram saat sinyal, auto-stop kalau jaringan down. Log ke logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
.venv/bin/python -u scripts/live_watch.py --codes-file watchlist_today.txt \
    --telegram --duration 120 >> "logs/live_$(date +%Y%m%d_%H%M).log" 2>&1
