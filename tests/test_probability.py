"""Skor peluang entry & pengurutan alert (scoring/probability.py)."""

from __future__ import annotations

from markup_radar.alert.telegram import format_batch_header, format_signal
from markup_radar.scoring.probability import (
    entry_score,
    rank_alerts,
    score_band,
)


def sig(**kw):
    """Sinyal netral (skor 50) yang bisa digeser per-fitur."""
    base = {"range_position": 0.70, "prior_run": 0.02, "rvol": 1.5,
            "close_in_range": 0.50, "broker_net_buy_streak": 1, "done_ratio": 0.45}
    return {**base, **kw}


def rec(code="AAAA", state="ACCUMULATION_ONGOING", **kw):
    return {"code": code, "state": state, "signals": sig(**kw)}


# --- arah tiap fitur (harus sesuai bukti audit) ----------------------------

def test_posisi_tengah_lebih_tinggi_dari_puncak():
    assert entry_score(sig(range_position=0.45)) > entry_score(sig(range_position=0.95))


def test_sudah_lari_menurunkan_skor():
    assert entry_score(sig(prior_run=-0.03)) > entry_score(sig(prior_run=0.25))


def test_volume_sepi_lebih_tinggi_dari_ramai():
    """Kontra-intuitif tapi ini yang ditemukan data: rvol<1 menang 74%, 2-5x 49%."""
    assert entry_score(sig(rvol=0.6)) > entry_score(sig(rvol=3.0))


def test_close_lemah_lebih_tinggi_dari_close_puncak():
    assert entry_score(sig(close_in_range=0.25)) > entry_score(sig(close_in_range=0.90))


def test_streak_broker_menaikkan():
    assert entry_score(sig(broker_net_buy_streak=4)) > entry_score(sig(broker_net_buy_streak=0))


def test_setup_terbaik_masuk_band_tinggi():
    s = entry_score(sig(range_position=0.40, prior_run=-0.02, rvol=0.7,
                        close_in_range=0.30, broker_net_buy_streak=4, done_ratio=0.55))
    assert s >= 75 and score_band(s)[0] == "TINGGI"


def test_setup_terburuk_masuk_band_rendah():
    s = entry_score(sig(range_position=0.97, prior_run=0.30, rvol=3.0,
                        close_in_range=0.95, broker_net_buy_streak=0, done_ratio=0.35))
    assert s < 55 and score_band(s)[0] == "RENDAH"


# --- ketahanan terhadap data hilang ----------------------------------------

def test_sinyal_kosong_netral():
    """Data hilang tak boleh diam-diam menaikkan ATAU menurunkan peringkat."""
    assert entry_score({}) == 50


def test_skor_selalu_dalam_rentang():
    for s in ({}, sig(), sig(range_position=5.0, prior_run=9.0, rvol=-1.0)):
        assert 0 <= entry_score(s) <= 100


# --- pengurutan -------------------------------------------------------------

def test_rank_urut_dari_skor_tertinggi():
    recs = [
        rec("BURUK", range_position=0.97, close_in_range=0.95, broker_net_buy_streak=0),
        rec("BAGUS", range_position=0.40, prior_run=-0.02, rvol=0.7,
            close_in_range=0.30, broker_net_buy_streak=4, done_ratio=0.55),
        rec("TENGAH"),
    ]
    out = rank_alerts(recs)
    assert [r["code"] for r in out] == ["BAGUS", "TENGAH", "BURUK"]
    assert [r["rank"] for r in out] == [1, 2, 3]


def test_distribution_tidak_diskor_dan_ditaruh_akhir():
    """Peringatan jual tak boleh terbaca sebagai rekomendasi beli nomor sekian."""
    recs = [rec("JUAL", state="DISTRIBUTION_WARNING"), rec("BELI")]
    out = rank_alerts(recs)
    assert out[-1]["code"] == "JUAL"
    assert out[-1]["entry_score"] is None and out[-1]["rank"] is None
    assert out[0]["rank"] == 1


def test_tie_break_pakai_posisi_range():
    """Skor sama (bucket pos20 yang sama) -> yang lebih dekat dasar menang."""
    a = rec("ATAS_BUCKET", range_position=0.55)
    b = rec("BAWAH_BUCKET", range_position=0.20)
    assert entry_score(a["signals"]) == entry_score(b["signals"])   # sama-sama <0.60
    assert [r["code"] for r in rank_alerts([a, b])][0] == "BAWAH_BUCKET"


def test_rank_daftar_kosong():
    assert rank_alerts([]) == []


# --- tampilan Telegram ------------------------------------------------------

def test_header_menampilkan_urutan_prioritas():
    out = rank_alerts([
        rec("BAGUS", range_position=0.40, prior_run=-0.02, rvol=0.7,
            close_in_range=0.30, broker_net_buy_streak=4, done_ratio=0.55),
        rec("BURUK", range_position=0.97, close_in_range=0.95, broker_net_buy_streak=0),
    ])
    msg = format_batch_header("2026-09-09", out)
    assert "Urutan prioritas entry" in msg
    assert msg.index("BAGUS") < msg.index("BURUK")
    assert "⭐" in msg and "TINGGI" in msg


def test_header_memperingatkan_saat_tak_ada_band_tinggi():
    out = rank_alerts([rec("BURUK", range_position=0.97, close_in_range=0.95,
                           broker_net_buy_streak=0)])
    assert "Tak ada yang masuk band TINGGI" in format_batch_header("2026-09-09", out)


def test_header_pisahkan_peringatan_jual():
    out = rank_alerts([rec("JUAL", state="DISTRIBUTION_WARNING"), rec("BELI")])
    assert "Peringatan jual (tak diskor): JUAL" in format_batch_header("2026-09-09", out)


def test_format_signal_menampilkan_peringkat_bukan_confidence():
    out = rank_alerts([rec("BAGUS", range_position=0.40, prior_run=-0.02, rvol=0.7,
                           close_in_range=0.30, broker_net_buy_streak=4, done_ratio=0.55)])
    out[0]["confidence"] = 88
    msg = format_signal("2026-09-09", out[0])
    assert "Prioritas #1" in msg and "TINGGI" in msg
    assert "Kekuatan sinyal" not in msg      # confidence sengaja tak ditampilkan
