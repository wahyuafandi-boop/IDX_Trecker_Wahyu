"""Test format_alert: HTML mode aman untuk state ber-underscore."""

from markup_radar.alert import format_alert

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
