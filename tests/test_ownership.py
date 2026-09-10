"""Test parser float control & rotasi broker (ownership_client) + alert."""

from markup_radar.alert import format_alert
from markup_radar.ingest.ownership_client import (
    categorize_broker_flow,
    controlling_pct,
    fetch_broker_rotation,
    fetch_ownership,
    fmt_rp,
    parse_ksei_rows,
    summarize_ownership,
)

# Sample ala shape live /analysis/shareholder/ksei (probe 2026-07-04):
# nilai lembar berupa STRING, id = individual (ritel).
_KSEI = [
    {"code": "X", "date": "2026-03-31T00:00:00.000Z",
     "local_cp": "600", "local_id": "300", "foreign_id": "100",
     "foreign_ib": "0", "local_sc": "0"},          # total 1000, ritel 40%
    {"code": "X", "date": "2026-01-30T00:00:00.000Z",
     "local_cp": "500", "local_id": "400", "foreign_id": "100"},
    # total 1000, ritel 50% (baris lebih tua, sengaja ditaruh belakangan)
    {"code": "X", "date": "2026-02-27T00:00:00.000Z",
     "local_cp": "0", "local_id": "0"},            # total 0 -> dibuang
]

_HOLDERS = [
    {"name": "PT Pengendali", "percentage": 58.62, "badge": "{PENGENDALI}"},
    {"name": "Publik", "percentage": 41.28, "badge": "{}"},
    {"name": "Direksi A", "percentage": 0.10, "badge": "{DIREKSI,PENGENDALI}"},
]


def test_parse_ksei_rows_sorts_and_computes_pct():
    out = parse_ksei_rows(_KSEI)
    assert [r["date"] for r in out] == ["2026-01-30", "2026-03-31"]  # 0 dibuang
    assert out[0]["retail_pct"] == 50.0
    assert out[1]["retail_pct"] == 40.0
    assert out[1]["total_shares"] == 1000.0


def test_controlling_pct_sums_badged_holders():
    assert controlling_pct(_HOLDERS) == 58.72  # 58.62 + 0.10
    assert controlling_pct([]) == 0.0


def test_summarize_ownership():
    s = summarize_ownership(_KSEI, _HOLDERS)
    assert s["asof"] == "2026-03-31"
    assert s["retail_pct"] == 40.0
    assert s["controlling_pct"] == 58.72
    # ritel dari free float: 40 / (100-58.72) * 100 = 96.9
    assert abs(s["retail_float_pct"] - 96.9) < 0.1
    assert s["retail_trend_pp"] == -10.0  # 50 -> 40 (barang pindah ke kuat)
    assert s["trend_months"] == 1
    assert summarize_ownership([], _HOLDERS) is None  # tanpa KSEI -> None


def test_summarize_ownership_clamps_impossible_float_ratio():
    # Pengendali 70% -> FF 30%, ritel 40% dari total -> rasio 133% (sumber
    # beda tanggal, inkonsisten) -> disembunyikan (None), bukan ditampilkan.
    holders = [{"name": "P", "percentage": 70.0, "badge": "{PENGENDALI}"}]
    s = summarize_ownership(_KSEI, holders)
    assert s["retail_float_pct"] is None
    assert s["controlling_pct"] == 70.0


def test_summarize_ownership_without_holders():
    s = summarize_ownership(_KSEI, [])
    assert s["retail_float_pct"] is None  # komposisi kosong -> tak dihitung
    assert s["controlling_pct"] == 0.0


_CATS = {"retail": ["YP", "PD"], "foreign": ["AK"], "smart": ["AZ"]}


def test_categorize_broker_flow():
    records = [
        {"broker": "YP", "net_value": -1_000_000_000.0},
        {"broker": "pd", "net_value": -500_000_000.0},   # case-insensitive
        {"broker": "AK", "net_value": 1_200_000_000.0},
        {"broker": "AZ", "net_value": 400_000_000.0},
        {"broker": "ZZ", "net_value": -100_000_000.0},   # di luar mapping
    ]
    f = categorize_broker_flow(records, _CATS)
    assert f["retail_net"] == -1_500_000_000.0
    assert f["foreign_net"] == 1_200_000_000.0
    assert f["smart_net"] == 400_000_000.0
    assert f["other_net"] == -100_000_000.0
    assert f["n_brokers"] == 5


def test_fmt_rp():
    assert fmt_rp(1_200_000_000) == "+1.2M"
    assert fmt_rp(-1_500_000_000) == "-1.5M"
    assert fmt_rp(2_300_000_000_000) == "+2.3T"
    assert fmt_rp(-45_000_000) == "-45jt"
    assert fmt_rp(500) == "+500"


class _Boom:
    def shareholder_ksei(self, *a, **kw):
        raise RuntimeError("down")

    def broker_summary_stock(self, *a, **kw):
        raise RuntimeError("down")


def test_fetch_fail_soft():
    assert fetch_ownership(_Boom(), "WINR") is None
    assert fetch_broker_rotation(_Boom(), "WINR", "2026-07-04", _CATS) is None


def test_format_alert_renders_ownership_and_rotation():
    items = [{
        "code": "WINR", "state": "MARKUP_START", "confidence": 70,
        "signals": {"done_ratio": 0.6, "rvol": 2.1, "close_in_range": 0.8,
                    "broker_net_buy_streak": 3},
        "ownership": {"asof": "2026-06-30", "retail_pct": 12.5,
                      "retail_float_pct": 30.0, "controlling_pct": 58.7,
                      "retail_trend_pp": -2.1, "trend_months": 5},
        "rotation": {"retail_net": -2_100_000_000.0,
                     "foreign_net": 1_800_000_000.0,
                     "smart_net": 400_000_000.0, "other_net": 0.0,
                     "n_brokers": 40},
    }]
    msg = format_alert("2026-07-04", items)
    assert "🏦 Ritel 12.5% (30% FF) · pengendali 58.7% · ▼2.1pp/5bln" in msg
    assert "🔄 Ritel -2.1M · Asing +1.8M · Smart +400jt" in msg
