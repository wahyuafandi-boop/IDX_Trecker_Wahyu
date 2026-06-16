# Progress Riset — Sumber Data Markup Radar

> Catatan diskusi & keputusan terkait pemilihan sumber data untuk engine **Markup Radar**
> (lihat spec lengkap di `markup-radar-spec-1.md`).
> Update terakhir: 2026-06-16

---

## 1. Konteks singkat

Project **Markup Radar** butuh data EOD IDX untuk menghitung 9 sinyal (S1–S9) dan
mengklasifikasi state saham (`MARKUP_START`, `ACCUMULATION_ONGOING`, dst).

Sinyal terbagi 2 kelompok berdasarkan ketersediaan data:

- **S3–S9** (broker flow, foreign, OHLCV, volume, close, index): relatif mudah didapat.
- **S1, S2** (Done Ratio & Absorption — berbasis **done by bid/offer**): ini "jantung" engine,
  tapi datanya **paling langka**. Inilah **gating question** utama (spec §7).

---

## 2. Konsep kunci: Done by Bid vs Done by Offer

- **BID** = harga yang mau dibayar pembeli (antrian beli).
- **OFFER** = harga yang mau dilepas penjual (antrian jual).
- **Done di OFFER** = pembeli agresif mengangkat offer → demand kuat → harga cenderung naik.
- **Done di BID** = penjual agresif lego ke bid → supply menang → harga cenderung turun.
- **Done di BID tapi harga DITAHAN = ABSORPSI** → bandar menyerap jualan diam-diam
  → **akumulasi masih berlangsung**. Ini nuansa kunci yang dicari engine.

**Beda penting (sering ketukar):**
- *Closing bid/offer queue* (S5) = foto antrian saat close. → relatif tersedia.
- *Done by bid/offer* (S1/S2) = klasifikasi tiap transaksi yang sudah terjadi. → langka.

Data done detail **bukan data mentah** — ini hasil hitungan dari data **tick (trade-by-trade)**.
Aplikasi seperti Stockbit & datasahambei menghitungnya sendiri.

---

## 3. Evaluasi sumber data

| Sumber | Done bid/offer (S1/S2) | Broker flow (S3–S9) | API otomatis? | Biaya | Catatan |
|---|---|---|---|---|---|
| **MarketFlow** (RapidAPI) | ❌ | ❌ | ✅ | free / ~$15+ | Data global/forex (XAUUSD dll). **Salah alat** untuk IDX. Publisher `yasimpratama88`. |
| **OHLC.dev IDX** (RapidAPI) | ❌ | ✅ | ✅ | free tier, ~$5–15/bln | IDX-native. Broker summary, foreign, OHLCV, index. Publisher sama: `yasimpratama88`. |
| **Sectors.app** | ❌ | ✅✅ | ✅ | ada free tier + berbayar (angka belum dikonfirmasi) | IDX-native, fitur **Bandarmology** kuat + label akum/distribusi bawaan + LLM-ready query. **Kandidat terbaik untuk S3–S9.** |
| **datasahambei** (app Android, `com.kajokangin.indosaham`) | ✅ (premium) | ✅ | ❌ **manual only** | Premium: Rp 85rb/bln, Rp 249rb/3bln, Rp 990rb/thn | Punya **Trade Book** (BLot/SLot/BFreq/SFreq per harga) = data done detail asli! Tapi **tidak ada API**, bayar via Google Play, cuma bisa dilihat manual di app. |

### Catatan verifikasi datasahambei (dari screenshot app)
- **Order Book** tab = bid/offer queue (S5). ✅
- **Trade Book** tab = **Done Summary** per harga: `Price | Freq | BLot | SLot | Lot | BFreq | SFreq`.
  - `BLot` = volume done di OFFER (buy). `SLot` = volume done di BID (sell).
  - **Ini bahan mentah persis untuk S1 (Done Ratio) & S2 (Absorption).**
  - Versi gratis hanya menampilkan sebagian level harga teratas ("Upgrade to Premium to show all data").
- **Bandarmology** tab = analisis akumulasi/distribusi per periode + NBSA foreign (premium-locked).
- Fitur premium memuat **"Detail Bid/Offer Info"** = done detail penuh.

---

## 4. Temuan utama (gating question — TERJAWAB)

> **Done by bid/offer (S1/S2) TIDAK tersedia sebagai API legal & murah.**

- Datanya **nyata & dipublikasi** (datasahambei & Stockbit menampilkannya), murah (~Rp 85rb/bln di app),
  TAPI **hanya bisa dilihat manual di aplikasi — tidak ada API → tidak bisa diotomasi** untuk scan banyak saham.
- API resmi (OHLC.dev / Sectors / Invezgo) hanya punya "trading summary" agregat (value/volume/freq),
  **bukan** breakdown BLot/SLot per harga.
- Untuk dapat S1/S2 otomatis & legal: butuh **data tick intraday** (tier mahal/berlisensi, Tier 3 spec §7)
  lalu hitung sendiri — di luar scope MVP.

Ini konsisten dengan peringatan di spec §7 (Tier 2: done detail = data spesialis yang langka).

---

## 5. KEPUTUSAN

**MVP dibangun dengan Sectors.app dalam "mode degraded" (S3–S9), skip S1/S2 dulu.**

Alasan:
1. Sebagian besar value engine (deteksi akumulasi & markup) tetap jalan dari broker flow + foreign + volume + close.
2. Legal, stabil, bisa diotomasi untuk scan banyak saham (beda dari app yang manual).
3. Bisa mulai menghasilkan output cepat, tidak terjebak mencari data langka berbulan-bulan.

**S1/S2 (absorpsi via done detail) = enhancement v2** kalau nanti dapat akses tick data / API done summary.

---

## 6. Tindakan yang sudah dilakukan

- ✅ Riset & evaluasi 4 sumber data (lihat tabel §3).
- ✅ Konfirmasi gating question via screenshot app datasahambei.
- ✅ Draft email ke **data.saham.bei@gmail.com** dibuat di Gmail (folder Draft) — menanyakan
  apakah ada **API / ekspor data** untuk Done Summary, Broker Summary, Foreign.
  **Status: menunggu user menekan Send** (tool hanya bisa membuat draft, tidak mengirim).

---

## 7. Langkah berikutnya (TODO)

- [ ] **Kirim email** ke datasahambei (dari folder Draft Gmail) → tunggu jawaban soal akses API.
- [ ] **Verifikasi Sectors.app**: cek halaman pricing (sectors.app/pricing) — free tier berapa request/hari,
      plan termurah, apakah endpoint Bandarmology/broker termasuk.
- [ ] **(Opsional) Evaluasi Invezgo** sebagai kandidat tambahan (klaim: analisa transaksi broker + foreign).
- [ ] **Scaffold project MVP** (struktur spec §11) dirancang untuk Sectors.app + degraded mode:
      folder structure, `.env.example`, client ingestion.
- [ ] Implement S3–S9 + unit test (spec Phase 2).
- [ ] Tarik data histori untuk backtesting awal (validasi threshold).

---

## 8. Open question yang masih perlu keputusan

1. Universe saham: scan seluruh IDX, LQ45, atau watchlist manual? (pengaruh ke rate limit)
2. Kalau datasahambei membalas & menyediakan API done summary → integrasi sebagai sumber S1/S2?
3. Budget bulanan untuk data (Sectors plan + kemungkinan datasahambei)?
