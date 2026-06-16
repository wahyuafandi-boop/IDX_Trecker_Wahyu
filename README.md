# Markup Radar — IDX Swing Signal Confirmation Engine

Engine EOD (end-of-day) untuk mengonfirmasi apakah sebuah saham IDX **masih
akumulasi** atau **mulai transisi ke markup**, berbasis **Wyckoff Smart Money +
Bandarmologi**. Data dari **[Invezgo](https://invezgo.com)** (REST API).

> Spec lengkap & metodologi: [`markup-radar-spec-1.md`](markup-radar-spec-1.md).
> Status: **scaffold** — signal engine + classifier sudah jalan & teruji;
> beberapa path endpoint Invezgo masih perlu diverifikasi (Phase 0).

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
  store/         SQLite (audit & backtesting)
  alert/         Telegram
  narrative/     Claude API (opsional)
scripts/
  verify_data.py Phase 0 — cek shape data Invezgo (gating question)
  run_daily.py   entrypoint EOD
tests/           PyTest (signals + classifier)
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

# Test
pytest -q
```

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
- **Signal-only**: tidak ada eksekusi order. Risk management tetap manual
  (spec §9).
