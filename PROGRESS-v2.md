# Markup Radar v2 — Refactor Progress

> **Source of truth** untuk refactor regime-aware (spec: [`markup-radar-spec-2.md`](markup-radar-spec-2.md)).
> **BACA FILE INI DULU di setiap sesi baru** sebelum menyentuh kode — supaya tidak
> menimpa / mengulang pekerjaan fase sebelumnya.
> Setelah menyelesaikan satu fase: **centang tabel + tambah baris changelog**, lalu lapor ke user.

- **Branch kerja:** `markup-radar-engine`
- **HEAD:** `a442b53` (F3) — F1–F3 sudah **COMMITTED** (`0719766` F1, `b5e35b9` F2, `a442b53` F3). Belum di-push.
- **Package:** `markup_radar` (JANGAN rename — spec §10)

---

## Status fase (F1–F8)

| Fase | Deskripsi | Status | Test |
|------|-----------|--------|------|
| **F1** | Primitives: `atr`, `donchian` (price_volume), `relative_strength`, `market_regime`, `Regime` (market) | ✅ **DONE** 2026-06-23 | `test_signals.py` 39 ✓ |
| **F2** | Levels: `signals/levels.py` baru — `TradeLevels` + `compute_trade_levels` (R:R dihitung, floor stop, est_hold) | ✅ **DONE** 2026-06-23 | `test_levels.py` 9 ✓ |
| **F3** | Profiles & config: blok `regime_profiles` di `settings.yaml` + property `cfg.regime_profiles` + loader | ✅ **DONE** 2026-06-23 | `test_config.py` 15 ✓ |
| **F4** | Classifier RS gate (opt-in via profil) + `relative_strength` wiring di `compute_signals` | ⬜ TODO | `test_classifier.py` (+regresi backward-compat) |
| **F5** | Integrasi `run_daily.py`: resolve regime → profil → classify → levels → record | ⬜ TODO | — |
| **F6** | Alert v2: `format_alert` render level + regime tag (HANYA state MARKUP_*) | ⬜ TODO | `test_alert.py` |
| **F7** | Backtest regime-aware: `replay` regime-per-bar + `simulate_exit` (SL-first) + NULL model | ⬜ TODO | `test_backtest.py` |
| **F8** | **TUNE** (gate sebelum live): rvol per-regime, ablation RS, tentukan angka final di YAML | ⬜ TODO | — |

**Full suite saat ini:** `131 passed`.

---

## Changelog

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
- F1–F3 sudah di-commit di branch `markup-radar-engine` (belum di-push ke remote/VPS/cloud).
- **Utang teknis F4:** rebalance `score_weights` (queue_imbalance 10→5, ihsg 10→5, +relative_strength 10)
  HARUS barengan edit `score.py` §4.7 (tambah norm `relative_strength`), kalau tidak total bobot ≠ 100.
- Spec lengkap per-file ada di `markup-radar-spec-2.md` §4 (modul), §6 (YAML), §7 (test DoD).
