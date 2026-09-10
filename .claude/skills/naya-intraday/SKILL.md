---
name: naya-intraday
description: >-
  Analisa transaksi intraday fast-trade satu saham IDX pakai data realtime
  Invezgo (MCP), lalu keluarkan SATU laporan 4-lensa (Aliran net buy/sell,
  Bid/Offer, Akumulasi/Distribusi broker 5 hari, News/Corp Action) plus blok
  "Ringkasan Struktur". Pakai skill ini SETIAP user minta bedah sebuah emiten
  untuk fast trade / bandarmologi — mis. "analisa [TICKER]", "coba [TICKER]",
  "cek [TICKER]", "bandarmologi [TICKER]", "fast trade [TICKER]", "ini saham
  lagi ngapain", atau cuma menyebut kode saham untuk dibedah. Output bersifat
  READER/EDUKASI, BUKAN ajakan beli-jual. Bukan untuk screening banyak saham,
  bukan analisa fundamental/valuasi, bukan konten edukasi tanpa ticker.
---

# naya-intraday — Reader Analisa Transaksi Fast Trade (Invezgo)

Terjemahkan kondisi transaksi intraday SATU emiten jadi laporan yang bisa langsung
dibaca / diajarkan. Output default = SATU dokumen HTML 4 lensa + Ringkasan Struktur
via `mcp__visualize__show_widget` (2 chart tertanam: net flow + bar broker).
**Kalau tool visual tidak tersedia** (mis. dipakai dari bot teks), keluarkan
laporan TEKS terstruktur dengan urutan lensa yang sama — tanpa chart, angka tetap.

Bahasa output: Indonesia, santai-jelas, jujur soal keterbatasan data.

---

## 0. ATURAN EKSEKUSI (WAJIB)

1. Tarik SEMUA data sekali di awal, PARALEL, jangan bolak-balik. Satu batch:
   momentum (value & volume), intraday per-menit, snapshot intraday (VWAP),
   order-book, broker summary 5 hari, chart daily ~1 bulan, news, disclosure,
   dan cek jam WIB. Status ARA/ARB kelihatan dari order-book + snapshot.
2. Hitung sendiri, jangan ngarang — semua net/lot/harga dihitung dari data yang ditarik.
3. Selalu tutup dengan disclaimer: reader/edukasi, bukan ajakan transaksi, snapshot cepat basi.
4. Jangan duplikasi isi chart di teks — chart dirender, teks merangkum.

### Mapping tool data (pakai yang tersedia di environment)

| Data | MCP markup-radar (mcp-invezgo.wahyuafandi.my.id) | MCP resmi Invezgo (Claude Desktop) |
|---|---|---|
| Arus beli/jual per interval (kumulatif) | `get_done_momentum` (scope value & volume) | `momentum` |
| OHLCV per menit | `get_intraday_ohlc` | `intraday` |
| Snapshot + VWAP (avg) + prev + bid/offer | `get_intraday_snapshot` | `intraday-data` |
| Order book bid[]/offer[] (price/lot/freq) | `get_order_book` | `order-book` |
| Broker summary 5 hari | `get_broker_summary` (from/to) | `summary-stock` |
| Inventory broker intraday (hari ini) | `get_intraday_broker_flow` | `intraday-inventory` |
| OHLCV daily ~1 bulan (S/R & tren) | `get_stock_ohlcv` | `chart` |
| Berita / keterbukaan | `get_stock_news` (category NEWS / REPORT) | `news` / `disclosure` |
| Nama broker dari kode | `get_broker_list` | `list-broker` |
| Profil emiten (papan, umur listing) | `get_company_profile` | `information` |

Catatan kumulatif: kolom buy/sell momentum bersifat KUMULATIF —
net per interval = delta_buy − delta_sell.

---

## 1. HITUNGAN INTI

- VWAP = snapshot `avg`. Kalau mencurigakan (mis. = close persis padahal range lebar),
  hitung ulang value/volume. VWAP = garis kendali: harga > VWAP pembeli unggul; < VWAP penjual unggul.
- Net kumulatif (value) = buy_terakhir − sell_terakhir dari momentum(value). Miliar (M) utk likuid, juta (jt) utk tipis.
- Net kumulatif (lot) = (buy − sell)/100 dari momentum(volume).
- Net per interval: value = (Δbuy − Δsell)/1e9 (M); lot = (Δbuy − Δsell)/100.
- Volume (lot) = volume/100. Value = snapshot `value`.
- lot/freq per level order book = rata-rata lot per order (tangan besar vs kerumunan).
- % change = (close − prev)/prev.

---

## 2. FORMAT OUTPUT — 1 laporan, urutan tetap

Mode widget: render via `mcp__visualize__show_widget` (baca `read_me` modul chart/data_viz dulu).
Satu widget = 4 card (lensa), 2 chart Chart.js: net flow (Lensa 1) + bar broker (Lensa 3).
Mode teks: 4 seksi dengan header sama, tabel markdown ringkas, angka tetap disebut.

### LENSA 1 — Aliran Transaksi (Net Buy/Sell)
- Header: TICKER  harga  +delta (+%)  [badge: ARA/ARB/TUTUP]  jam WIB.
- Metric: VWAP, High/Low, Value / Volume(lot), Net (value + lot).
- Chart net flow: bar = net per interval VALUE (hijau #199e70 beli, merah #e34948 jual);
  garis harga biru #3987e5 sumbu kanan. Tooltip tampil value + lot sekaligus.
- Gauge Net Dist ↔ Net Acc sesuai net kumulatif.
- Reading 1 paragraf: fase akum/distribusi, momen markup/distribusi, sebut angka value + lot.

### RINGKASAN STRUKTUR (tepat di bawah metric Lensa 1)
Sintesa 3 sinyal jadi 1 status:
1. Bid/Offer: ada lot jomplang / besar sendiri? Silang dengan status net + freq. Pasar tutup → "tidak live".
2. Volume: bandingkan vs rata-rata daily (chart). Naik + ada sentimen = organik;
   naik tanpa sentimen = tanda tanya (spekulatif). Sebut likuiditas tipis kalau value kecil.
3. Chart flow: rangkum 1 kalimat.
→ Status: akumulasi / distribusi / netral (+ kuat/tipis) dan seberapa reliable.

### LENSA 2 — Bid / Offer
- Big money? Tabel: harga, lot, freq, lot/order, kolom "Baca".
  - lot tebal + freq sedikit = big money satu tangan.
  - lot tebal + freq banyak = kerumunan ecer.
  - freq meledak + lot mungil seragam = pecah-lot/layering (spoof).
- Wall asli vs semu: "ditahan/refill" hanya kalau lot bertahan SAAT volume memakan.
  Bedakan supply riil di harga sekarang vs antrian numpuk di ARA.
- Bid rapih / spoof: bid rata tanpa gap → cek freq (tinggi=asli; rendah seragam=spoof) + silang net flow.
  Bid rata + net jual di atas = kemungkinan support kosmetik untuk distribusi.
  Ingat: bid = niat, bisa dicabut; cuma DONE yang riil.
- Support/Resist: level chart (5-menit + daily) dipadu konfirmasi order book — selaras = makin valid.
- Pendalaman opsional: `get_order_queue` (antrian per level) & `get_price_volume_profile`
  (volume per harga) untuk memastikan siapa antri di level kunci.

### LENSA 3 — Akum / Distribusi Broker (5 hari)
- Bar chart net broker (top 5 akum + top 5 distribusi), satuan M/jt.
- Dua tabel: kode, nama (dari get_broker_list — jangan tebak), net, tipe
  (Ritel / Asing / Institusi / Underwriter / Campuran).
- Verdict: konsentrasi (njomplang?) + siapa penampung (ritel vs asing/institusi).
- Tag Underwriter dua arah: top broker = penjamin emisi IPO → cek dia akumulasi (bullish)
  atau distribusi ke ritel (waspada) — jangan diasumsikan.
- Intraday: `get_intraday_broker_flow` untuk lihat broker mana nyerok/buang HARI INI.

### LENSA 4 — News, Corp Action & Keterbukaan
- Banner katalis / bendera merah (lihat GUARDS).
- Tabel: corp action (dividen/split/akuisisi/RUPS), fundamental ringkas, catatan risiko.
- Verdict akhir + saran fast-trade netral (level kunci, jangan ngejar di pucuk), disclaimer.

---

## 3. GUARDS (deteksi otomatis)

- Likuiditas tipis: value harian < ~Rp1-2 M atau freq rata ≤ ~5 → order book/broker tak reliable; andalkan news/flow.
- Pasar tutup: order-book kosong (freq 0) / jam > 16:00 WIB → pakai snapshot terakhir + S/R chart;
  jangan baca buku kosong sebagai "wall hilang".
- ARA/ARB: offer[] kosong (ARA) / bid[] kosong (ARB) → badge; baca antrian sisi tersisa; harga mentok.
- UMA: news/disclosure memuat "UMA"/"Unusual Market Activity" → banner merah; konteks mengalahkan teknikal.
- Suspend: "suspensi/penghentian sementara perdagangan" → banner merah (sejajar UMA).
- IPO baru: corp action CONVERTION / IPO < ~1 bulan → riwayat pendek, S/R belum matang, ekstra volatil.
  (Cek juga get_company_profile: listing_date + papan.)
- Papan Pengembangan / pompom / free float kecil → bendera merah, spekulatif.
- Divergensi harga vs flow: harga hijau tapi net jual (atau sebaliknya) → highlight distribusi/akumulasi tersembunyi.
- Corp action: split/akuisisi/M&A/dividen → tag katalis; split → ingatkan harga adjust dibagi rasio.
- Leverage: akuisisi dibiayai utang afiliasi besar → catat risiko.

---

## 4. ATURAN SINTESA (logic)

- Bid besar sendiri + net akum = konfirmasi akum. Offer besar sendiri + net distribusi + bid tipis = konfirmasi distribusi.
  Offer besar sendiri TAPI net akum = mungkin target jual bandar (masih akum di bawah) — cek freq & jarak harga dulu.
- Volume tinggi + berita = organik; volume tinggi tanpa berita = waspada.
- Candle hijau BUKAN berarti dibeli: selalu cek net flow. Hijau + net jual = distribusi ke dalam kekuatan.
- Saham likuid (freq besar semua) → jejak bandar bukan di order book; pindah ke broker summary.
  Saham tipis → order book & broker kecil, sinyal lemah.

---

## 5. CATATAN RENDER (mode widget)

- Chart dibangun ulang dari momentum (5-menit) + intraday — bukan screenshot platform lain
  ("konsep sama, sumber beda").
- Warna: net buy #199e70, net sell #e34948, price #3987e5, grid #2c2c2a, tick #898781.
- Angka dibulatkan / `toLocaleString('id-ID')`. Saham tipis → juta (jt); likuid → miliar (M).
- Ikuti aturan visual host: transparan, dark-mode aman, CSS variables.

---

## 6. DISCLAIMER (selalu)
Reader/edukasi, bukan ajakan transaksi. Snapshot intraday berubah cepat. Definisi net
buy/sell Invezgo belum tentu identik platform lain. Broker net = agregat banyak klien,
bukan tentu satu pihak.
