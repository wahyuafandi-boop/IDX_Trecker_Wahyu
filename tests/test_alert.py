"""Test format_alert: HTML mode aman untuk state ber-underscore."""

from markup_radar.alert import format_alert
from markup_radar.alert.telegram import _why_bullets, format_signal

ITEMS = [
    {"code": "BBRI", "state": "MARKUP_START", "confidence": 90,
     "signals": {"done_ratio": 0.8, "rvol": 3.0, "close_in_range": 0.9, "broker_net_buy_streak": 3}},
]


def test_uses_html_bold_not_markdown():
    out = format_alert("2026-06-17", ITEMS)
    assert "<b>Markup Radar</b>" in out
    assert "*" not in out                       # tidak ada sisa Markdown


def test_state_underscore_preserved():
    out = format_alert("2026-06-17", ITEMS)
    assert "MARKUP_START" in out                # underscore tetap utuh (aman di HTML)


def test_narrative_escaped_and_italic():
    items = [{**ITEMS[0], "narrative": "buyer ambil alih <kuat>"}]
    out = format_alert("2026-06-17", items)
    assert "<i>" in out
    assert "&lt;kuat&gt;" in out                # karakter HTML di-escape


def test_empty_items_message():
    assert "Tidak ada sinyal" in format_alert("2026-06-17", [])


# ---- F6: Alert v2 (level + regime tag + RS, hanya MARKUP_*) ----
_LEVELS = {
    "resistance": 158.0, "support": 138.0, "atr": 4.2,
    "entry": 158.79, "stop_loss": 151.2, "take_profit": 173.97,
    "rr_realized": 2.0, "stop_pct": 0.0478, "est_hold_days": 8,
}
MARKUP_V2 = {
    "code": "VERN", "state": "MARKUP_START", "confidence": 62,
    "regime": "BEARISH", "relative_strength": 0.032,
    "signals": {"done_ratio": 0.58, "rvol": 2.1, "close_in_range": 0.78,
                "broker_net_buy_streak": 4},
    "levels": _LEVELS,
}


def test_markup_renders_level_and_regime_lines():
    out = format_alert("2026-06-23", [MARKUP_V2])
    assert "📍" in out and "🎯" in out
    assert "Entry &gt;158" in out          # > di-escape (HTML safe)
    assert "R:R 2.0" in out                # R:R DIHITUNG dari levels, bukan dilabel
    assert "~hold 8d" in out
    assert "BEARISH" in out                # regime tag di header
    assert "RS +3.2%" in out               # relative strength
    assert "*" not in out                  # tetap tak ada sisa Markdown


def test_rr_shown_matches_levels_value():
    # R:R yang dirender = rr_realized di levels (anti label palsu).
    item = {**MARKUP_V2, "levels": {**_LEVELS, "rr_realized": 1.7}}
    out = format_alert("2026-06-23", [item])
    assert "R:R 1.7" in out
    assert "R:R 2.0" not in out


def test_non_markup_has_no_entry_lines():
    # DISTRIBUTION: regime/RS tampil, tapi TANPA entry/level (levels None).
    dist = {
        "code": "ATAP", "state": "DISTRIBUTION_WARNING", "confidence": 30,
        "regime": "BEARISH", "relative_strength": -0.012,
        "signals": {"done_ratio": 0.35, "rvol": 1.2, "close_in_range": 0.9,
                    "broker_net_buy_streak": 0},
        "levels": None,
    }
    out = format_alert("2026-06-23", [dist])
    assert "🎯" not in out
    assert "Entry &gt;" not in out         # baris entry absen (footer pakai kata "Entry")
    assert "📍" not in out
    assert "BEARISH" in out                # regime tetap tampil
    assert "RS -1.2%" in out


def test_footer_is_regime_aware():
    out = format_alert("2026-06-23", [MARKUP_V2])
    assert "regime-aware" in out
    assert "breakout" in out


def test_backward_compat_item_without_v2_fields():
    # Item lama (tanpa regime/levels/relative_strength) tetap dirender tanpa error.
    out = format_alert("2026-06-23", ITEMS)
    assert "MARKUP_START" in out
    assert "🎯" not in out                 # tak ada levels -> tak ada baris entry


# ---- Panduan level utk state pantau (BOB/BOW, 2026-07-10) ----
ACCUM_WATCH = {
    "code": "ASGR", "state": "ACCUMULATION_ONGOING", "confidence": 55,
    "signals": {"done_ratio": 0.28, "rvol": 7.0, "close_in_range": 0.85,
                "broker_net_buy_streak": 4},
    "levels": _LEVELS,
}


def test_watch_state_renders_bob_bow_guidance():
    out = format_signal("2026-07-09", ACCUM_WATCH)
    assert "📐" in out
    assert "BOB" in out and "BOW" in out
    assert "158.79" in out                 # level breakout = entry dari levels
    assert "138" in out                    # support = batas invalidasi
    assert "🎯" not in out                 # blok rencana MARKUP tidak ikut tampil
    assert "belum sinyal masuk" in out


def test_watch_state_without_levels_has_no_guidance():
    out = format_signal("2026-07-09", {**ACCUM_WATCH, "levels": None})
    assert "📐" not in out


def test_markup_signal_keeps_trade_plan_block():
    out = format_signal("2026-06-23", MARKUP_V2)
    assert "🎯" in out
    assert "📐" not in out


def test_compatibility_bullet_high_and_low():
    # Corr tinggi = poin plus; corr rendah SAAT ada streak akum = warning.
    hi = _why_bullets({"broker_net_buy_streak": 3, "flow_price_corr": 0.62}, None)
    assert any("searah broker" in b and "62%" in b for b in hi)
    lo = _why_bullets({"broker_net_buy_streak": 3, "flow_price_corr": 0.02}, None)
    assert any("belum mengikuti" in b and b.startswith("⚠️") for b in lo)
    # Tanpa bacaan (None) atau tanpa akumulasi -> tak ada bullet compatibility.
    none = _why_bullets({"broker_net_buy_streak": 3, "flow_price_corr": None}, None)
    assert not any("compatibility" in b for b in none)
    no_akum = _why_bullets({"broker_net_buy_streak": 0, "flow_price_corr": 0.02}, None)
    assert not any("compatibility" in b for b in no_akum)


def test_live_signal_wall_verdict_messages():
    from markup_radar.alert.telegram import format_live_signal
    eaten = format_live_signal("FUTR", wall_pulled=True, wall_verdict="EATEN")
    assert "DIMAKAN" in eaten and "timing entry" in eaten
    pulled = format_live_signal("FUTR", wall_pulled=True, wall_verdict="PULLED")
    assert "DICABUT" in pulled and "tunggu" in pulled
    # Tak terbedakan -> pesan generik lama (kompatibel mundur).
    generic = format_live_signal("FUTR", wall_pulled=True, wall_verdict=None)
    assert "ditarik atau dimakan" in generic


def test_live_bow_messages():
    from markup_radar.alert import format_live_bow
    ac = format_live_bow("ASGR", kind="AC", bow_lo=138.0, bow_hi=140.1,
                         support=138.0, time_str="10:15")
    assert "Peluang BOW" in ac and "138–140.1" in ac and "SL di bawah" in ac
    inv = format_live_bow("ASGR", kind="INVALID", bow_lo=138.0, bow_hi=140.1,
                          support=138.0)
    assert "Setup Batal" in inv and "di bawah support 138" in inv


def test_bow_zone_helper():
    from markup_radar.signals.levels import bow_zone
    assert bow_zone({"support": 138.0, "atr": 4.2}) == (138.0, 140.1)
    assert bow_zone({}) == (0.0, 0.0)
    assert bow_zone({"support": 0.0, "atr": 4.2}) == (0.0, 0.0)


def test_ownership_wording_follows_retail_pct():
    # <20% = poin plus ("cuma" boleh); >=50% = poin minus (tanpa "cuma").
    low = _why_bullets({}, {"retail_pct": 12.0})[-1]
    assert "cuma" in low and "terkunci" in low
    high = _why_bullets({}, {"retail_pct": 80.7, "retail_trend_pp": 3.7,
                             "trend_months": 6})[-1]
    assert "cuma" not in high
    assert "Mayoritas" in high and "poin minus" in high
    assert "indikasi distribusi" in high
    mid = _why_bullets({}, {"retail_pct": 35.0})[-1]
    assert "cuma" not in mid and "Mayoritas" not in mid


# --- Live signal: angka konkret + arahan tindakan (2026-09-10) --------------

def test_live_tembok_dimakan_sebut_harga_dan_lot():
    from markup_radar.alert.telegram import format_live_signal
    m = format_live_signal("SMDR", wall_pulled=True, wall_verdict="EATEN",
                           imb=1.8, accum_label="AKUM/4d", time_str="10:24",
                           wall_price=370, wall_before=12500, wall_after=3100,
                           price_now=372, entry=371.85, stop_loss=350)
    assert "DIMAKAN" in m
    assert "370" in m and "12.500" in m and "3.100" in m   # harga & lot disebut
    assert "75%" in m                                       # porsi yang hilang
    assert "INI TIMING ENTRY" in m
    assert "371,85" in m                                    # desimal dipertahankan
    assert "Stop loss 350" in m


def test_live_dimakan_tapi_harga_belum_tembus_entry():
    """Judul harus cocok isi: jangan bilang 'timing entry' lalu suruh menunggu."""
    from markup_radar.alert.telegram import format_live_signal
    m = format_live_signal("TRON", wall_pulled=True, wall_verdict="EATEN",
                           wall_price=111, wall_before=80000, wall_after=15000,
                           price_now=109, entry=111.55, stop_loss=100.13)
    assert "BELUM MASUK" in m
    assert "INI TIMING ENTRY" not in m


def test_live_tembok_dicabut_menyuruh_tunggu():
    from markup_radar.alert.telegram import format_live_signal
    m = format_live_signal("PACK", wall_pulled=True, wall_verdict="PULLED",
                           wall_price=386, wall_before=48000, wall_after=9200,
                           price_now=379, entry=387.93)
    assert "DICABUT" in m and "TUNGGU DULU" in m
    assert "INI TIMING ENTRY" not in m


def test_live_tanpa_data_tembok_tetap_jalan():
    """Backward-compat: pemanggil lama tak mengirim angka tembok."""
    from markup_radar.alert.telegram import format_live_signal
    m = format_live_signal("JECX", verdict="FAKE_OVER", imb=2.1,
                           accum_label="AKUM/5d", time_str="11:02")
    assert "JECX" in m and "Antrian beli 2.1" in m
    assert "\n\n\n" not in m          # tak ada baris kosong ganda
