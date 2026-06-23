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


def test_accumulation_has_no_levels(cfg):
    data = make_snapshot("accumulation", "DEMO")
    state, _, levels = run_daily.evaluate(
        data, _signals(cfg, data), cfg, _eff(cfg, Regime.BULLISH))
    assert state == "ACCUMULATION_ONGOING"
    assert levels is None


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
