"""Test parser insider & calendar (insider_client) + rendering alert."""

from markup_radar.alert import format_alert
from markup_radar.ingest.insider_client import (
    _iso_date,
    fetch_insider_map,
    parse_calendar_items,
    summarize_insider_rows,
)

# Sample sesuai shape live /analysis/shareholder-insider (probe 2026-07-04).
_INSIDER_ROWS = [
    {
        "date": "2026-07-03T16:24:01.000Z",
        "code": "WINR",
        "name": "PEMENANG NUSANTARA INTERNASIONAL",
        "prev_percent": 56.64,
        "next_percent": 56.48,
        "change": -0.16,
        "purpose": "Divestasi",
        "subrow": [{"date": "2026-06-24T00:00:00.000Z", "price": 20,
                    "status": "Sell", "value": -7618600}],
    },
    {
        "date": "2026-06-20T10:00:00.000Z",
        "code": "WINR",
        "name": "X",
        "change": 0.30,
        "purpose": "Investasi",
    },
    {
        "date": "2026-07-03T10:26:01.000Z",
        "code": "DGWG",
        "name": "AGRO JAYA MANDIRI",
        "change": 4.8,
        "purpose": "Pembayaran Sebagian Fasilitas Repo",
    },
]


def test_iso_date_variants():
    assert _iso_date("2026-07-03T16:24:01.000Z") == "2026-07-03"
    assert _iso_date("2026-08-11T00:00:00") == "2026-08-11"
    assert _iso_date("20260625") == "2026-06-25"  # DateStr PUBLIC_EXPOSE
    assert _iso_date(None) == ""
    assert _iso_date("bukan tanggal") == ""


def test_summarize_insider_rows_per_code():
    out = summarize_insider_rows(_INSIDER_ROWS, {"WINR"})
    assert set(out) == {"WINR"}  # DGWG di luar watchlist -> dibuang
    w = out["WINR"]
    assert w["n_reports"] == 2
    assert w["net_change_pct"] == 0.14  # -0.16 + 0.30
    assert w["buys"] == 1 and w["sells"] == 1
    # last_* = laporan terbaru (2026-07-03), bukan urutan list.
    assert w["last_date"] == "2026-07-03"
    assert w["last_purpose"] == "Divestasi"
    assert w["last_change_pct"] == -0.16


def test_summarize_insider_rows_all_codes_when_none():
    out = summarize_insider_rows(_INSIDER_ROWS, None)
    assert set(out) == {"WINR", "DGWG"}


class _PagedClient:
    """Fake: shareholder_insider dengan 2 halaman (limit tercapai di hal 1)."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def shareholder_insider(self, date_from, date_to, *, page, limit, **kw):
        self.asked.append(page)
        return self.pages.get(page, [])


def test_fetch_insider_map_pages_until_short_batch():
    rows_p1 = [dict(r, code=f"AA{i:02d}") for i, r in
               enumerate(_INSIDER_ROWS * 34)][:100]  # tepat `limit` -> lanjut
    c = _PagedClient({1: rows_p1, 2: _INSIDER_ROWS})
    out = fetch_insider_map(c, "2026-06-04", "2026-07-04", {"WINR"}, limit=100)
    assert c.asked == [1, 2]  # hal 2 pendek -> berhenti (tak minta hal 3)
    assert "WINR" in out


def test_fetch_insider_map_fail_soft():
    class _Boom:
        def shareholder_insider(self, *a, **kw):
            raise RuntimeError("API down")

    assert fetch_insider_map(_Boom(), "a", "b", {"WINR"}) == {}


# Sample sesuai shape live /analysis/calendar (probe 2026-07-04).
_CAL_ITEMS = [
    {"code": "SDRA", "type": "RUPS_SCHEDULE",
     "payload": {"Date": "2026-07-15T00:00:00", "Remark": "EGM",
                 "RecDate": "2026-07-01T00:00:00"}},
    {"code": "WINR", "type": "PUBLIC_EXPOSE",
     "payload": {"DateStr": "20260710", "TimeStr": "11:00"}},
    {"code": "WINR", "type": "RUPS_SCHEDULE",  # lampau -> dibuang
     "payload": {"Date": "2026-06-25T00:00:00", "Remark": "AGM"}},
    {"code": "PJHB", "type": "CONVERTION",     # di luar horizon -> dibuang
     "payload": {"TradeDate": "2026-11-02T00:00:00", "Remark": "-"}},
    {"code": "XXXX", "type": "APA_INI",        # type asing -> label Title Case
     "payload": {"Date": "2026-07-20T00:00:00"}},
]


def test_parse_calendar_items_window_and_sort():
    out = parse_calendar_items(_CAL_ITEMS, "2026-07-04", horizon_days=21)
    assert [c["date"] for c in out] == ["2026-07-10", "2026-07-15", "2026-07-20"]
    labels = [c["label"] for c in out]
    assert labels[0] == "PubEx"
    assert labels[1] == "RUPS EGM"          # Remark ikut label
    assert labels[2] == "Apa Ini"           # fallback Title Case
    # Remark '-' tidak ditempel; event lampau & di luar horizon terbuang.
    assert all(c["type"] != "CONVERTION" for c in out)


def test_parse_calendar_items_bad_inputs():
    assert parse_calendar_items([], "2026-07-04") == []
    assert parse_calendar_items(_CAL_ITEMS, "tanggal-rusak") == []
    assert parse_calendar_items([{"type": "X", "payload": {}}], "2026-07-04") == []


def test_format_alert_renders_insider_and_corp_actions():
    items = [{
        "code": "WINR", "state": "MARKUP_START", "confidence": 70,
        "signals": {"done_ratio": 0.6, "rvol": 2.1, "close_in_range": 0.8,
                    "broker_net_buy_streak": 3},
        "insider": {"net_change_pct": -0.16, "n_reports": 1, "buys": 0,
                    "sells": 1, "last_date": "2026-07-03",
                    "last_purpose": "Divestasi", "last_change_pct": -0.16,
                    "window_days": 30},
        "corp_actions": [{"type": "RUPS_SCHEDULE", "label": "RUPS EGM",
                          "date": "2026-07-15", "remark": "EGM"}],
    }]
    msg = format_alert("2026-07-04", items)
    assert "👤 Insider 30d: 🔴 -0.16 pp (1 laporan, terakhir Divestasi 2026-07-03)" in msg
    assert "📅 RUPS EGM 2026-07-15" in msg


def test_format_alert_without_enrichment_unchanged():
    items = [{
        "code": "BBCA", "state": "MARKUP_START", "confidence": 60,
        "signals": {"done_ratio": 0.6, "rvol": 2.0, "close_in_range": 0.7,
                    "broker_net_buy_streak": 2},
    }]
    msg = format_alert("2026-07-04", items)
    assert "👤" not in msg and "📅" not in msg
