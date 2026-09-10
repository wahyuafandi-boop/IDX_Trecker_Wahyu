#!/usr/bin/env bash
# Evaluasi mingguan forward-return sinyal untuk cron VPS.
# Jadwal saran: Sabtu 02:00 UTC = 09:00 WIB (market tutup, data EOD Jumat sudah masuk):
#   0 2 * * 6  /path/ke/repo/run_eval.sh
# Hasil: console log + mirror worksheet 'evaluation' & 'eval_summary' di Google Sheet.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
.venv/bin/python scripts/evaluate_signals.py --sheets \
    >> "logs/eval_$(date +%Y%m%d).log" 2>&1
