"""S5 Closing Queue Imbalance, S6 RVOL, S7 Close-in-range (spec §3)."""

from __future__ import annotations

import pandas as pd


def rvol(volume_series: pd.Series, window: int = 20) -> float:
    """S6: volume hari ini / MA(window) volume. >=2.0 = spike.

    Butuh minimal `window`+1 data; kalau kurang, pakai rata-rata yang ada.
    """
    vol = pd.Series(volume_series).dropna()
    if len(vol) < 2:
        return 0.0
    today = vol.iloc[-1]
    hist = vol.iloc[-(window + 1):-1]
    avg = hist.mean()
    if not avg or avg <= 0:
        return 0.0
    return float(today / avg)


def close_in_range(high: float, low: float, close: float) -> float:
    """S7: (close - low) / (high - low). >0.6 = close kuat."""
    rng = high - low
    if rng <= 0:
        return 0.5
    return float((close - low) / rng)


def queue_imbalance(
    bid_volume: float, offer_volume: float, *, cap: float | None = None
) -> float:
    """S5: bid_volume / offer_volume di close. >1 demand menumpuk.

    `cap` (opsional, dipakai jalur EOD): batas rasio yang "masih bisa dipercaya".
    Rasio di ATAS cap = buku offer nyaris kosong (close near-ARA, sisi jual tinggal
    recehan) → rasio meledak (bisa ratusan ribu) dan TAK andal sebagai konfirmasi
    "demand terkendali menumpuk". Diperlakukan seperti offer<=0 → 0.0 (tak bisa
    dibandingkan; JANGAN dianggap konfirmasi) supaya tak minting MARKUP_CONFIRMED
    palsu. Default None = tanpa cap → dipakai jalur LIVE tape-reading yang sudah
    punya proteksi buku tipis sendiri (lot_per_order / RITEL_NOISE).
    """
    if offer_volume <= 0:
        return 0.0
    ratio = float(bid_volume / offer_volume)
    if cap is not None and ratio > cap:
        return 0.0
    return ratio


def queue_verdict(imbalance: float, demand: float = 1.0, neutral_low: float = 0.8) -> str:
    """Label interpretasi S5 untuk live watch (tape-reading bid/offer).

    - imbalance >= demand    -> "DEMAND_DOMINAN" (antri beli dominan = konfirmasi
                                bandar mulai markup)
    - imbalance <  neutral_low -> "SUPPLY_DOMINAN" (antri jual dominan = ditahan/
                                distribusi)
    - di antaranya           -> "SEIMBANG" (belum jelas, masih dikumpulkan)
    - <= 0                   -> "NO_DATA"
    """
    if imbalance <= 0:
        return "NO_DATA"
    if imbalance >= demand:
        return "DEMAND_DOMINAN"
    if imbalance < neutral_low:
        return "SUPPLY_DOMINAN"
    return "SEIMBANG"


def lot_per_order(lot: float, freq: float) -> float:
    """Rata-rata lot per order (lot / freq) — sidik jari "big money".

    Tape-reading bandarmologi: yang penting bukan total lot, tapi ISI antrian.
    Order gede atau lot seragam (mis. 100 semua) -> lot/order tinggi = dana besar;
    banyak order kecil acak (ritel) -> lot/order rendah. freq<=0 -> 0 (tak bisa
    disimpulkan). Catatan: rincian per-order (sabar 1-1-1) tak ada di data agregat
    Invezgo, jadi ini proksi untuk "order gede / seragam", bukan deteksi 1-1-1.
    """
    if freq <= 0:
        return 0.0
    return float(lot / freq)


def queue_composition_verdict(
    bid_lot: float,
    bid_freq: float,
    offer_lot: float,
    offer_freq: float,
    *,
    demand: float = 1.0,
    neutral_low: float = 0.8,
    bigmoney_lot_per_order: float = 20.0,
) -> str:
    """Verdict S5 yang membaca KOMPOSISI antrian (top-N), bukan cuma rasio lot.

    Inti bandarmologi: rasio bid/offer mentah bisa dikondisikan (apalagi mini-cap).
    Yang lebih jujur = lot/order (lot/freq) tiap sisi — lihat `lot_per_order`.
    Pakai pada agregat top-N (4 baris teratas yang "nyata"), bukan semua level.

    Label (prioritas atas-ke-bawah):
      - NO_DATA        : kedua sisi kosong.
      - DEMAND/SUPPLY  : satu sisi kosong (tak ada antri jual/beli).
      - PASSIVE_ACCUM  : rasio TIDAK demand-dominan, tapi bid diisi big money &
                         lot/order bid >= offer -> bandar nampung diam-diam (ikut beli).
      - PASSIVE_DISTRIB: rasio TIDAK supply-dominan, tapi offer diisi big money &
                         lot/order offer > bid -> jual diam-diam (jangan beli).
      - RITEL_NOISE    : tak ada big money di kedua sisi -> rasio tak bisa dipercaya.
      - DEMAND_DOMINAN : ada big money & rasio bid/offer >= demand.
      - SUPPLY_DOMINAN : ada big money & rasio < neutral_low.
      - SEIMBANG       : sisanya.
    """
    if bid_lot <= 0 and offer_lot <= 0:
        return "NO_DATA"
    if offer_lot <= 0:
        return "DEMAND_DOMINAN"   # tak ada antri jual
    if bid_lot <= 0:
        return "SUPPLY_DOMINAN"   # tak ada antri beli

    imb = queue_imbalance(bid_lot, offer_lot)
    bid_lpo = lot_per_order(bid_lot, bid_freq)
    off_lpo = lot_per_order(offer_lot, offer_freq)
    bid_big = bid_lpo >= bigmoney_lot_per_order
    off_big = off_lpo >= bigmoney_lot_per_order

    # 1) big money tersembunyi — nilai utama tape-reading (kontra-intuitif).
    if bid_big and bid_lpo >= off_lpo and imb < demand:
        return "PASSIVE_ACCUM"
    if off_big and off_lpo > bid_lpo and imb >= neutral_low:
        return "PASSIVE_DISTRIB"
    # 2) tak ada big money di mana pun -> rasio tipis/ritel, jangan dibaca arah.
    if not bid_big and not off_big:
        return "RITEL_NOISE"
    # 3) ada big money & rasio terang.
    if imb >= demand:
        return "DEMAND_DOMINAN"
    if imb < neutral_low:
        return "SUPPLY_DOMINAN"
    return "SEIMBANG"


def queue_intent_verdict(
    bid_lot: float,
    bid_freq: float,
    offer_lot: float,
    offer_freq: float,
    *,
    accumulating: bool,
    demand: float = 1.0,
    neutral_low: float = 0.8,
    bigmoney_lot_per_order: float = 20.0,
) -> str:
    """Verdict S5 KONTEKSTUAL: komposisi order book + status akumulasi broker (S3).

    Inti "fake bit / fake over" (transkrip 3 & 4): tembok besar di order book itu
    AMBIGU kalau dibaca sendirian — artinya dibalik oleh ada/tidaknya akumulasi:

      - Tembok OFFER tebal (big money di sisi jual, offer >= bid):
          * akumulasi  -> FAKE_OVER  : market maker tahan harga buat nampung murah
                                       = BULLISH (jadikan watchlist, entry saat jebol).
          * tak akum   -> SUPPLY_REAL: suplai/distribusi asli = BEARISH (hindari).
      - Tembok BID tebal (big money di sisi beli, bid > offer):
          * tak akum   -> FAKE_BID   : ilusi demand sambil distribusi = JEBAKAN.
          * akumulasi  -> DEMAND_REAL: demand asli = BULLISH.
      - Tak ada tembok big money -> fallback `queue_composition_verdict`
        (PASSIVE_ACCUM/RITEL_NOISE/SEIMBANG/...).

    Kunci video: "fake over valid kalau ADA akumulasi; kalau enggak ada, enggak
    valid." Maka `accumulating` (dari S3 broker net-buy streak) WAJIB diisi; kalau
    status tak diketahui, pass False (konservatif -> tembok dianggap distribusi).
    """
    if bid_lot <= 0 and offer_lot <= 0:
        return "NO_DATA"

    bid_lpo = lot_per_order(bid_lot, bid_freq)
    off_lpo = lot_per_order(offer_lot, offer_freq)
    bid_big = bid_lpo >= bigmoney_lot_per_order
    off_big = off_lpo >= bigmoney_lot_per_order

    # Tembok OFFER (offer lebih berat & diisi big money) — sinyal kunci video.
    if off_big and offer_lot >= bid_lot:
        return "FAKE_OVER" if accumulating else "SUPPLY_REAL"
    # Tembok BID (bid lebih berat & diisi big money).
    if bid_big and bid_lot > offer_lot:
        return "DEMAND_REAL" if accumulating else "FAKE_BID"
    # Tak ada tembok big money -> baca komposisi murni.
    return queue_composition_verdict(
        bid_lot, bid_freq, offer_lot, offer_freq,
        demand=demand, neutral_low=neutral_low,
        bigmoney_lot_per_order=bigmoney_lot_per_order,
    )


def price_change(prev_close: float, close: float) -> float:
    """Perubahan harga absolut (untuk absorption flag S2)."""
    return float(close - prev_close)


def price_ranging(closes: pd.Series, window: int = 10, band: float = 0.05) -> bool:
    """True bila harga bergerak sideways (range sempit) dalam `window` terakhir."""
    c = pd.Series(closes).dropna().iloc[-window:]
    if len(c) < 2 or c.mean() == 0:
        return False
    spread = (c.max() - c.min()) / c.mean()
    return bool(spread <= band)


def near_range_high(high: float, low: float, close: float, threshold: float = 0.8) -> bool:
    """True bila close berada di dekat puncak range harian (untuk distribusi)."""
    return close_in_range(high, low, close) >= threshold


def atr(high, low, close, period: int = 14) -> float:
    """Average True Range (Wilder disederhanakan = SMA of True Range).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|). Dipakai untuk
    trade levels (jarak SL ATR-based). 0.0 bila tak ada data.
    """
    h, l, c = (pd.Series(x).astype(float) for x in (high, low, close))
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).dropna()
    if tr.empty:
        return 0.0
    win = tr.iloc[-period:] if len(tr) >= period else tr
    return float(win.mean())


def donchian(high, low, lookback: int = 20) -> tuple[float, float]:
    """(resistance, support) = (max high, min low) `lookback` hari terakhir.

    Dipakai untuk anchor breakout level (entry = resis + buffer). (0.0, 0.0) bila
    tak ada data.
    """
    h = pd.Series(high).dropna().iloc[-lookback:]
    l = pd.Series(low).dropna().iloc[-lookback:]
    if h.empty or l.empty:
        return 0.0, 0.0
    return float(h.max()), float(l.min())


# --- Cabut vs Dimakan (tape-reading): tembok offer menyusut karena apa? -------

def _bar_field(bar: dict, *names: str):
    """Ambil field bar intraday dengan toleransi variasi nama (high/h, volume/v/vol)."""
    for n in names:
        for k, v in bar.items():
            if str(k).lower() == n and v is not None:
                return v
    return None


def _parse_bar_time(value) -> "dt_.datetime | None":
    """Waktu bar intraday -> datetime lokal tz-naive. None bila tak bisa diparse.

    Toleran: epoch detik/milidetik (angka), string ISO (buang 'Z'/offset),
    atau 'HH:MM(:SS)' saja (digabung dengan tanggal hari ini).
    """
    import datetime as dt_
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:          # milidetik
            ts /= 1000.0
        try:
            return dt_.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip().replace("Z", "")
    try:
        parsed = dt_.datetime.fromisoformat(s)
        # ISO ber-offset (+07:00) -> buang tzinfo, jam sudah lokal-ish; yang
        # penting konsisten antar bar utk filter "sejak siklus lalu".
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    try:                        # 'HH:MM' / 'HH.MM' / 'HH:MM:SS'
        hhmm = s.replace(".", ":")
        t = dt_.time.fromisoformat(hhmm if hhmm.count(":") >= 1 else f"{hhmm}:00")
        return dt_.datetime.combine(dt_.date.today(), t)
    except ValueError:
        return None


def wall_drop_verdict(bars, wall_price: float, since=None) -> str | None:
    """Bedakan tembok offer DIMAKAN vs DICABUT (ajaran tape-reading: "dicabut apa
    dimakan — liat done detail"). `bars` = bar intraday mentah (list of dict).

    - "EATEN"  : sejak `since` ada perdagangan menyentuh harga tembok
                 (high >= wall_price, volume > 0) -> demand asli menyerap barang
                 = timing entry klasik.
    - "PULLED" : harga TIDAK pernah menyentuh level tembok sejak `since` -> lot
                 hilang pasti ditarik (fake offer terkonfirmasi; positif tapi
                 belum ada demand nyata).
    - None     : data tak ada / shape tak dikenali / timestamp tak bisa diparse
                 padahal `since` diminta -> jangan menebak.
    """
    if wall_price is None or wall_price <= 0 or not bars:
        return None
    parsed: list[tuple] = []       # (time|None, high, volume)
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        high = _bar_field(bar, "high", "h")
        vol = _bar_field(bar, "volume", "v", "vol")
        t = _parse_bar_time(_bar_field(bar, "time", "date", "timestamp", "t"))
        try:
            parsed.append((t, float(high), float(vol) if vol is not None else 0.0))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    if since is not None:
        timed = [p for p in parsed if p[0] is not None]
        if not timed:
            return None            # tak bisa filter waktu -> jangan menebak
        window = [p for p in timed if p[0] >= since]
        # Tak ada bar sejak siklus lalu = tak ada transaksi sama sekali ->
        # tembok menyusut tanpa perdagangan = pasti dicabut.
        if not window:
            return "PULLED"
        parsed = window
    high_since = max(p[1] for p in parsed)
    vol_since = sum(p[2] for p in parsed)
    return "EATEN" if (high_since >= wall_price and vol_since > 0) else "PULLED"
