# Markup Radar — IDX Swing Signal Confirmation Engine

Engine EOD (end-of-day) untuk mengonfirmasi apakah sebuah saham IDX **masih
akumulasi** atau **mulai transisi ke markup**, berbasis **Wyckoff Smart Money +
Bandarmologi**. Data dari **[Invezgo](https://invezgo.com)** (REST API).

> Spec lengkap & metodologi: [`markup-radar-spec-1.md`](markup-radar-spec-1.md).
> Status: signal engine, classifier, alerting & **backtesting (Phase 5)** sudah
> jalan & teruji; beberapa path endpoint Invezgo masih perlu diverifikasi
> (Phase 0) sebelum data live.

## Kenapa Invezgo

Invezgo punya REST API + SDK resmi untuk data IDX dan — yang paling penting —
endpoint `momentum-chart` mengekspos **buy/sell done** (≈ done-at-offer vs
done-at-bid), bahan mentah untuk sinyal tersulit **S1 Done Ratio** & **S2
Absorption**. Pemetaan endpoint → sinyal:

| Sinyal | Endpoint Invezgo |
|---|---|
| S1 Done Ratio, S2 Absorption | `/analysis/momentum-chart/{code}` |
| S3 Net Flow, S4 Concentration | `/analysis/summary/stock/{code}` |
| S5 Closing Queue | `/analysis/order-book/{code}` |
| S6 RVOL, S7 Close-in-range | stock chart OHLCV *(path TODO verify)* |
| S8 Foreign Flow | `/analysis/top/foreign` |
| S9 IHSG Filter | IHSG via stock chart |

## Struktur

```
config/          settings.yaml (threshold, watchlist) + .env.example
src/markup_radar/
  ingest/        InvezgoClient + normalizer per sumber data
  signals/       S1..S9 (pure pandas, teruji)
  scoring/       classifier (rule §4) + confidence score
  store/         SQLite — hasil harian (Store) + cache historis (HistoryCache)
  alert/         Telegram
  narrative/     Claude API (opsional)
  backtest/      replay histori + akurasi per state + tuning (Phase 5)
scripts/
  verify_data.py Phase 0 — cek shape data Invezgo (gating question)
  run_daily.py   entrypoint EOD
  backtest.py    Phase 5 — backtest & threshold tuning
tests/           PyTest (signals, classifier, ingest, backtest)
```

## Setup

```bash
pip install -e ".[dev]"          # atau: pip install -r requirements.txt
cp config/.env.example .env      # isi INVEZGO_API_KEY, TELEGRAM_*, dst
```

API key Invezgo: daftar/login di https://invezgo.com → ambil di settings.

## Pakai

```bash
# Phase 0 — verifikasi data dulu (jawab gating question §7)
python scripts/verify_data.py --code BBCA --date 2026-06-16

# Scan harian
python scripts/run_daily.py                 # hari ini
python scripts/run_daily.py --date 2026-06-16 --dry-run

# Backtest & tuning (Phase 5)
python scripts/backtest.py --from 2024-01-01 --to 2026-06-01
python scripts/backtest.py --code BBRI --from 2024-01-01 --to 2026-06-01 --tune

# Test
pytest -q
```

### Backtest output

`backtest.py` mereplay classifier hari-demi-hari lalu mengukur **forward
return** N hari ke depan dan melaporkan **hit-rate per state**:

| state | n | hit_rate | avg_fwd_max | avg_fwd_close |
|---|---|---|---|---|
| MARKUP_START | … | berapa % diikuti markup ≥ target_up | … | … |
| DISTRIBUTION_WARNING | … | berapa % diikuti penurunan | … | … |

`--tune` melakukan grid-search `done_ratio_markup` untuk cari keseimbangan
jumlah sinyal vs hit-rate → dipakai set threshold final di `settings.yaml`.

**Cache historis**: `load_history` membaca dari SQLite (`HistoryCache`) lebih
dulu dan hanya menarik tanggal yang belum ada dari API — done detail
(per-tanggal, paling mahal) cukup ditarik sekali. Run backtest kedua = 0 API
call. Pakai `--no-cache` untuk paksa tarik ulang.

## Otomatisasi (GitHub Actions)

Workflow [`.github/workflows/daily.yml`](.github/workflows/daily.yml) menjalankan
scan tiap hari kerja **10:00 UTC (17:00 WIB)** — setelah market IDX tutup — dan
bisa juga di-trigger manual dari tab **Actions** (`workflow_dispatch`, dengan
opsi `date` & `dry_run`).

Set **Repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Wajib | Keterangan |
|---|---|---|
| `INVEZGO_API_KEY` | ✅ | API key Invezgo |
| `TELEGRAM_BOT_TOKEN` | ✅ | bot token (untuk alert) |
| `TELEGRAM_CHAT_ID` | ✅ | tujuan alert |
| `INVEZGO_BASE_URL` | — | default `https://api.invezgo.com` |
| `ANTHROPIC_API_KEY` | — | hanya bila narrative diaktifkan |

Hasil SQLite di-upload sebagai artifact (`markup-radar-db`, retensi 30 hari)
karena storage runner bersifat ephemeral.

## Output state

`MARKUP_START` · `ACCUMULATION_ONGOING` · `DISTRIBUTION_WARNING` · `NEUTRAL` ·
`INSUFFICIENT_DATA` — masing-masing dengan confidence 0–100 dan breakdown
sinyal, disimpan ke SQLite dan dikirim ke Telegram (state actionable saja).

## Catatan & TODO

- **Phase 0 belum tuntas**: jalankan `verify_data.py` untuk konfirmasi shape
  response & path yang ditandai `# TODO(verify)` di `ingest/client.py`
  (OHLCV, stock/index list, kode IHSG).
- **Threshold default** di `config/settings.yaml` — wajib di-tune via
  backtesting (spec Phase 5).
- **Kuota & rate limit**: pemakaian ~**5 API call/saham/hari** (IHSG & foreign
  ditarik 1x per run). Atur `api.rate_limit_per_min` di settings sesuai tier
  Invezgo (Advance 250/min) — client throttle otomatis agar tidak kena 429.
- **Signal-only**: tidak ada eksekusi order. Risk management tetap manual
  (spec §9).
