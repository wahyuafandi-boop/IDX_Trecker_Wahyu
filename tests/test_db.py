"""Test Store SQLite: migrasi kolom evaluasi + alert_sent + upsert levels."""

import json
import sqlite3

from markup_radar.store.db import Store

_SIG = {"done_ratio": 0.5, "rvol": 2.0}
_LV = {"entry": 105.0, "stop_loss": 95.0, "take_profit": 125.0, "rr_realized": 2.0}


def test_save_and_read_new_columns(tmp_path):
    st = Store(tmp_path / "t.db")
    st.save_result("2026-07-01", "BBRI", "MARKUP_START", 70, _SIG,
                   regime="BULLISH", relative_strength=0.021, levels=_LV)
    (row,) = st.all_results()
    assert row["regime"] == "BULLISH"
    assert row["relative_strength"] == 0.021
    assert row["alert_sent"] == 0
    assert json.loads(row["levels"])["entry"] == 105.0
    st.close()


def test_mark_alert_sent_only_targets_given_codes(tmp_path):
    st = Store(tmp_path / "t.db")
    st.save_result("2026-07-01", "BBRI", "MARKUP_START", 70, _SIG, levels=_LV)
    st.save_result("2026-07-01", "TLKM", "NEUTRAL", 10, _SIG)
    st.mark_alert_sent("2026-07-01", ["BBRI"])
    rows = {r["code"]: r for r in st.all_results()}
    assert rows["BBRI"]["alert_sent"] == 1
    assert rows["TLKM"]["alert_sent"] == 0
    st.close()


def test_upsert_preserves_alert_sent_flag(tmp_path):
    # Re-run tanggal sama (upsert) tidak boleh me-reset alert_sent yang sudah 1.
    st = Store(tmp_path / "t.db")
    st.save_result("2026-07-01", "BBRI", "MARKUP_START", 70, _SIG)
    st.mark_alert_sent("2026-07-01", ["BBRI"])
    st.save_result("2026-07-01", "BBRI", "MARKUP_CONFIRMED", 90, _SIG,
                   regime="BULLISH")
    (row,) = st.all_results()
    assert row["state"] == "MARKUP_CONFIRMED"   # data ter-update
    assert row["alert_sent"] == 1               # flag tak hilang
    st.close()


def test_migration_on_legacy_db(tmp_path):
    # DB skema lama (tanpa kolom evaluasi) harus terbuka & ter-migrasi mulus.
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE results (
            date TEXT NOT NULL, code TEXT NOT NULL, state TEXT NOT NULL,
            confidence INTEGER, signals TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, code));
    """)
    conn.execute("INSERT INTO results (date, code, state, confidence, signals) "
                 "VALUES ('2026-06-18', 'JSMR', 'ACCUMULATION_ONGOING', 63, '{}')")
    conn.commit()
    conn.close()

    st = Store(path)
    (row,) = st.all_results()
    assert row["regime"] == ""          # default migrasi
    assert row["alert_sent"] == 0
    assert row["levels"] is None
    # Buka kedua kali: migrasi idempoten (kolom sudah ada -> dilewati).
    st.close()
    Store(path).close()
