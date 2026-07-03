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

# Kolom tambahan untuk evaluasi forward (2026-07-03). ALTER TABLE dijalankan
# tiap buka DB; kolom yang sudah ada dilewati (OperationalError) — DB lama
# ter-migrasi otomatis tanpa langkah manual.
_MIGRATIONS = [
    "ALTER TABLE results ADD COLUMN regime TEXT DEFAULT ''",
    "ALTER TABLE results ADD COLUMN relative_strength REAL DEFAULT 0",
    "ALTER TABLE results ADD COLUMN alert_sent INTEGER DEFAULT 0",
    "ALTER TABLE results ADD COLUMN levels TEXT",  # JSON entry/SL/TP (MARKUP_* saja)
]


class Store:
    def __init__(self, db_path: str | Path = "data/markup_radar.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self.conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # kolom sudah ada
        self.conn.commit()

    def save_result(
        self,
        date: str,
        code: str,
        state: str,
        confidence: int,
        signals: dict,
        *,
        regime: str = "",
        relative_strength: float = 0.0,
        levels: dict | None = None,
    ) -> None:
        """Upsert hasil klasifikasi satu saham untuk satu tanggal.

        `alert_sent` sengaja TIDAK di-set di sini (status kirim baru diketahui
        setelah send_telegram) — pakai `mark_alert_sent` setelah alert sukses;
        upsert ulang tidak me-reset flag yang sudah 1.
        """
        self.conn.execute(
            """
            INSERT INTO results (date, code, state, confidence, signals,
                                 regime, relative_strength, levels)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, code) DO UPDATE SET
                state=excluded.state,
                confidence=excluded.confidence,
                signals=excluded.signals,
                regime=excluded.regime,
                relative_strength=excluded.relative_strength,
                levels=excluded.levels,
                created_at=datetime('now')
            """,
            (
                date, code, state, confidence,
                json.dumps(signals, default=str),
                regime, relative_strength,
                json.dumps(levels, default=str) if levels else None,
            ),
        )
        self.conn.commit()

    def mark_alert_sent(self, date: str, codes: list[str]) -> None:
        """Tandai baris yang alert-nya BENAR-BENAR terkirim ke Telegram."""
        if not codes:
            return
        placeholders = ",".join("?" * len(codes))
        self.conn.execute(
            f"UPDATE results SET alert_sent=1 WHERE date=? AND code IN ({placeholders})",
            (date, *codes),
        )
        self.conn.commit()

    def get_results(self, date: str) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM results WHERE date = ?", (date,))
        return [dict(r) for r in cur.fetchall()]

    def all_results(self) -> list[dict]:
        """Semua baris hasil scan, urut tanggal — input evaluate_signals."""
        cur = self.conn.execute("SELECT * FROM results ORDER BY date, code")
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
