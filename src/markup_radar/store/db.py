"""SQLite store untuk hasil harian (audit & backtesting).

Idempoten: tulis ulang dengan (code, date) yang sama akan meng-upsert.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    date        TEXT NOT NULL,
    code        TEXT NOT NULL,
    state       TEXT NOT NULL,
    confidence  INTEGER,
    signals     TEXT,            -- JSON breakdown S1..S9
    created_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_results_date ON results(date);
CREATE INDEX IF NOT EXISTS idx_results_state ON results(state);
"""


class Store:
    def __init__(self, db_path: str | Path = "data/markup_radar.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def save_result(
        self, date: str, code: str, state: str, confidence: int, signals: dict
    ) -> None:
        """Upsert hasil klasifikasi satu saham untuk satu tanggal."""
        self.conn.execute(
            """
            INSERT INTO results (date, code, state, confidence, signals)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date, code) DO UPDATE SET
                state=excluded.state,
                confidence=excluded.confidence,
                signals=excluded.signals,
                created_at=datetime('now')
            """,
            (date, code, state, confidence, json.dumps(signals, default=str)),
        )
        self.conn.commit()

    def get_results(self, date: str) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM results WHERE date = ?", (date,))
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
