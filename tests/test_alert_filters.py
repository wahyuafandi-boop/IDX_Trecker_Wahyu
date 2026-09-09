"""Gate alert: posisi range, prior-run, streak broker, dedup episode."""

from __future__ import annotations

import pandas as pd
import pytest

from markup_radar.alert.filters import alert_gate, apply_alert_filters
from markup_radar.signals.price_volume import prior_run, range_position


def rec(state="ACCUMULATION_ONGOING", code="AAAA", **signals):
    base = {"range_position": 0.5, "prior_run": 0.0, "broker_net_buy_streak": 3}
    return {"code": code, "state": state, "signals": {**base, **signals}}


# --- gate posisi range ------------------------------------------------------

def test_puncak_range_ditolak():
    assert alert_gate(rec(range_position=0.92)) == "puncak_range(0.92)"


def test_zona_tengah_lolos():
    assert alert_gate(rec(range_position=0.45)) is None


def test_tepat_di_ambang_lolos():
    """Ambang inklusif: 0.85 lolos, di ATAS-nya baru ditolak."""
    assert alert_gate(rec(range_position=0.85)) is None
    assert alert_gate(rec(range_position=0.851)) is not None


# --- gate prior run ---------------------------------------------------------

def test_sudah_lari_ditolak():
    assert alert_gate(rec(prior_run=0.22)) == "sudah_lari(+22%)"


def test_lari_wajar_lolos():
    assert alert_gate(rec(prior_run=0.08)) is None


def test_harga_turun_lolos():
    assert alert_gate(rec(prior_run=-0.10)) is None


# --- gate streak broker -----------------------------------------------------

def test_streak_nol_ditolak():
    assert alert_gate(rec(broker_net_buy_streak=0)) == "streak_broker(0)"


# --- DISTRIBUSI dikecualikan dari gate sisi beli ----------------------------

def test_distribution_warning_bebas_gate_beli():
    """DISTRIBUTION_WARNING per definisi dekat puncak range (near_range_high di
    classifier) — kalau kena gate beli, peringatan jual jadi bisu."""
    r = rec(state="DISTRIBUTION_WARNING", range_position=0.98,
            prior_run=0.40, broker_net_buy_streak=0)
    assert alert_gate(r) is None


def test_distribution_tetap_kena_dedup():
    r = rec(state="DISTRIBUTION_WARNING", range_position=0.98)
    assert alert_gate(r, last_alert_date="2026-09-05",
                      scan_date="2026-09-08") == "episode_ulang(3h)"


# --- dedup episode ----------------------------------------------------------

def test_alert_ulang_dalam_jendela_ditahan():
    assert alert_gate(rec(), last_alert_date="2026-09-01",
                      scan_date="2026-09-08") == "episode_ulang(7h)"


def test_episode_baru_setelah_jeda_lolos():
    assert alert_gate(rec(), last_alert_date="2026-08-20",
                      scan_date="2026-09-08") is None


def test_tepat_di_batas_jeda():
    assert alert_gate(rec(), last_alert_date="2026-08-29",
                      scan_date="2026-09-08") == "episode_ulang(10h)"
    assert alert_gate(rec(), last_alert_date="2026-08-28",
                      scan_date="2026-09-08") is None


def test_belum_pernah_alert_lolos():
    assert alert_gate(rec(), last_alert_date=None, scan_date="2026-09-08") is None


def test_tanggal_rusak_tak_menggagalkan():
    assert alert_gate(rec(), last_alert_date="bukan-tanggal",
                      scan_date="2026-09-08") is None


# --- kill switch & data hilang ---------------------------------------------

def test_disabled_meloloskan_semua():
    r = rec(range_position=1.0, prior_run=0.9, broker_net_buy_streak=0)
    assert alert_gate(r, filters={"enabled": False},
                      last_alert_date="2026-09-07", scan_date="2026-09-08") is None


def test_sinyal_hilang_tidak_menolak():
    """Data cacat/None jangan diam-diam membungkam alert."""
    r = {"code": "AAAA", "state": "MARKUP_START", "signals": {}}
    assert alert_gate(r) is None


def test_ambang_bisa_dimatikan_satuan():
    r = rec(range_position=0.99)
    assert alert_gate(r, filters={"max_range_position": None}) is None


# --- apply_alert_filters ----------------------------------------------------

def test_apply_membagi_dan_menandai():
    recs = [
        rec(code="LOLOS", range_position=0.4),
        rec(code="PUNCAK", range_position=0.95),
        rec(code="ULANG", range_position=0.4),
    ]
    keep, drop = apply_alert_filters(
        recs, last_alert_dates={"ULANG": "2026-09-04"}, scan_date="2026-09-08"
    )
    assert [r["code"] for r in keep] == ["LOLOS"]
    assert {r["code"] for r in drop} == {"PUNCAK", "ULANG"}
    assert keep[0]["suppressed"] is None
    assert all(r["suppressed"] for r in drop)


def test_apply_urutan_dipertahankan():
    recs = [rec(code=c, range_position=0.4) for c in ("AAA", "BBB", "CCC")]
    keep, drop = apply_alert_filters(recs, scan_date="2026-09-08")
    assert [r["code"] for r in keep] == ["AAA", "BBB", "CCC"]
    assert drop == []


# --- primitif sinyal --------------------------------------------------------

def test_range_position_hitungan():
    high = pd.Series([100.0] * 19 + [120.0])
    low = pd.Series([80.0] * 20)
    assert range_position(high, low, 80.0, 20) == pytest.approx(0.0)
    assert range_position(high, low, 120.0, 20) == pytest.approx(1.0)
    assert range_position(high, low, 100.0, 20) == pytest.approx(0.5)


def test_range_position_range_datar_netral():
    flat = pd.Series([50.0] * 20)
    assert range_position(flat, flat, 50.0, 20) == 0.5


def test_range_position_hormati_lookback():
    """Hanya `lookback` bar terakhir yang dihitung."""
    high = pd.Series([500.0] + [100.0] * 20)
    low = pd.Series([10.0] + [80.0] * 20)
    assert range_position(high, low, 100.0, 20) == pytest.approx(1.0)


def test_prior_run_hitungan():
    closes = pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110.0])
    assert prior_run(closes, 10) == pytest.approx(0.10)


def test_prior_run_data_kurang_nol():
    assert prior_run(pd.Series([100.0, 110.0]), 10) == 0.0
