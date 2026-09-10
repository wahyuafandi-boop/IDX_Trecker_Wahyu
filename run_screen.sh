#!/usr/bin/env bash
# Refresh watchlist_today.txt dari Invezgo screener — dijalankan SEBELUM run_eod.sh.
# Jadwal cron VPS: 12:00 UTC = 19:00 WIB (Sen-Jum), 5 menit sebelum run_eod (12:05).
# SAFETY: bila screener gagal / hasil kosong, watchlist lama DIPERTAHANKAN
# (screen_universe.py exit != 0, file tak ditimpa) sehingga run_eod tetap jalan
# pakai watchlist terakhir yang baik. Log ke logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
.venv/bin/python scripts/screen_universe.py \
    >> "logs/screen_$(date +%Y%m%d).log" 2>&1
