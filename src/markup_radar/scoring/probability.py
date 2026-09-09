"""Skor peluang entry — untuk MENGURUTKAN alert satu malam, bukan memvonis.

Masalah yang dipecahkan: satu malam bisa keluar 8-10 sinyal dan user tak mungkin
masuk semuanya. Butuh urutan "mana yang paling layak dimasuki duluan".

KENAPA BUKAN `confidence_markup_start` YANG SUDAH ADA: skor itu bobotnya
ditentukan manual dan tak pernah divalidasi. Diuji atas 391 alert produksi
(18 Jun - 8 Sep 2026) ternyata **TERBALIK** — AUC 0.439, di BAWAH 0.50:

    confidence   <40    40-49   50-59   >=60
    menang r10   64%     58%     48%     44%

Sebabnya masuk akal: `confidence` memberi nilai tinggi pada "kekuatan" (rvol
tinggi, close di puncak, done_ratio tinggi) — dan justru itu kondisi gagalnya.
`confidence` tetap dipakai untuk hal lain (ordering lama, prioritas enrichment),
TAPI jangan dipakai memilih entry.

DASAR EMPIRIS (target: sentuh +5% sebelum -5% dalam 10 bar dari open H+1, yaitu
skenario trading nyata; base rate 57%, n=391):

    fitur                 bucket        n    menang   lift
    close_in_range        < 0.40       67      76%    +20%
    done_ratio            0.50-0.60    68      75%    +18%
    rvol                  < 1.0x       95      74%    +17%
    prior_run             turun        71      72%    +15%
    range_position        0.30-0.60    89      71%    +14%
    broker streak         >= 3        253    60-65%   +4..9%
    ---------------------------------- yang MENURUNKAN -----------------------
    range_position        > 0.85      166      47%    -10%
    prior_run             > +15%       64      45%    -11%
    rvol                  2-5x        136      49%     -7%
    close_in_range        > 0.70      218      49%     -8%
    broker streak         0-2         138    46-47%   -11..-9%

Polanya koheren dan agak ironis untuk engine bernama "Markup Radar": skor
tertinggi justru pada saham yang TENANG (volume sepi, close lemah, belum lari,
di tengah range) tapi brokernya diam-diam mengumpulkan. Yang ramai — volume
meledak, close di puncak, sudah lari — justru yang gagal.

KALIBRASI (n=356 alert sisi beli; DISTRIBUTION_WARNING tak diskor):

    skor      n     menang    latih(Jun-Jul) / uji(Agu-Sep)
    >= 75    87       78%          81%  /  77%      <- satu-satunya band terbukti
    55-74    53       53%          58%  /  48%      <- TIDAK stabil, = lempar koin
    <  55   216       50%          48%  /  50%

Simulasi ambil TOP-N per malam: top-1 menang 78% (median r10 +3.5%), top-2 74%
(+3.3%), top-3 70% (+2.6%), versus ambil SEMUA 57% (+1.1%).

JUJUR SOAL BATASNYA: satu periode 3 bulan, 88% regime BULLISH, AUC out-of-sample
0.638 (lumayan, bukan hebat). Yang terbukti cuma "skor >=75 lebih baik dari
sisanya" — di bawah 75 jangan diperlakukan sebagai gradasi bermakna. Ambang
sengaja kasar (kelipatan 6-15) supaya tak menempel ke noise; JANGAN dihaluskan
tanpa data forward baru.
"""

from __future__ import annotations

__all__ = ["entry_score", "score_band", "rank_alerts", "BANDS"]

# (skor minimum, label, hit-rate historis, n) — urut dari tertinggi.
BANDS: list[tuple[int, str, float, int]] = [
    (75, "TINGGI", 0.78, 87),
    (55, "SEDANG", 0.53, 53),
    (0, "RENDAH", 0.50, 216),
]

# State yang bermakna untuk diskor. DISTRIBUTION_WARNING sengaja di luar: itu
# peringatan JUAL, "peluang entry"-nya tak punya arti.
SCORABLE = ("MARKUP_CONFIRMED", "MARKUP_START", "ACCUMULATION_ONGOING")


def entry_score(signals: dict) -> int:
    """Poin 0-100. Makin tinggi = makin sering menang secara historis.

    Butuh `range_position` & `prior_run` (S12/S13). Sinyal yang tak ada
    diperlakukan netral — data hilang tak boleh diam-diam menaikkan ATAU
    menurunkan peringkat.
    """
    p = 50

    pos = signals.get("range_position")
    if pos is not None:
        pos = float(pos)
        p += 15 if pos < 0.60 else (0 if pos < 0.85 else -15)

    run = signals.get("prior_run")
    if run is not None:
        run = float(run)
        p += 12 if run < 0 else (0 if run < 0.05 else (-6 if run < 0.15 else -12))

    rvol = signals.get("rvol")
    if rvol is not None:
        rvol = float(rvol)
        # Bentuknya U: sepi (<1x) = akumulasi senyap, terbaik. 2-5x = zona
        # ambang engine sendiri, justru terburuk. >5x = kejadian nyata, netral.
        p += 12 if rvol < 1.0 else (-8 if rvol < 5.0 else 0)

    cir = signals.get("close_in_range")
    if cir is not None:
        cir = float(cir)
        p += 12 if cir < 0.40 else (0 if cir < 0.70 else -10)

    streak = signals.get("broker_net_buy_streak")
    if streak is not None:
        p += 8 if int(streak) >= 3 else -8

    dr = signals.get("done_ratio")
    if dr is not None and 0.50 <= float(dr) < 0.60:
        p += 8

    return max(0, min(100, p))


def score_band(score: int) -> tuple[str, float, int]:
    """(label, hit-rate historis, n) untuk satu skor."""
    for lo, label, rate, n in BANDS:
        if score >= lo:
            return label, rate, n
    return BANDS[-1][1], BANDS[-1][2], BANDS[-1][3]


def rank_alerts(records: list[dict]) -> list[dict]:
    """Urutkan alert satu malam dari peluang tertinggi & beri nomor peringkat.

    Menulis di tempat: `entry_score`, `score_band`, `score_hit_rate`, `rank`
    (1 = paling layak dimasuki). Record yang tak bisa diskor
    (DISTRIBUTION_WARNING) dapat `entry_score=None`, tak diberi peringkat, dan
    ditaruh di akhir — supaya peringatan jual tak pernah terbaca sebagai
    rekomendasi beli nomor sekian.
    """
    for r in records:
        if r.get("state") in SCORABLE:
            s = entry_score(r.get("signals") or {})
            label, rate, _ = score_band(s)
            r["entry_score"] = s
            r["score_band"] = label
            r["score_hit_rate"] = rate
        else:
            r["entry_score"] = None
            r["score_band"] = None
            r["score_hit_rate"] = None
        r["rank"] = None

    scorable = [r for r in records if r.get("entry_score") is not None]
    others = [r for r in records if r.get("entry_score") is None]
    # Tie-break: skor, lalu range_position terendah (paling jauh dari puncak).
    scorable.sort(key=lambda r: (-r["entry_score"],
                                 float((r.get("signals") or {}).get("range_position") or 1.0)))
    for i, r in enumerate(scorable, 1):
        r["rank"] = i
    return scorable + others
