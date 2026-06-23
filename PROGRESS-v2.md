# Markup Radar v2 — Refactor Progress

> **Source of truth** untuk refactor regime-aware (spec: [`markup-radar-spec-2.md`](markup-radar-spec-2.md)).
> **BACA FILE INI DULU di setiap sesi baru** sebelum menyentuh kode — supaya tidak
> menimpa / mengulang pekerjaan fase sebelumnya.
> Setelah menyelesaikan satu fase: **centang tabel + tambah baris changelog**, lalu lapor ke user.

- **Branch kerja:** `markup-radar-engine`
- **HEAD:** `1e56340` (F7) — **F1–F7 COMMITTED** (F1 `0719766`..F7 `1e56340`). **F8 + 3 bugfix ingest
  UNCOMMITTED** di working tree (dataset.py, ohlc_client.py, ihsg_client.py, _history.py baru,
  test_ingest.py, settings.yaml, scripts/tune_f8.py + tracker). **Semua belum di-push.**
- **Package:** `markup_radar` (JANGAN rename — spec §10)
- **REFACTOR v2 (F1–F8) SELESAI.** Verdict F8: pertahankan prior (data tipis). Engine belum
  live-ready dgn keyakinan statistik — butuh akumulasi sinyal forward dulu (lihat changelog F8).

---

## Status fase (F1–F8)

| Fase | Deskripsi | Status | Test |
|------|-----------|--------|------|
| **F1** | Primitives: `atr`, `donchian` (price_volume), `relative_strength`, `market_regime`, `Regime` (market) | ✅ **DONE** 2026-06-23 | `test_signals.py` 39 ✓ |
| **F2** | Levels: `signals/levels.py` baru — `TradeLevels` + `compute_trade_levels` (R:R dihitung, floor stop, est_hold) | ✅ **DONE** 2026-06-23 | `test_levels.py` 9 ✓ |
| **F3** | Profiles & config: blok `regime_profiles` di `settings.yaml` + property `cfg.regime_profiles` + loader | ✅ **DONE** 2026-06-23 | `test_config.py` 15 ✓ |
| **F4** | Classifier RS gate (opt-in via profil) + `relative_strength` wiring di `compute_signals` + score §4.7 | ✅ **DONE** 2026-06-23 | `test_classifier.py` (+regresi) |
| **F5** | Integrasi `run_daily.py`: resolve regime → profil → classify → levels → record | ✅ **DONE** 2026-06-23 | `test_run_daily.py` 5 ✓ |
| **F6** | Alert v2: `format_alert` render level + regime tag (HANYA state MARKUP_*) | ✅ **DONE** 2026-06-23 | `test_alert.py` 9 ✓ |
| **F7** | Backtest regime-aware: `replay` regime-per-bar + `simulate_exit` (SL-first) + NULL model | ✅ **DONE** 2026-06-23 | `test_backtest.py` 18 ✓ |
| **F8** | **TUNE** (gate sebelum live): rvol per-regime, ablation RS, tentukan angka final di YAML | ✅ **DONE** 2026-06-23 — verdict: data tipis, PERTAHANKAN prior | `scripts/tune_f8.py` |

**Full suite saat ini:** `156 passed`.

---

## Changelog

- **2026-06-23 — F8 SELESAI (verdict: PERTAHANKAN prior).** Run penuh 7 saham (AVIA TPIA BULL
  HEAL MAPA BREN PTRO, 2024-06..2026-06) lewat `scripts/tune_f8.py`. Hasil: **sinyal MARKUP terlalu
  sedikit untuk tuning meyakinkan** — BULLISH n~5-28 across rvol grid (hit@fwd_close+5%/20d 17-29%,
  TIDAK naik dgn rvol = bukan edge, kemungkinan noise); BEARISH n~1-5 (hit 0%). Ablation RS
  inconclusive (n=1 vs 2). Levels>null directional positif (agg edge +12%, 2/3 stok) tapi didorong
  1 trade (BULL +31%). **KEPUTUSAN: tak ubah threshold** (overfit noise / longgarkan rvol bear =
  langgar spec §2/§10). settings.yaml comment di-update: prior SUDAH dicek empiris (bukan tebakan
  buta), belum "final" sampai cukup sinyal forward (mirror Sheets) utk tune dgn n memadai. Bottleneck
  sebenarnya: sinyal langka by-design + horizon data API ~20 bln + per-saham. Engine TAK live-ready
  dgn keyakinan statistik; pipeline & tooling siap.
- **2026-06-23 — F8 DIMULAI + 3 BUGFIX ingest (blocker backtest panjang).** Universe tuning:
  AVIA TPIA BULL HEAL MAPA BREN PTRO (medium-cap, lintas sektor), window 2024-06..2026-06
  (~20 bln; regime split AVIA ~BULLISH 209/BEARISH 201 → kedua regime kebagian). Bug ditemukan
  & diperbaiki saat F8 (endpoint chart Invezgo cap ~6 bln + horizon histori ~2 thn):
  (1) `fetch_ohlcv` kirim full-range → 422; fix: windowed chunk + degrade rapi via helper baru
  `ingest/_history.fetch_windowed`. (2) `fetch_ihsg` (index endpoint) bug sama → pakai helper sama.
  (3) `load_history` broker `from` di luar horizon → inventory balikin EMPTY (bukan 422) → broker
  net cuma ~recent → streak S3 mati → 0 MARKUP historis; fix: clamp broker `from` ke awal OHLCV.
  Setelah fix: AVIA broker 430 baris, streak>=1 di 80% bar, 3 sinyal MARKUP/20bln (selektif, wajar).
  Tooling: `scripts/tune_f8.py` (rvol per-regime, ablation RS, levels-vs-null). Tests ingest +1
  (regresi 422-boundary), suite 156.
- **2026-06-23 — F7 selesai.** Backtest regime-aware. `engine.replay`: param `regime_profiles`,
  resolve regime per-bar dari IHSG-sampai-tanggal (no lookahead) → `eff` → classify; kolom baru
  `regime`/`relative_strength`; default horizon 5→20. `metrics`: `simulate_exit` (SL-first
  konservatif), `backtest_levels` (FILL realistis high≥entry + ongkos round-trip), `null_model`
  (random-entry deterministik, apple-to-apple), `signal_indices` (jembatan replay→index).
  `test_backtest.py` +11 (regime column, BEARISH allows outperformer, SL-first/TP/timeout, fill,
  cost, null determinism). Backward-compat: `regime_profiles=None` → perilaku lama. Suite: 144 → 155.
  *Catatan:* harga ditemui di metrics di-bangun untuk DIPAKAI F8 (belum dijalankan pada data nyata).
- **2026-06-23 — F6 selesai.** `format_alert` v2: header dapat tag `· REGIME · RS ±x.x%`; baris
  `📍 Resis/Support/ATR` + `🎯 Entry/SL(−%)/TP(R:R)/~hold` HANYA saat `levels` ada (MARKUP_*);
  state lain (DISTRIBUTION/ACCUMULATION) tampil warning tanpa entry. Footer baru regime-aware.
  Semua field v2 dibaca via `.get()` → item lama tetap kompatibel. `test_alert.py` +5 (render level,
  R:R dari levels bukan label, non-markup tanpa entry, footer, backward-compat). Suite: 139 → 144.
  *Deviasi kecil:* spec contoh tampilkan "Close" di baris 📍 — close mentah tak ada di record/signals,
  jadi dirender Resis/Support/ATR saja (TODO bila mau Close: plumb close ke record di run_daily).
  *Catatan:* "resis to watch" utk ACCUMULATION belum (butuh donchian dihitung utk non-markup juga).
- **2026-06-23 — F5 selesai.** Integrasi `run_daily.py`: resolve `regime` SEKALI per run dari
  IHSG → `eff = {**thresholds, **profile}`; helper baru `evaluate(data, signals, cfg, eff)` (pure,
  testable offline) → classify pakai `eff` + levels HANYA untuk MARKUP_* (atr_mult_sl/rr_target dari
  profil, sisanya dari blok `levels`). Record dapat key `regime`/`relative_strength`/`levels`; log
  per-saham tampilkan RS%. `test_run_daily.py` 5 test (markup→levels, non-markup→None, BEARISH
  perketat SL, RS-gate blokir underperform). Suite: 134 → 139. *Catatan:* `format_alert` belum render
  key baru (itu F6); backtest replay belum regime-aware (F7).
- **2026-06-23 — F4 selesai.** Classifier RS gate OPT-IN di `classify()` (klausa
  `(not require_rs or outperforms)` → no-op saat profil default → backward-compatible).
  Wiring `relative_strength` (S10) di `compute_signals` (nilai mentah, gate di classifier).
  Beresin utang §4.7: `score.py` tambah norm `relative_strength` + rebalance bobot (queue 10→5,
  ihsg 10→5, +RS 10, total tetap 100); `settings.yaml score_weights` disamakan. 3 test RS-gate baru
  (BEARISH underperform→NEUTRAL, outperform→MARKUP_START, regresi no-op) + update
  `test_confidence_high` (perfect score kini butuh RS). Demo markup conf 90→80 (tetap ≥70). Suite: 131 → 134.
- **2026-06-23 — F3 selesai.** Tambah di `settings.yaml`: `windows.rs_window`/`donchian_lookback`,
  blok `regime_profiles` (BULLISH/BEARISH), blok `levels`. Tambah property `cfg.regime_profiles` &
  `cfg.levels` di `config.py`. 6 test di `test_config.py`. **DITUNDA (sengaja, bukan F3):** rebalance
  `score_weights` + `relative_strength` di `score.py` (spec §4.7) — coupled ke score.py, geser output
  confidence → kerjakan bareng confidence work, bukan plumbing config. Suite: 125 → 131 passed.
- **2026-06-23 — F2 selesai.** Buat `src/markup_radar/signals/levels.py` (`TradeLevels`,
  `compute_trade_levels`) + `tests/test_levels.py` (9 test). Self-check `rr_realized ≈ rr_target`
  (target 2.0 & 1.5), floor stop 3% aktif saat ATR mungil, `est_hold_days` 5–20, `None` saat data
  kurang/ATR=0. Classifier & wiring belum disentuh. Suite: 116 → 125 passed.
- **2026-06-23 — F1 selesai.** Tambah `atr`/`donchian` di `price_volume.py`; `Regime`/`market_regime`/
  `relative_strength` di `market.py`; 13 test di `test_signals.py`. Fail-safe regime = BEARISH saat
  data kosong. `compute_signals` belum di-wire (itu F4). Suite: 103 → 116 passed.

---

## Aturan kerja (phase-gate — disepakati dengan user)

1. **Satu fase per langkah.** Test fase harus **hijau** sebelum lanjut.
2. **Stop & lapor** ke user di akhir tiap fase; tunggu aba-aba ("lanjut") sebelum fase berikutnya.
3. **Backward-compatible:** signature `classify(signals, thresholds)` TIDAK berubah; gate RS opt-in
   via profil → default no-op → semua test lama tetap lulus.
4. **Jangan langgar spec §1** (yang sudah benar: inversi done, `fwd_close`, horizon 10–20d, rvol 2.0
   baseline, dst.) & **§10** (larangan: turunin rvol di BEARISH, pakai `fwd_max`, label R:R, rename
   package, skip null model, dll).
5. **Urutan fase = spec §8.** Jangan lompat (mis. jangan sentuh classifier sebelum F4).

## Catatan untuk sesi berikutnya

- Memory anchor: `markup-radar-v2-progress` (di MEMORY.md) menunjuk ke file ini.
- **F1–F8 SELESAI.** F1–F7 di-commit; **F8 + 3 bugfix ingest belum di-commit, semua belum di-push.**
- **Langkah lanjut (BUKAN refactor lagi — soal data/validasi):**
  1. **Commit + push** kerjaan F8 (ingest fixes + tune script + YAML) ke `markup-radar-engine`.
  2. **Kumpulkan sinyal forward** dulu sebelum nge-tune final: jalankan EOD harian (sudah mirror ke
     Sheets) sampai ada cukup MARKUP (n besar) → baru tune rvol/RS dgn keyakinan + base-rate NEUTRAL.
  3. Re-run `scripts/tune_f8.py` berkala (cache bikin murah) untuk update verdict saat n bertambah.
  4. Pertimbangkan perluas universe / turunkan horizon data-start bila mau n lebih cepat.
- **Cara jalankan tune ulang:** `python scripts/tune_f8.py --from 2024-08-01 --to <today> --codes <list>`
  (mulai 2024-08 = dalam horizon data API; lebih awal ditolak 422 → auto-degrade). `--probe` utk cek 1 saham.
- Spec lengkap per-file ada di `markup-radar-spec-2.md` §4 (modul), §6 (YAML), §7 (test DoD).
