# Desain: Watchlist Volume-Spike (Screener Multi-Pass)

**Status:** RANCANGAN (belum diimplementasi) · Disusun 2026-07-07
**Konteks:** temuan sesi 2026-07-07 — `sma()` kini didukung API screener Invezgo
(`volume > 2 * sma("volume", 10)` → HTTP 201). Lihat [[markup-radar-daily-ops]].

---

## 1. Masalah & tujuan

- Screener produksi sekarang: `close > 100 and value > 3000000000` (filter likuiditas)
  → ~178 saham → **ambil 50 PERTAMA (urutan API, bukan "50 terbaik")** → `watchlist_today.txt`.
- Bottleneck MARKUP: dari data 437 baris, cuma **~10%** yang `rvol ≥ 2.0`. Universe harian
  tidak dipilih untuk volume-spike, jadi kandidat MARKUP kelaparan.
- Sekarang `sma()` jalan → screener BISA pre-filter volume-spike (proxy rvol).

**Tujuan:** naikkan porsi kandidat MARKUP **tanpa** membunuh pipeline
`ACCUMULATION_ONGOING` (akumulasi = volume SEPI/ranging, 17 dari 18 sinyal historis —
akan kepotong kalau screener cuma volume-spike).

## 2. Keputusan arsitektur: SATU watchlist, screener MULTI-PASS

Ditolak: ~~watchlist kedua + cron EOD kedua~~ → menggandakan quota EOD (~4-5 call/saham ×
2 set) dan menghasilkan 2 batch Telegram. Tidak perlu.

**Dipakai:** `screen_universe.py` menjalankan **beberapa pass formula**, hasilnya
**di-union + dedupe** jadi SATU `watchlist_today.txt` dengan cap total tetap 50.
Engine (`run_daily.py`) TIDAK disentuh — ia tetap scan semua kode & classify; saham
volume-spike natural jadi MARKUP, saham likuid-sepi jadi ACCUMULATION. Pemilahan
markup-vs-akumulasi = tugas classifier, bukan watchlist. **Nol perubahan engine.**

Quota EOD **tidak berubah** (cap 50 dipertahankan); cuma +1 HTTP call di tahap screener.

## 3. Skema config (settings.yaml, blok `screener`)

```yaml
screener:
  enabled: true
  category: [IDXENERGY, IDXBASIC, IDXINDUST, IDXNONCYC, IDXCYCLIC, IDXHEALTH,
             IDXFINANCE, IDXPROPERT, IDXTECHNO, IDXINFRA, IDXTRANS]
  max_watchlist: 50            # cap TOTAL setelah union (kontrol quota)
  inter_pass_delay_sec: 8      # jeda antar-pass (endpoint throttle rewel; 2 call aman)

  # BARU — daftar pass, URUT = prioritas saat total > max_watchlist.
  # Pass pertama (markup/spike) diisi DULU; sisa slot diisi pass berikutnya.
  passes:
    - name: markup             # volume-spike = kandidat rvol langka → prioritas
      formula: 'close > 100 and value > 3000000000 and volume > 2 * sma("volume", 20)'
      max: 30                  # cadangkan >=20 slot utk likuiditas (jaga akumulasi)
    - name: liquidity          # universe likuid (buat deteksi akumulasi senyap)
      formula: 'close > 100 and value > 3000000000'
      max: 50

  # BACKWARD-COMPAT: kalau `passes` absen → pakai `formula` tunggal (perilaku lama).
  formula: 'close > 100 and value > 3000000000'
```

## 4. Algoritma merge (di `screen_universe.py`)

```
result = []                      # urut, dedupe
untuk tiap pass di passes:
    codes = fetch_screened_codes(pass.formula, category, limit=pass.max)
    tambah codes ke result yang BELUM ada (dedupe, jaga urutan)
    kalau len(result) >= max_watchlist: potong ke max_watchlist, stop
    sleep(inter_pass_delay_sec)   # hormati throttle
tulis result -> watchlist_today.txt (atomik)
header: "# N kode (M markup-spike + K likuiditas) | passes: ..."
```

Karena pass `markup` diproses lebih dulu, saham volume-spike **dijamin masuk 50** dan
tidak tertruncate acak seperti sekarang — ini perbaikan nyata bahkan bila kedua pass
pakai `value > 3B` yang sama.

## 5. Rollout 2 fase (gaya kehati-hatian F8)

- **Fase 1 (konservatif, deploy pertama):** pass `markup` pakai floor `value > 3B` SAMA
  dgn likuiditas → spike ⊂ likuid. Efeknya murni **reprioritas** (spike dijamin masuk 50),
  **nol** saham baru, **nol** noise small-cap. Aman, langsung bisa diukur.
- **Fase 2 (opsional, setelah data forward):** turunkan floor spike ke `value > 1000000000`
  → tangkap small-cap yang lagi spike (yang di luar universe likuid). Lebih banyak kandidat
  MARKUP tapi lebih banyak noise/data tipis. Aktifkan HANYA kalau Fase 1 masih terlalu
  sedikit markup, dan sadar risikonya (small-cap = queue tipis, bandar gampang main —
  justru sebab `queue_imbalance_cap` sudah dipasang).

## 6. Quota & throttle

- Screener: **2 call/run** (dari 1). Endpoint throttle ketat (429 setelah ~3 call cepat),
  jadi `inter_pass_delay_sec: 8` cukup. Cron 1×/hari → aman.
- EOD scan: **tak berubah** (cap 50 dipertahankan) → ~200-250 call/run seperti sekarang.
- Total bulanan praktis sama; kenaikan cuma ~22 call screener/bulan (negligible vs 30k).

## 7. Safety / edge case (pertahankan perilaku lama)

- **Pass gagal/kosong (fail-soft):** kalau satu pass error/throttle → log WARN, LANJUT
  pass lain. Kalau pass prioritas (markup) gagal tapi likuiditas sukses → watchlist = likuid
  (degradasi wajar, akumulasi tetap jalan).
- **Semua pass gagal / hasil < `--min-count`:** `watchlist_today.txt` lama DIPERTAHANKAN,
  exit != 0 (persis SAFETY sekarang di screen_universe.py:169).
- **Tulis atomik** (tmp → os.replace) dipertahankan.
- **Hari sepi (0 spike):** watchlist = pure likuiditas → identik perilaku hari ini. Akumulasi
  aman, tidak ada regresi.

## 8. Checklist implementasi (file yang disentuh)

1. `scripts/screen_universe.py`
   - `main()`: baca `sc.get("passes")`; kalau ada → loop multi-pass + merge + cap;
     kalau tidak → jalur `formula` tunggal lama (backward-compat).
   - Faktorkan merge jadi fungsi kecil `merge_passes(passes, category, cap, delay)` +
     unit-testable murni (tanpa network) dgn suntikan fungsi fetch.
   - `write_watchlist_atomic`: header baru mencantumkan komposisi (M spike + K likuid).
   - Pertahankan `--min-count`, `--dry-run`, `--out`, atomic write.
2. `config/settings.yaml`
   - Tambah blok `passes` (Fase 1) + `inter_pass_delay_sec`; sisakan `formula` utk fallback.
   - Update komentar (skema baru).
3. `tests/`
   - `test_screen_universe.py` (baru atau tambah): merge dedupe + prioritas + cap +
     fail-soft satu pass (fetch di-mock; NOL call jaringan).
4. (Opsional) `run_screen.sh` — tak berubah (tetap panggil screen_universe.py).

## 9. Rencana verifikasi (saat implementasi)

- `python scripts/screen_universe.py --dry-run` → lihat komposisi tanpa nulis file.
- Probe 1× tiap formula (jeda ≥8s, throttle-safe) → konfirmasi jumlah & bahwa
  `volume > 2*sma("volume",20)` valid dgn `value>3B` (Fase 1).
- Unit test merge hijau (murni, tanpa API).
- Setelah deploy VPS: cek header `watchlist_today.txt` + log EOD berikutnya — apakah
  porsi MARKUP naik vs baseline.

## 10. Risiko & tunable

| Risiko | Mitigasi |
|---|---|
| Throttle 429 pada 2 call | `inter_pass_delay_sec` (default 8s); pass prioritas dulu |
| Proxy `2*sma` ≠ rvol engine persis | Ini PRE-filter, bukan gate. Engine tetap hitung rvol asli & gate — proxy meleset = saham classify NEUTRAL, tak ada risiko correctness |
| Hari liar: spike ramai crowd-out likuiditas | `passes[markup].max = 30` → cadangkan ≥20 slot likuiditas |
| Fase 2 small-cap noise | Tunda sampai data Fase 1 cukup; `queue_imbalance_cap` sudah meredam artefak ARA |

**Tunable utama:** `passes[markup].max` (alokasi), multiplier `2` & window `20` di formula,
floor `value` pass markup (3B Fase 1 → 1B Fase 2), `inter_pass_delay_sec`.

## 11. Konfirmasi yang masih ngutang (throttle-safe, saat implementasi)

- `ema()` jalan atau tidak (probe sesi ini kena 429 sebelum sempat tes).
- Formula gabungan `close>100 and value>3B and volume>2*sma("volume",20)` balik berapa
  kode across 11 sektor (buat sizing `passes[markup].max`).
