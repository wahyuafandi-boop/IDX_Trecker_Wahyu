"""Unit test mirror Google Sheets — tanpa jaringan (worksheet & cfg di-stub)."""

import json
from types import SimpleNamespace

from markup_radar.store.sheets import (
    _HEADER,
    SheetsSink,
    _result_row,
    build_sink,
    load_service_account_info,
)


class _FakeWorksheet:
    """Tangkap panggilan append_rows untuk assertion."""

    def __init__(self):
        self.appended = []

    def append_rows(self, rows, value_input_option=None):
        self.appended.extend(rows)


def _cfg(**sheets):
    return SimpleNamespace(sheets=sheets)


def _result(**over):
    r = {
        "date": "2026-06-21",
        "code": "BBRI",
        "state": "MARKUP_CONFIRMED",
        "confidence": 90,
        "narrative": "kondisi kuat",
        "signals": {
            "done_ratio": 0.6789,
            "rvol": 2.34,
            "close_in_range": 0.9,
            "broker_net_buy_streak": 3,
            "queue_imbalance": 1.25,
            "ihsg_above_ma50": True,
        },
    }
    r.update(over)
    return r


def test_result_row_matches_header_order():
    row = _result_row(_result(), run_ts="2026-06-21T19:00:00")
    assert len(row) == len(_HEADER)
    assert row[:5] == ["2026-06-21T19:00:00", "2026-06-21", "BBRI", "MARKUP_CONFIRMED", 90]
    # done_ratio dibulatkan 4 desimal; ihsg jadi bool; narrative di kolom akhir.
    assert row[_HEADER.index("done_ratio")] == 0.6789
    assert row[_HEADER.index("ihsg_above_ma50")] is True
    assert row[-1] == "kondisi kuat"


def test_result_row_defaults_for_missing_signals():
    row = _result_row({"date": "d", "code": "X", "state": "NEUTRAL", "confidence": 0},
                      run_ts="t")
    assert row[_HEADER.index("queue_imbalance")] == 0.0
    assert row[_HEADER.index("broker_net_buy_streak")] == 0
    assert row[-1] == ""  # narrative kosong


def test_append_results_writes_all_rows_and_counts():
    ws = _FakeWorksheet()
    sink = SheetsSink(ws)
    n = sink.append_results([_result(), _result(code="BMRI", state="NEUTRAL")],
                            run_ts="t")
    assert n == 2
    assert len(ws.appended) == 2
    assert ws.appended[0][2] == "BBRI"
    assert ws.appended[1][2] == "BMRI"


def test_append_results_empty_is_noop():
    ws = _FakeWorksheet()
    assert SheetsSink(ws).append_results([]) == 0
    assert ws.appended == []


def test_build_sink_disabled_returns_none():
    assert build_sink(_cfg(enabled=False, spreadsheet_id="abc")) is None


def test_build_sink_no_spreadsheet_id_returns_none(monkeypatch):
    monkeypatch.delenv("MARKUP_RADAR_SHEET_ID", raising=False)
    assert build_sink(_cfg(enabled=True, spreadsheet_id="")) is None


def test_build_sink_no_credentials_returns_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    # enabled + ada spreadsheet_id + gspread terpasang, tapi kredensial absen.
    assert build_sink(_cfg(enabled=True, spreadsheet_id="sheet123")) is None


def test_load_service_account_info_from_env_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps({"type": "service_account"}))
    info = load_service_account_info()
    assert info == {"type": "service_account"}


def test_load_service_account_info_invalid_json_returns_none(monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{not-json")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert load_service_account_info() is None
