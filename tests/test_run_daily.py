"""F5: integrasi run_daily — regime → eff → classify → levels (helper `evaluate`).

Test offline pakai snapshot demo (tanpa network/Invezgo). Fokus: levels HANYA
muncul untuk MARKUP_*, dan profil regime (atr_mult_sl/RS-gate) benar-benar dipakai.
"""

import importlib
import sys
from pathlib import Path

import pytest

from markup_radar.config import load_settings
from markup_radar.demo import make_snapshot
from markup_radar.signals import compute_signals
from markup_radar.signals.levels import TradeLevels
from markup_radar.signals.market import Regime

# `evaluate` ada di scripts/run_daily.py (entrypoint, bukan package).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
run_daily = importlib.import_module("run_daily")


@pytest.fixture(scope="module")
def cfg():
    return load_settings()


def _signals(cfg, data):
    return compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)


def _eff(cfg, regime: Regime) -> dict:
    return {**cfg.thresholds, **cfg.regime_profiles.get(regime.value, {})}


def test_markup_state_gets_levels(cfg):
    # Snapshot markup di BULLISH (RS tak diwajibkan) → MARKUP_* + levels terbit.
    data = make_snapshot("markup", "DEMO")
    state, conf, levels = run_daily.evaluate(
        data, _signals(cfg, data), cfg, _eff(cfg, Regime.BULLISH))
    assert state in ("MARKUP_START", "MARKUP_CONFIRMED")
    assert isinstance(levels, TradeLevels)
    assert abs(levels.rr_realized - 2.0) <= 0.05    # rr_target BULLISH 2.0


def test_non_markup_has_no_levels(cfg):
    # State non-MARKUP (NEUTRAL) → levels None (spec D5).
    data = make_snapshot("neutral", "DEMO")
    state, conf, levels = run_daily.evaluate(
        data, _signals(cfg, data), cfg, _eff(cfg, Regime.BULLISH))
    assert state == "NEUTRAL"
    assert levels is None


def test_accumulation_gets_watch_levels(cfg):
    # Sejak 2026-07-10 state pantau juga dapat levels (panduan BOB/BOW di alert;
    # entry tetap bersyarat breakout — bukan sinyal masuk).
    data = make_snapshot("accumulation", "DEMO")
    state, _, levels = run_daily.evaluate(
        data, _signals(cfg, data), cfg, _eff(cfg, Regime.BULLISH))
    assert state == "ACCUMULATION_ONGOING"
    assert isinstance(levels, TradeLevels)
    assert levels.entry > levels.resistance    # entry = breakout di atas resistance


def test_bearish_profile_tightens_stop(cfg):
    # Profil BEARISH pakai atr_mult_sl=1.8 (< BULLISH 2.0) → SL lebih ketat untuk
    # saham & ATR yang sama (asal floor 3% tak aktif). Paksa lolos RS-gate BEARISH.
    data = make_snapshot("markup", "DEMO")
    signals = _signals(cfg, data)
    signals["relative_strength"] = 0.05    # outperform → lolos gate require_rs
    _, _, lv_bear = run_daily.evaluate(data, signals, cfg, _eff(cfg, Regime.BEARISH))
    _, _, lv_bull = run_daily.evaluate(data, signals, cfg, _eff(cfg, Regime.BULLISH))
    assert lv_bear is not None and lv_bull is not None
    assert lv_bear.stop_pct <= lv_bull.stop_pct


def test_bearish_blocks_markup_when_underperform(cfg):
    # Snapshot markup tapi underperform IHSG (RS negatif) di BEARISH → RS-gate
    # memblok → bukan MARKUP → tak ada levels.
    data = make_snapshot("markup", "DEMO")
    signals = _signals(cfg, data)
    signals["relative_strength"] = -0.05
    state, _, levels = run_daily.evaluate(data, signals, cfg, _eff(cfg, Regime.BEARISH))
    assert state not in ("MARKUP_START", "MARKUP_CONFIRMED")
    assert levels is None


# ---- BOW-AC: _write_live_codes (tier + sidecar live_levels.json) ----
from markup_radar.config import Settings


def _rec(code, state, conf, levels=None):
    return {"code": code, "state": state, "confidence": conf, "levels": levels}


_LV = {"resistance": 158.0, "support": 138.0, "atr": 4.2, "entry": 158.79,
       "stop_loss": 151.2, "take_profit": 173.97, "rr_realized": 2.0,
       "stop_pct": 0.0478, "est_hold_days": 8}


def test_write_live_codes_tier_priority_and_sidecar(tmp_path):
    import json as _json
    cfg2 = Settings(raw={"live_watch": {
        "max_codes": 3,
        "include_states": ["MARKUP_CONFIRMED", "MARKUP_START", "ACCUMULATION_ONGOING"],
        "out_file": str(tmp_path / "live_today.txt"),
        "levels_file": str(tmp_path / "live_levels.json"),
    }}, db_path=str(tmp_path / "x.db"))
    actionable = [
        _rec("AKUM1", "ACCUMULATION_ONGOING", 90, _LV),   # conf tinggi tapi tier bawah
        _rec("MKUP1", "MARKUP_START", 50, _LV),
        _rec("MKUP2", "MARKUP_CONFIRMED", 40, _LV),
        _rec("AKUM2", "ACCUMULATION_ONGOING", 30, _LV),   # kalah slot (cap 3)
    ]
    codes = run_daily._write_live_codes(actionable, cfg2, dry_run=False)
    # MARKUP dulu (CONFIRMED > START) baru ACCUM meski conf ACCUM lebih tinggi.
    assert codes == ["MKUP2", "MKUP1", "AKUM1"]
    sidecar = _json.loads((tmp_path / "live_levels.json").read_text())
    assert set(sidecar) == {"MKUP2", "MKUP1", "AKUM1"}
    assert sidecar["AKUM1"]["state"] == "ACCUMULATION_ONGOING"
    assert sidecar["AKUM1"]["support"] == 138.0
    assert sidecar["AKUM1"]["bow_hi"] == 140.1          # support + 0.5*ATR
    assert sidecar["MKUP2"]["entry"] == 158.79


def test_write_live_codes_empty_writes_empty_sidecar(tmp_path):
    import json as _json
    cfg2 = Settings(raw={"live_watch": {
        "out_file": str(tmp_path / "live_today.txt"),
        "levels_file": str(tmp_path / "live_levels.json"),
    }}, db_path=str(tmp_path / "x.db"))
    assert run_daily._write_live_codes([], cfg2, dry_run=False) == []
    assert _json.loads((tmp_path / "live_levels.json").read_text()) == {}


# ---- BOW-AC: keputusan per-siklus (_bow_check di live_watch) ----
live_watch = importlib.import_module("live_watch")

_BOWLV = {"state": "ACCUMULATION_ONGOING", "support": 138.0,
          "bow_lo": 138.0, "bow_hi": 140.1}


def test_bow_check_ac_needs_two_cycles_in_zone_and_bid_held():
    # Siklus pertama di zona -> belum AC (baru tandai in_zone).
    ev, in_zone = live_watch._bow_check(_BOWLV, 139.0, 5000, {}, 0.9)
    assert ev is None and in_zone
    # Siklus kedua masih di zona + bid dijaga (>= 90%) -> AC.
    prev = {"bow_in_zone": True, "bid_lot": 5000}
    ev, _ = live_watch._bow_check(_BOWLV, 139.5, 4700, prev, 0.9)
    assert ev == "AC"
    # Bid terkuras (< 90% siklus lalu) -> belum konfirmasi.
    ev, _ = live_watch._bow_check(_BOWLV, 139.5, 3000, prev, 0.9)
    assert ev is None


def test_bow_check_invalid_below_support():
    ev, in_zone = live_watch._bow_check(_BOWLV, 137.0, 5000, {"bow_in_zone": True}, 0.9)
    assert ev == "INVALID" and not in_zone


def test_bow_check_none_when_no_data():
    assert live_watch._bow_check({}, 139.0, 5000, {}, 0.9) == (None, False)
    assert live_watch._bow_check(_BOWLV, 0.0, 5000, {}, 0.9) == (None, False)
    # Di atas zona (harga sudah lari) -> bukan BOW.
    ev, in_zone = live_watch._bow_check(_BOWLV, 150.0, 5000,
                                        {"bow_in_zone": True, "bid_lot": 5000}, 0.9)
    assert ev is None and not in_zone


# --- Gate alert terintegrasi (2026-09-09) ----------------------------------

def test_compute_signals_menghasilkan_konteks_timing(cfg):
    """range_position & prior_run ikut dihitung untuk SEMUA kode (bahan evaluasi)."""
    data = make_snapshot("markup", "DEMO")
    sig = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
    assert 0.0 <= sig["range_position"] <= 1.0
    assert isinstance(sig["prior_run"], float)


def test_gate_alert_memotong_kirim_bukan_klasifikasi(cfg):
    """Sinyal di puncak range tetap DIKLASIFIKASI & tersimpan, tapi tak dikirim."""
    from markup_radar.alert import apply_alert_filters

    data = make_snapshot("markup", "DEMO")
    sig = compute_signals(data, cfg.thresholds, cfg.windows, cfg.broker_top_n)
    state, conf, levels = run_daily.evaluate(data, sig, cfg, _eff(cfg, Regime.BULLISH))

    puncak = {"code": "AAAA", "state": "ACCUMULATION_ONGOING",
              "signals": {**sig, "range_position": 0.97, "prior_run": 0.02,
                          "broker_net_buy_streak": 3}}
    tengah = {"code": "BBBB", "state": "ACCUMULATION_ONGOING",
              "signals": {**sig, "range_position": 0.45, "prior_run": 0.02,
                          "broker_net_buy_streak": 3}}
    keep, held = apply_alert_filters([puncak, tengah], filters=cfg.alert_filters,
                                     scan_date="2026-09-08")
    assert [r["code"] for r in keep] == ["BBBB"]
    assert held[0]["suppressed"].startswith("puncak_range")
    # state & confidence tetap dihitung apa adanya — gate tak menyentuhnya
    assert state and isinstance(conf, int)


def test_alert_filters_config_terbaca(cfg):
    f = cfg.alert_filters
    assert f["enabled"] is True
    assert f["max_range_position"] == 0.85
    assert f["episode_gap_days"] == 10
    assert "DISTRIBUTION_WARNING" not in f["buy_side_states"]
