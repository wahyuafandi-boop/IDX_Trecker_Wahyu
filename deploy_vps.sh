#!/usr/bin/env bash
# Deploy Markup Radar ke VPS Contabo lewat scp (repo VPS bukan working tree git —
# lihat catatan deploy 2026-07-24: "scp bukan git-pull").
#
#   ./deploy_vps.sh            # kirim kode + config, lalu verifikasi
#   ./deploy_vps.sh --dry-run  # tampilkan apa yang akan dikirim, tanpa mengirim
#
# TIDAK menyentuh: .env (kredensial VPS punya sendiri), data/ (DB produksi),
# logs/, watchlist_today.txt, live_today.txt, live_levels.json.
#
# CATATAN SSH: VPS ini pakai fail2ban. Hindari membuka banyak koneksi beruntun —
# skrip ini sengaja memakai SATU koneksi ControlMaster untuk semua langkah.
set -euo pipefail

HOST=root@144.91.71.3
PORT=2222
KEY=~/.ssh/idx_agent_vps
DEST=/root/IDX_Trecker_Wahyu
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="1"

CTL=$(mktemp -u /tmp/mr-ssh-XXXXXX)
SSH_OPTS=(-p "$PORT" -i "$KEY" -o ConnectTimeout=20
          -o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=120)
cleanup() { ssh "${SSH_OPTS[@]}" -O exit "$HOST" 2>/dev/null || true; }
trap cleanup EXIT

# File yang dikirim: kode + config saja.
FILES=(
  src
  scripts
  tests
  config/settings.yaml
  requirements.txt
  pyproject.toml
  run_eod.sh
  run_live.sh
  run_screen.sh
  run_eval.sh
)

echo "== target: $HOST:$DEST"
if [ -n "$DRY" ]; then
  echo "== DRY RUN, yang akan dikirim:"
  printf '   %s\n' "${FILES[@]}"
  exit 0
fi

echo "== cek koneksi"
ssh "${SSH_OPTS[@]}" "$HOST" "echo '   ok:' \$(hostname) \$(date -u +%FT%TZ)"

echo "== backup config lama di VPS"
ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd $DEST && cp config/settings.yaml config/settings.yaml.bak-\$(date +%Y%m%d-%H%M%S)"

echo "== kirim berkas"
for f in "${FILES[@]}"; do
  scp -q -P "$PORT" -i "$KEY" -o ControlPath="$CTL" -r "$f" "$HOST:$DEST/$(dirname "$f")/"
  echo "   -> $f"
done

echo "== bersihkan __pycache__ basi"
ssh "${SSH_OPTS[@]}" "$HOST" "cd $DEST && find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true"

echo "== jalankan test suite di VPS"
ssh "${SSH_OPTS[@]}" "$HOST" "cd $DEST && .venv/bin/python -m pytest -q 2>&1 | tail -5"

echo "== smoke test (dry-run, 3 kode, tanpa kirim Telegram)"
ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd $DEST && .venv/bin/python scripts/run_daily.py --dry-run --codes TLKM BBNI LSIP 2>&1 | tail -12"

echo "== cron terpasang"
ssh "${SSH_OPTS[@]}" "$HOST" "crontab -l | grep -E 'IDX_Trecker|run_(eod|screen|eval|live)' || echo '   (tak ada)'"

echo "== SELESAI"
