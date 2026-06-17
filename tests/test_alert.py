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
