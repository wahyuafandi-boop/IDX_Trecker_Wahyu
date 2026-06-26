#!/usr/bin/env bash
# Wrapper live bid/offer untuk cron VPS. Jadwal: 02:00 UTC = 09:00 WIB (Sen-Jum).
# Baca live_today.txt (subset setup MARKUP terbaik hasil scan EOD tadi malam, top-N
# by confidence — BUKAN 50 kode watchlist, biar hemat kuota: ~305 vs ~3050 call/hari).
# Kalau file kosong/absen (nol setup malam ini) live_watch keluar cepat tanpa call.
# Polling 2 jam, ping Telegram saat sinyal, auto-stop kalau jaringan down. Log ke logs/.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
.venv/bin/python -u scripts/live_watch.py --codes-file live_today.txt \
    --telegram --duration 120 >> "logs/live_$(date +%Y%m%d_%H%M).log" 2>&1
