# Markup Radar v2 — Refactor Spec: Regime-Aware Swing Engine + Trade Levels

> **Untuk dieksekusi via Claude Code.** Lanjutan & restrukturisasi dari `markup-radar-spec-1.md`.
> **Package tetap `markup_radar`** (jangan rename — churn tak berguna).
> **Bahasa:** prosa Indonesia, kode/identifier English (sesuai gaya repo).

---

## 0. Ringkasan perubahan (TL;DR untuk Claude Code)

Status sekarang: Phase 0–5 jalan; IHSG sudah dilepas dari hard-gate dan diturunkan
jadi bobot confidence (tuned 2026-06-21). Spec ini menyelesaikan transisi itu menjadi
arsitektur **regime-aware** penuh + menambah **entry/SL/TP levels**.

| # | Perubahan | File utama | Sifat |
|---|---|---|---|
| C1 | IHSG jadi **REGIME SELECTOR** (bukan cuma bobot) — memilih profil parameter | `signals/market.py`, `scoring/classifier.py` | struktural |
| C2 | Dua **profil regime**: `BULLISH` / `BEARISH` (rvol, RS, ATR-SL, R:R, risk beda) | `config/settings.yaml`, `config.py` | struktural |
| C3 | **Relative Strength (S10)**: di regime BEARISH, saham wajib outperform IHSG | `signals/market.py`, `signals/__init__.py`, `classifier.py` | sinyal baru |
| C4 | **Trade levels** (entry/SL/TP) — ATR-based, **R:R DIHITUNG** bukan dilabel | `signals/levels.py` (baru), `signals/price_volume.py` | fitur baru |
| C5 | **Alert format v2** — level + est_hold + regime tag, hanya untuk state MARKUP_* | `alert/telegram.py` | UX |
| C6 | **Backtest per-regime** + simulasi exit level + null model | `backtest/engine.py`, `backtest/metrics.py` | validasi |

**Properti penting: backward-compatible.** Signature `classify(signals, thresholds)` tidak
berubah. Gate baru (RS) bersifat *opt-in* lewat key profil (`require_relative_strength`),
jadi default → no-op → **semua test lama tetap lulus**.

---

## 1. Apa yang SUDAH benar — JANGAN diutak-atik

Ini hasil kerja keras yang sudah tervalidasi. Pertahankan apa adanya:

1. **Inversi arah done** di `done_client.py` (`done_offer ← field 'sell'`, `done_bid ← field 'buy'`).
   Diverifikasi empiris pada BBRI/BBCA/TLKM (korelasi negatif). **Jangan dibalik lagi.**
2. **Metrik validasi pakai `fwd_close`, bukan `fwd_max`.** `fwd_max` = spike intraday yang menyesatkan.
   Semua tuning/penilaian edge harus pakai `fwd_close`. (Pelajaran 2026-06-21.)
3. **Horizon 10–20 hari**, bukan 5 hari. Edge cuma muncul di sini. Default backtest = 20d.
4. **rvol_spike = 2.0** (bukan 1.5) sebagai baseline. Menurunkan = nambah noise/bull-trap.
5. **`latest_available_done_date`** (momentum-chart telat ~1 hari). Pertahankan.
6. **Coverage tracking** di `HistoryCache` (cache parsial ≠ lengkap). Pertahankan.
7. **Broker daily-net forward-fill** + delta kumulatif. Pertahankan.
8. **`live_watch.py`** (tool intraday manual S5). Pertahankan — lihat §9.

---

## 2. Filosofi sistem (guardrails — BACA sebelum coding)

Ini sistem **swing confirmation engine (10–20 hari)**, bukan intraday. Prinsip yang
TIDAK boleh didrift selama refactor:

- **Market jelek → lebih SELEKTIF, bukan lebih longgar.** Saat IHSG < MA50, palang dinaikkan
  (rvol lebih tinggi + wajib relative strength), bukan diturunkan. Akumulasi smart money memang
  terjadi di markdown, tapi mayoritas bounce di markdown = bull trap. Edge datang dari selektivitas.
- **NEUTRAL itu jawaban yang valid.** Kalau tak ada footprint akumulasi beneran, diam = benar.
  Jangan tuning cuma supaya sinyal nyala.
- **Level = sinyal, bukan order.** Engine tidak eksekusi. Sizing & eksekusi tetap manual.
- **R:R DIHITUNG dari level aktual, tidak pernah dilabel.** (Bug klasik: entry 158 / SL 138 / TP 178
  dilabel "R:R 2" padahal realized 1.0.) Yang ditampilkan di alert = angka asli.
- **Musuh utama di counter-trend = death by a thousand cuts.** Profil BEARISH yang ketat + risk
  dipotong = mekanisme bertahan, bukan banyaknya sinyal.

---

## 3. Keputusan desain terkunci (D1–D7)

Ini sekaligus **jawaban untuk 3 pertanyaan setup levels** (R:R, SL, state mana):

| ID | Keputusan | Rationale |
|---|---|---|
| **D1** | IHSG = **regime selector** (BULLISH/BEARISH), bukan veto & bukan cuma bobot | Veto bikin engine bisu di market lemah; bobot saja tak cukup melindungi. Regime nge-swap seluruh playbook. |
| **D2** | **R:R = 2.0** untuk dua regime (samakan dengan MT5 Agent) | Satu mental-model risk untuk dua sistem (DRY). |
| **D3** | **SL = ATR-based** (2.0× ATR di BULLISH, 1.8× di BEARISH), **bukan** 20-day-low | 20-day-low di gocap bisa 15–25% jauh → R:R 2 maksa TP +30–50% (sebulan+). ATR volatility-normalized & konsisten dgn MT5 Agent. |
| **D4** | **Floor stop 3%** (`min_stop_pct`) | Pengaman keras: berapapun ATR, SL tak pernah lebih ketat dari 3% → cegah whipsaw. |
| **D5** | **Levels hanya untuk `MARKUP_START` & `MARKUP_CONFIRMED`** | State lain belum/tak akan breakout. `ACCUMULATION_ONGOING` cukup tampil "resis to watch". (Sesuai usulanmu — instinct benar.) |
| **D6** | **Horizon target 10–20d**, holding **EMERGE dari jarak TP** (bukan di-set "max 1 minggu") | Holding tak bisa di-set langsung; cuma bias ke target lebih dekat → stop lebih ketat → whipsaw. `est_hold_days` ditampilkan agar tiap alert jujur soal lama swing. |
| **D7** | **Risk per trade beda per regime** (1% BULLISH, 0.5% BEARISH) — metadata sinyal, bukan eksekusi | Counter-trend = win-rate lebih rendah (base-rate); potong risk yang bikin bertahan. |

> **Catatan kalibrasi rvol:** angka `rvol 2.0` di-tune pada window campuran/bearish 13-saham.
> Setelah split regime, **rvol per-regime WAJIB di-tune ulang** (lihat §5). Tebakan awal:
> BULLISH 2.0, BEARISH 2.5. Jangan anggap final sebelum backtest per-regime.

---

## 4. Modul baru & perubahan per file

### 4.1 Regime selector — `signals/market.py`

Tambah di samping `ihsg_above_ma50` (reuse logikanya):

```python
from enum import Enum

class Regime(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

def market_regime(ihsg_close, window: int = 50) -> Regime:
    """IHSG vs MA(window) → regime. Fail-safe: data kosong/kurang = BEARISH
    (profil lebih ketat saat market tak diketahui)."""
    c = pd.Series(ihsg_close).dropna()
    if c.empty:
        return Regime.BEARISH
    ma = c.iloc[-window:].mean()
    return Regime.BULLISH if c.iloc[-1] > ma else Regime.BEARISH
```

### 4.2 Relative Strength (S10) — `signals/market.py`

```python
def relative_strength(stock_close, ihsg_close, window: int = 20) -> float:
    """Return saham − return IHSG selama `window` hari. >0 = outperform.
    CATATAN: pakai window posisional (bukan date-join). Akurat cukup untuk 20d EOD;
    refine ke date-align bila butuh presisi (lihat §Edge Cases)."""
    s = pd.Series(stock_close).dropna()
    i = pd.Series(ihsg_close).dropna()
    if len(s) < window + 1 or len(i) < window + 1:
        return 0.0
    s_ret = s.iloc[-1] / s.iloc[-(window + 1)] - 1
    i_ret = i.iloc[-1] / i.iloc[-(window + 1)] - 1
    return float(s_ret - i_ret)
```

**Wiring di `signals/__init__.py` (`compute_signals`)** — tambah satu key (nilai mentah,
threshold diterapkan di classifier):

```python
"relative_strength": market.relative_strength(
    df["close"], data.ihsg_close, w.get("rs_window", 20)
) if not df.empty else 0.0,
```

### 4.3 ATR & Donchian — `signals/price_volume.py`

```python
def atr(high, low, close, period: int = 14) -> float:
    """Average True Range (Wilder disederhanakan = SMA of True Range)."""
    h, l, c = (pd.Series(x).astype(float) for x in (high, low, close))
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).dropna()
    if tr.empty:
        return 0.0
    win = tr.iloc[-period:] if len(tr) >= period else tr
    return float(win.mean())

def donchian(high, low, lookback: int = 20) -> tuple[float, float]:
    """(resistance, support) = (max high, min low) `lookback` hari terakhir."""
    h = pd.Series(high).dropna().iloc[-lookback:]
    l = pd.Series(low).dropna().iloc[-lookback:]
    if h.empty or l.empty:
        return 0.0, 0.0
    return float(h.max()), float(l.min())
```

### 4.4 Trade levels — `signals/levels.py` (FILE BARU)

```python
"""Trade levels (entry/SL/TP) untuk state MARKUP_* — ATR-based, R:R DIHITUNG.
Hanya dipanggil untuk MARKUP_START / MARKUP_CONFIRMED. Output dipakai alert +
(opsional) simulasi exit di backtest."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import pandas as pd
from markup_radar.signals.price_volume import atr, donchian


@dataclass
class TradeLevels:
    resistance: float
    support: float
    atr: float
    entry: float
    stop_loss: float
    take_profit: float
    rr_realized: float     # DIHITUNG dari level — bukan dilabel
    stop_pct: float        # jarak SL dari entry (%) → transparansi whipsaw
    est_hold_days: int     # estimasi lama swing → bunuh fantasi "1 minggu"

    def as_dict(self) -> dict:
        return asdict(self)


def compute_trade_levels(
    ohlcv: pd.DataFrame, *,
    lookback: int = 20, atr_period: int = 14, breakout_buffer: float = 0.005,
    atr_mult_sl: float = 2.0, rr_target: float = 2.0,
    min_stop_pct: float = 0.03, hold_slack: float = 1.8,
) -> TradeLevels | None:
    """Hitung level breakout. None bila data kurang."""
    if ohlcv is None or len(ohlcv) < lookback + 1:
        return None
    resistance, support = donchian(ohlcv["high"], ohlcv["low"], lookback)
    a = atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], atr_period)
    if a <= 0 or resistance <= 0:
        return None

    entry = resistance * (1 + breakout_buffer)
    # SL: ATR-based, tapi tak pernah lebih ketat dari min_stop_pct (anti-whipsaw).
    stop_dist = max(atr_mult_sl * a, min_stop_pct * entry)
    stop_loss = entry - stop_dist
    risk = entry - stop_loss
    take_profit = entry + rr_target * risk
    rr_realized = (take_profit - entry) / risk if risk > 0 else 0.0
    stop_pct = stop_dist / entry if entry > 0 else 0.0
    est_hold = int(round((take_profit - entry) / a * hold_slack))

    return TradeLevels(
        resistance=round(resistance, 2), support=round(support, 2), atr=round(a, 2),
        entry=round(entry, 2), stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2), rr_realized=round(rr_realized, 2),
        stop_pct=round(stop_pct, 4), est_hold_days=est_hold,
    )
```

> **Self-check wajib (test):** `rr_realized` harus ≈ `rr_target` (±0.05). Ini yang menutup
> bug "label ≠ realized". Lihat §7.

### 4.5 Profile system — `config/settings.yaml` + `config.py`

Tambah blok baru di `settings.yaml` (lihat §6 untuk YAML lengkap). Di `config.py`,
tambah property:

```python
@property
def regime_profiles(self) -> dict[str, dict]:
    return dict(self.raw.get("regime_profiles", {}))
```

Profil = **overlay thresholds yang dipilih regime**. Cara pakainya:

```python
eff_thresholds = {**cfg.thresholds, **cfg.regime_profiles[regime.value]}
```

### 4.6 Classifier refactor — `scoring/classifier.py`

Satu-satunya perubahan di `classify()`: tambah klausa RS ke `base_markup` (opt-in via profil).
**Tak ada perubahan signature.** RS hanya menggate MARKUP — `ACCUMULATION`/`DISTRIBUTION`
tak tersentuh.

```python
# di dalam classify(), ganti blok base_markup:
require_rs = t.get("require_relative_strength", False)
outperforms = s.get("relative_strength", 0.0) > t.get("rs_min", 0.0)

base_markup = (
    s["done_ratio"] > t["done_ratio_markup"]
    and s["rvol"] >= t["rvol_spike"]
    and s["close_in_range"] > t["close_in_range_strong"]
    and s["broker_net_buy_streak"] >= 1
    and (not require_rs or outperforms)   # ← BEARISH: wajib outperform IHSG
)
```

Integrasi di `run_daily.py` (resolusi regime per run):

```python
from markup_radar.signals.market import market_regime
from markup_radar.signals.levels import compute_trade_levels

regime = market_regime(ihsg_close, cfg.windows.get("ihsg_ma", 50))
profile = cfg.regime_profiles.get(regime.value, {})
eff = {**cfg.thresholds, **profile}

# ... per saham:
state = classify(signals, eff)
conf = confidence_markup_start(signals, cfg.score_weights)
levels = None
if state in ("MARKUP_START", "MARKUP_CONFIRMED"):
    levels = compute_trade_levels(
        data.ohlcv,
        lookback=cfg.windows.get("donchian_lookback", 20),
        atr_mult_sl=eff.get("atr_mult_sl", 2.0),
        rr_target=eff.get("rr_target", 2.0),
        min_stop_pct=eff.get("min_stop_pct", 0.03),
    )
record["regime"] = regime.value
record["relative_strength"] = signals.get("relative_strength", 0.0)
record["levels"] = levels.as_dict() if levels else None
```

### 4.7 Confidence score — `scoring/score.py`

Tambah komponen `relative_strength` (informatif di dua regime). Rebalance bobot agar tetap
total 100 (tunable):

```python
_DEFAULT_WEIGHTS = {
    "done_ratio": 25, "rvol": 20, "close_in_range": 15,
    "broker_streak": 20, "queue_imbalance": 5, "ihsg": 5,
    "relative_strength": 10,   # baru
}
# tambah ke norm{}:
"relative_strength": _clamp01(s.get("relative_strength", 0.0) / 0.10),  # +10% outperf → 1.0
```

Update `score_weights` di `settings.yaml` sama persis. (Confidence cuma untuk ordering alert,
sekunder — boleh di-tune belakangan.)

### 4.8 Alert format v2 — `alert/telegram.py`

Format baru. Level **hanya** muncul untuk MARKUP_*; ACCUMULATION tampil "resis to watch";
DISTRIBUTION tampil warning tanpa entry.

**Sebelum:**
```
🚀 BBRI — MARKUP_START (90)
   done 0.80 · RVOL 3.0x · close 0.90 · broker streak 3
```

**Sesudah:**
```
🚀 VERN — MARKUP_START (conf 62) · BEARISH · RS +3.2%
   done 0.58 · RVOL 2.1x · close 0.78 · streak 4
   📍 Resis 158 · Support 138 · Close 152 · ATR 4.2
   🎯 Entry >158.8 · SL 153.4 (−3.4%) · TP 169.6 (R:R 2.0) · ~hold 8d
   <narasi>
```

`format_alert` perlu baca `it["levels"]` (dict) & `it["regime"]`/`it["relative_strength"]`.
Render baris 📍 & 🎯 **hanya jika `levels` ada**. Tetap pakai `parse_mode HTML` + escape.
Footer diganti: `Setup swing 10–20 hari (regime-aware). Entry = breakout terkonfirmasi, bukan harga sekarang. Kelola risiko sendiri.`

---

## 5. Backtest & validasi (WAJIB sebelum live)

### 5.1 Replay jadi regime-aware — `backtest/engine.py`

Per bar, resolve regime dari history IHSG **sampai tanggal itu**, pilih profil, merge, lalu classify:

```python
def replay(dataset, thresholds, windows, *, regime_profiles=None,
           top_n=5, horizon=20, warmup=20):   # default horizon 20, bukan 5
    ...
    for i in range(warmup, len(ohlcv)):
        ...
        regime = market_regime(ihsg_hist, windows.get("ihsg_ma", 50))
        prof = (regime_profiles or {}).get(regime.value, {})
        eff = {**thresholds, **prof}
        state = classify(signals, eff)
        ...
        rows.append({..., "regime": regime.value,
                     "relative_strength": round(signals.get("relative_strength", 0.0), 4)})
```

### 5.2 Pertanyaan yang harus dijawab backtest

1. **Tune rvol PER REGIME.** Pisahkan baris bullish vs bearish, grid-search `rvol_spike`
   tiap regime (pakai `fwd_close`, horizon 20). Tentukan angka final per profil.
2. **Ablation RS:** bandingkan hit-rate MARKUP di regime BEARISH **dengan vs tanpa** gate RS.
   RS hanya layak dipertahankan kalau **menaikkan** edge bersih. Kalau tidak, buang.
3. **Robustness across saham:** rvol optimal ngumpul di angka sama, atau berserak?
   Berserak → bukti kuat butuh regime (bukan satu threshold global).

### 5.3 Simulasi exit level (jawaban "take-profit exit") — `backtest/metrics.py`

Tambah mode opsional yang menilai strategi TP/SL, bukan cuma `fwd_close`:

```python
def simulate_exit(ohlcv, entry_idx, levels, horizon) -> dict:
    """Walk-forward dari entry_idx+1. KONSERVATIF: bila 1 bar menyentuh SL & TP,
    anggap SL kena dulu (tanpa data intraday → jangan optimis)."""
    e, sl, tp = levels.entry, levels.stop_loss, levels.take_profit
    end = min(entry_idx + 1 + horizon, len(ohlcv))
    for j in range(entry_idx + 1, end):
        bar = ohlcv.iloc[j]
        hit_sl = bar["low"] <= sl
        hit_tp = bar["high"] >= tp
        if hit_sl:                                  # SL diprioritaskan (konservatif)
            return {"exit": "SL", "bars": j - entry_idx, "ret": sl / e - 1}
        if hit_tp:
            return {"exit": "TP", "bars": j - entry_idx, "ret": tp / e - 1}
    last = ohlcv.iloc[end - 1]
    return {"exit": "TIMEOUT", "bars": end - 1 - entry_idx, "ret": last["close"] / e - 1}
```

**Wajib ada:**
- **Fill realistis:** trade dianggap *taken* HANYA bila ada bar sesudah sinyal yang `high > entry`
  (breakout beneran terjadi). Kalau tidak → "no fill", jangan dihitung.
- **NULL MODEL (non-negotiable):** jalankan `simulate_exit` yang sama pada **entry tanggal ACAK**
  (jumlah sama). Strategi level cuma punya edge kalau **mengalahkan random-entry** setelah ongkos.
  Kalau tidak ngalahin → tak ada edge level, titik.
- **Ongkos:** masukkan fee + slippage realistis. Lebih banyak trade = fee makin makan.

---

## 6. `settings.yaml` — blok baru

```yaml
# Window tambahan untuk RS & levels.
windows:
  volume_ma: 20
  ihsg_ma: 50
  broker_streak_lookback: 5
  rs_window: 20             # S10 relative strength
  donchian_lookback: 20     # resis/support untuk levels

# Profil regime: overlay thresholds yang dipilih IHSG vs MA50.
# rvol_spike & rs_min WAJIB di-tune ulang per regime (lihat spec §5). Angka di
# bawah = tebakan awal, BUKAN final.
regime_profiles:
  BULLISH:
    rvol_spike: 2.0
    require_relative_strength: false
    rs_min: 0.0
    atr_mult_sl: 2.0
    rr_target: 2.0
    risk_per_trade: 0.01      # metadata (engine tak eksekusi)
  BEARISH:
    rvol_spike: 2.5           # NAIK di market lemah, bukan turun
    require_relative_strength: true
    rs_min: 0.0               # >0 = harus outperform; 0 = minimal seimbang dgn IHSG
    atr_mult_sl: 1.8
    rr_target: 2.0
    risk_per_trade: 0.005     # potong setengah

# Param levels (default lintas regime; atr_mult_sl & rr_target diambil dari profil).
levels:
  breakout_buffer: 0.005      # 0.5% di atas resis = konfirmasi jebol
  atr_period: 14
  min_stop_pct: 0.03          # floor: SL tak pernah lebih ketat dari 3%
  hold_slack: 1.8             # faktor estimasi est_hold (harga tak gerak lurus)

# Confidence direbalance (total 100) — relative_strength ditambahkan.
score_weights:
  done_ratio: 25
  rvol: 20
  close_in_range: 15
  broker_streak: 20
  queue_imbalance: 5
  ihsg: 5
  relative_strength: 10
```

---

## 7. Testing requirements (DoD per modul)

Tambah ke `tests/`. Target: semua test lama tetap hijau + test baru.

- **`test_signals.py`**: `atr()` benar untuk seri sederhana; `donchian()` ambil max/min window;
  `relative_strength()` positif saat saham > index, negatif sebaliknya, 0.0 saat data kurang.
- **`test_levels.py` (baru):**
  - **`rr_realized` ≈ `rr_target` (±0.05)** — self-check anti-bug-label.
  - SL tak pernah lebih ketat dari `min_stop_pct` (uji ATR sangat kecil → floor aktif).
  - `est_hold_days > 0` & masuk akal (≈7–14 untuk input tipikal — **bukti ini bukan trade 1-minggu**).
  - data < lookback+1 → `None`.
- **`test_classifier.py`**: tambah —
  - BEARISH + `require_relative_strength=True` + saham underperform (rs<rs_min) → **NEUTRAL**
    (bukan MARKUP), walau done/rvol/cir/streak lolos.
  - BEARISH + outperform → MARKUP_START.
  - **Regresi backward-compat:** `classify(s)` tanpa profil (RS absent) → state sama seperti
    sebelum refactor (RS klausa no-op).
- **`test_backtest.py`**: replay dengan `regime_profiles` menghasilkan kolom `regime`;
  `simulate_exit` menghormati aturan **SL-first** saat satu bar menyentuh SL & TP.

---

## 8. Urutan eksekusi (phasing untuk Claude Code)

Kerjakan berurutan; tiap fase punya test yang harus hijau sebelum lanjut.

1. **F1 — Primitives.** `atr`, `donchian`, `relative_strength`, `market_regime`, `Regime`.
   + test_signals. (Tanpa nyentuh classifier.)
2. **F2 — Levels.** `signals/levels.py` + `compute_trade_levels` + test_levels (termasuk self-check R:R).
3. **F3 — Profiles & config.** Blok `regime_profiles` di YAML + `cfg.regime_profiles` + loader.
4. **F4 — Classifier RS gate.** Klausa RS opt-in + `relative_strength` di `compute_signals` +
   test_classifier (termasuk regresi backward-compat).
5. **F5 — Integrasi run_daily.** Resolve regime → profil → classify → levels → record.
6. **F6 — Alert v2.** `format_alert` render level + regime (hanya MARKUP_*).
7. **F7 — Backtest regime-aware.** replay + `simulate_exit` + null model.
8. **F8 — TUNE.** Jalankan §5: rvol per-regime, ablation RS, tentukan angka final di YAML.
   **Ini gerbang sebelum live.**

> **Commit terpisah:** fix data `done_client` swap + 2 test (yang ke-stage di branch) **bukan**
> bagian refactor ini. Kalau test-nya hijau, commit duluan — kerjaan beda, jangan nunggu.

---

## 9. Intraday: pakai `live_watch.py`, jangan paksa ke EOD signal

Sistem sudah PUNYA primitif intraday yang benar: `scripts/live_watch.py` — polling order-book
(S5) manual saat market buka, ping Telegram saat saham *flip* ke `DEMAND_DOMINAN`. Ini home yang
tepat untuk hasrat intraday: **manual, present-only, kuota-terbatas otomatis.** Sinyal swing
Wyckoff (10–20d) **tidak punya edge intraday** — backtest sudah buktikan. Jangan tempelkan
auto-alert intraday ke sinyal swing; itu cara tercepat bikin sistem yang *terlihat* kuat tapi noise.

**Enhancement aman yang disarankan (opsional):** buat `live_watch` membaca **watchlist dari hasil
EOD terakhir** (hanya pantau saham yang kemarin `MARKUP_START`/`ACCUMULATION_ONGOING`), sehingga
konfirmasi intraday **ter-anchor ke setup swing** — bukan sinyal intraday berdiri sendiri.

```python
# live_watch: ganti default codes dgn state actionable dari Store hari terakhir.
def watch_from_last_eod(store, fallback):
    rows = store.get_results(store.latest_date())   # tambah helper latest_date() di db.py
    codes = [r["code"] for r in rows
             if r["state"] in ("MARKUP_START", "ACCUMULATION_ONGOING")]
    return codes or fallback
```

> Kalau memang mau fast-trading beneran (≤1 minggu), kendaraannya **MT5 Agent**, bukan engine ini.

---

## 10. Yang TIDAK boleh dilakukan

- ❌ Menurunkan `rvol_spike` di regime BEARISH supaya sinyal lebih banyak.
- ❌ Menilai edge / tuning pakai `fwd_max`. Selalu `fwd_close`.
- ❌ Melabel R:R (mis. tulis "R:R 2" tanpa hitung). Selalu `rr_realized` dari level.
- ❌ Menampilkan entry/SL/TP untuk state non-MARKUP.
- ❌ Membalik arah done di `done_client.py`.
- ❌ Menargetkan horizon 5 hari / "max 1 minggu" sebagai parameter.
- ❌ Auto-alert intraday dari sinyal swing.
- ❌ Backtest exit yang menganggap TP kena duluan saat satu bar menyentuh SL & TP.
- ❌ Skip null model (random-entry) saat memvalidasi strategi level.
- ❌ Rename package `markup_radar`.

---

## Edge Cases & Considerations

- **Intrabar H/L ambiguity:** dalam 1 candle harian tak diketahui SL atau TP kena duluan →
  asumsi konservatif **SL-first** (sudah di `simulate_exit`). Idealnya pakai `intraday_chart`
  untuk presisi; untuk v2 cukup asumsi konservatif.
- **Gap-through stop:** IDX (terutama gocap watchlist `DATA/JARR/VERN/ATAP/ESIP`) sering gap.
  SL "di X" tak menjamin eksekusi di X. Slippage harus masuk hitungan risk. (Engine tak eksekusi,
  jadi ini disclaimer di alert.)
- **Tick-size rounding:** entry/SL/TP idealnya dibulatkan ke fraksi harga IDX yang valid
  (Rp1/Rp2/Rp5/… per pita harga). Untuk v2 boleh ditunda; flag sebagai TODO di `levels.py`.
- **False breakout:** `breakout_buffer` 0.5% menyaring jebol-semu tapi tak cukup. Pertimbangkan
  syarat **close di atas resis** (bukan cuma touch) atau konfirmasi volume di bar breakout —
  uji di backtest sebelum mengetatkan.
- **RS alignment:** `relative_strength` pakai window posisional (bukan date-join). Akurat cukup
  untuk 20d EOD; bila butuh presisi (mis. ada banyak hari libur tak sinkron), refactor untuk
  passing IHSG sebagai frame date-indexed ke `compute_signals`.
- **Stop terlalu lebar di gocap ber-ATR besar:** ATR besar → `stop_dist` besar → posisi kecil &
  `est_hold` panjang. v2 menanganinya lewat transparansi (`stop_pct`, `est_hold_days` di alert),
  bukan cap keras. Tambah `max_stop_pct` opsional bila perlu.
- **Regime fail-safe:** IHSG kosong/gagal fetch → `market_regime` balik `BEARISH` (profil ketat),
  bukan BULLISH. Lebih aman salah ke arah konservatif.
