"""Normalisasi --codes (override watchlist) di run_daily + plumbing config regime."""

import pytest

from markup_radar.config import load_codes_file, load_settings, parse_codes


@pytest.mark.parametrize(
    "tokens,expected",
    [
        (["BBCA", "BBRI"], ["BBCA", "BBRI"]),          # space-separated
        (["BBCA,BBRI,BMRI"], ["BBCA", "BBRI", "BMRI"]),  # comma-separated
        (["BBCA, BBRI"], ["BBCA", "BBRI"]),            # koma + spasi campur
        (["bbca", "bbri"], ["BBCA", "BBRI"]),          # uppercase
        (["BBCA", "bbca", "BBRI"], ["BBCA", "BBRI"]),  # dedup, jaga urutan
        ([","], []),                                    # token sampah
        ([], []),                                        # tidak ada kode
    ],
)
def test_parse_codes(tokens, expected):
    assert parse_codes(tokens) == expected


def test_load_codes_file(tmp_path):
    f = tmp_path / "watchlist_today.txt"
    f.write_text(
        "# komentar header\n"
        "BBCA\n"
        "bbri, BMRI   # campur koma/spasi + komentar inline\n"
        "\n"                 # baris kosong
        "BBCA\n"             # duplikat -> di-dedup
        "   \n",
        encoding="utf-8",
    )
    assert load_codes_file(f) == ["BBCA", "BBRI", "BMRI"]


def test_load_codes_file_missing(tmp_path):
    with pytest.raises(OSError):
        load_codes_file(tmp_path / "tidak_ada.txt")


# ---- F3: profil regime + param levels dari settings.yaml ----
@pytest.fixture(scope="module")
def cfg():
    return load_settings()


def test_regime_profiles_has_both_regimes(cfg):
    prof = cfg.regime_profiles
    assert set(prof) == {"BULLISH", "BEARISH"}


def test_bearish_profile_is_stricter_than_bullish(cfg):
    bull = cfg.regime_profiles["BULLISH"]
    bear = cfg.regime_profiles["BEARISH"]
    # Market lemah -> palang DINAIKKAN (rvol lebih tinggi + wajib RS), risk dipotong.
    assert bear["rvol_spike"] > bull["rvol_spike"]
    assert bear["require_relative_strength"] is True
    assert bull["require_relative_strength"] is False
    assert bear["risk_per_trade"] < bull["risk_per_trade"]


def test_overlay_merge_picks_profile_threshold(cfg):
    # Cara pakai per spec §4.5: profil meng-override threshold dasar.
    eff = {**cfg.thresholds, **cfg.regime_profiles["BEARISH"]}
    assert eff["rvol_spike"] == cfg.regime_profiles["BEARISH"]["rvol_spike"]
    assert eff["done_ratio_markup"] == cfg.thresholds["done_ratio_markup"]  # dasar tetap


def test_levels_params_present(cfg):
    lv = cfg.levels
    assert lv["min_stop_pct"] == 0.03   # floor anti-whipsaw (spec D4)
    assert lv["breakout_buffer"] == 0.005
    assert "atr_period" in lv and "hold_slack" in lv


def test_new_windows_keys_present(cfg):
    w = cfg.windows
    assert w["rs_window"] == 20
    assert w["donchian_lookback"] == 20


def test_regime_profiles_empty_when_absent():
    # Property aman saat key tak ada (backward-compat config lama).
    from markup_radar.config import Settings

    assert Settings(raw={}).regime_profiles == {}
    assert Settings(raw={}).levels == {}
